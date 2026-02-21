#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pipeline.py — Motor de Pipeline (Metadados, API e Laudo)
--------------------------------------------------------

Lida com funções de integração que acontecem depois (ou durante) do download de exames.
"""

import sys
import json
import shutil
import unicodedata
import subprocess
from pathlib import Path

import requests

try:
    import pydicom
except ImportError:
    pydicom = None

import config
from logger import log_info, log_ok, log_erro, log_debug, log_skip, log_aviso
import img_conversor


def pipeline_ativo_no_modo_atual() -> bool:
    """
    Pipeline pode rodar em:
    - storage_mode = pipeline
    - storage_mode = transient + PIPELINE_ON_TRANSIENT=true
    """
    return (config.STORAGE_MODE == "pipeline") or (
        config.STORAGE_MODE == "transient" and getattr(config, "PIPELINE_ON_TRANSIENT", False)
    )

def salvar_metadata_dicom(dcm_path: Path, output_json: Path):
    """
    Lê um arquivo DICOM e exporta seus metadados principais para JSON.
    """
    if not pydicom:
        log_debug("pydicom não disponível para exportar metadados.")
        return False
    try:
        ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
        meta = {}
        for elem in ds:
            if elem.VR in ["OB", "OW", "SQ"]: continue
            meta[elem.name] = str(elem.value)
        output_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        log_debug(f"Erro ao exportar metadados DICOM: {e}")
        return False

def enviar_para_pipeline_api(an: str, servidor: str, destino_base: Path, js: dict) -> tuple[bool, bool]:
    """
    Envia payload simplificado para a API de pipeline, quando configurada.
    """
    if not getattr(config, "PIPELINE_ENABLED", True):
        log_info(f"[PIPELINE] AN {an}: envio desativado em config ([PIPELINE] enabled=false).")
        return True, False

    api_url = getattr(config, "PIPELINE_API_URL", "")
    if not api_url:
        log_aviso(f"[PIPELINE] AN {an}: api_url não configurada. Envio ignorado.")
        return True, False

    cockpit_meta = destino_base / "metadata_cockpit.json"
    if not cockpit_meta.exists():
        log_aviso(f"[PIPELINE] AN {an}: metadata_cockpit.json ausente. Envio ignorado.")
        return True, False

    try:
        cockpit = json.loads(cockpit_meta.read_text(encoding="utf-8"))
    except Exception as e:
        log_erro(f"[PIPELINE] AN {an}: falha lendo metadata_cockpit.json: {e}")
        return (not getattr(config, "PIPELINE_STRICT", False), False)

    exame = str(cockpit.get("exame", "") or "")
    exame_norm = unicodedata.normalize("NFKD", exame).encode("ascii", "ignore").decode("ascii").upper()
    include_terms = [t.strip().upper() for t in getattr(config, "PIPELINE_INCLUDE_TERMS", ["TORAX"]) if str(t).strip()]
    exclude_terms = [t.strip().upper() for t in getattr(config, "PIPELINE_EXCLUDE_TERMS", ["PERFIL"]) if str(t).strip()]

    if include_terms and not all(term in exame_norm for term in include_terms):
        log_skip(f"[PIPELINE] AN {an}: exame '{exame}' fora do critério de inclusão ({', '.join(include_terms)}).")
        return True, False
    if exclude_terms and any(term in exame_norm for term in exclude_terms):
        log_skip(f"[PIPELINE] AN {an}: exame '{exame}' bloqueado por exclusão ({', '.join(exclude_terms)}).")
        return True, False

    request_format = getattr(config, "PIPELINE_REQUEST_FORMAT", "json")
    headers = {}
    token = getattr(config, "PIPELINE_API_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = max(1, int(getattr(config, "PIPELINE_TIMEOUT", 30)))
    response_trace_file = destino_base / "pipeline_response.json"

    def _safe_json_or_text(resp: requests.Response):
        try: return resp.json()
        except Exception: return resp.text

    def _select_pipeline_dcm(dicom_files: list[Path]) -> Path:
        enriched = []
        for p in dicom_files:
            series_uid = ""
            series_num = 0
            instance_num = 0
            try:
                ds = pydicom.dcmread(str(p), stop_before_pixels=True)
                series_uid = str(getattr(ds, "SeriesInstanceUID", "") or "")
                series_num_raw = str(getattr(ds, "SeriesNumber", "") or "").strip()
                instance_num_raw = str(getattr(ds, "InstanceNumber", "") or "").strip()
                if series_num_raw.isdigit(): series_num = int(series_num_raw)
                if instance_num_raw.isdigit(): instance_num = int(instance_num_raw)
            except Exception: pass
            enriched.append((p, series_uid, series_num, instance_num))

        enriched.sort(key=lambda t: (t[2], t[1], t[3], t[0].name))
        first_idx_by_series = {}
        for idx, item in enumerate(enriched):
            uid = item[1] or f"__series_{idx}"
            if uid not in first_idx_by_series:
                first_idx_by_series[uid] = idx

        unique_series = list(first_idx_by_series.items())
        if len(unique_series) >= 2:
            second_series_idx = unique_series[1][1]
            return enriched[second_series_idx][0]
        if len(enriched) >= 2:
            return enriched[1][0]
        return enriched[0][0]

    if request_format == "multipart_single_file":
        dicom_files = sorted(destino_base.glob("*.dcm"))
        if not dicom_files:
            log_erro(f"[PIPELINE] AN {an}: nenhum DICOM encontrado para envio multipart.")
            return (not getattr(config, "PIPELINE_STRICT", False), False)

        dcm_file = _select_pipeline_dcm(dicom_files)
        if len(dicom_files) > 1:
            log_info(f"[PIPELINE] AN {an}: arquivo selecionado para envio: {dcm_file.name}")
        age_value = ""
        try:
            ds = pydicom.dcmread(str(dcm_file), stop_before_pixels=True)
            raw_age = str(getattr(ds, "PatientAge", "") or "").strip()
            if raw_age and len(raw_age) >= 4 and raw_age[:3].isdigit():
                age_num = int(raw_age[:3])
                age_unit = raw_age[3].upper()
                if age_unit == "Y": age_value = f"{age_num} year old"
                elif age_unit == "M": age_value = f"{age_num} month old"
                elif age_unit == "W": age_value = f"{age_num} week old"
                elif age_unit == "D": age_value = f"{age_num} day old"
        except Exception as e:
            log_debug(f"[PIPELINE] AN {an}: não foi possível extrair PatientAge: {e}")
        if not age_value:
            log_aviso(f"[PIPELINE] AN {an}: PatientAge ausente/indefinido no DICOM. Envio ignorado.")
            return True, False

        form_data = {
            "age": age_value,
            "identificador": str(an),
        }
        form_data = {k: v for k, v in form_data.items() if v}

        try:
            with open(dcm_file, "rb") as fp:
                files = {"file": (dcm_file.name, fp, "application/dicom")}
                resp = requests.post(api_url, data=form_data, files=files, headers=headers, timeout=(5, timeout))
                trace = {
                    "request": {
                        "url": api_url,
                        "format": request_format,
                        "an": an,
                        "servidor": servidor,
                        "exame": exame,
                        "file_name": dcm_file.name,
                        "fields": form_data,
                    },
                    "response": {"status_code": resp.status_code, "body": _safe_json_or_text(resp)},
                }
                response_trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
                resp.raise_for_status()
            log_ok(f"[PIPELINE] AN {an}: multipart enviado para API ({dcm_file.name}).")
            return True, True
        except Exception as e:
            log_erro(f"[PIPELINE] AN {an}: falha no envio multipart: {e}")
            return (not getattr(config, "PIPELINE_STRICT", False), False)

    if request_format == "multipart_optimized_image":
        dicom_files = sorted(destino_base.glob("*.dcm"))
        if not dicom_files:
            log_erro(f"[PIPELINE] AN {an}: nenhum DICOM encontrado para otimização.")
            return (not getattr(config, "PIPELINE_STRICT", False), False)

        dcm_file = _select_pipeline_dcm(dicom_files)
        log_info(f"[PIPELINE] AN {an}: otimizando imagem para envio: {dcm_file.name}")

        try:
            jpeg_bytes, mime_type = img_conversor.otimizar_imagem_para_api(str(dcm_file), limite_mb=4.0)
            
            age_value = ""
            try:
                ds = pydicom.dcmread(str(dcm_file), stop_before_pixels=True)
                raw_age = str(getattr(ds, "PatientAge", "") or "").strip()
                if raw_age and len(raw_age) >= 4 and raw_age[:3].isdigit():
                    age_num = int(raw_age[:3])
                    age_unit = raw_age[3].upper()
                    mapping = {"Y": "year old", "M": "month old", "W": "week old", "D": "day old"}
                    if age_unit in mapping:
                        age_value = f"{age_num} {mapping[age_unit]}"
            except Exception: pass

            form_data = {
                "age": age_value,
                "identificador": str(an),
                "original_filename": dcm_file.name
            }
            form_data = {k: v for k, v in form_data.items() if v}

            files = {"file": (f"{an}.jpg", jpeg_bytes, "image/jpeg")}
            resp = requests.post(api_url, data=form_data, files=files, headers=headers, timeout=(5, timeout))
            
            trace = {
                "request": {
                    "url": api_url,
                    "format": request_format,
                    "an": an,
                    "file_name": f"{an}.jpg",
                    "fields": form_data,
                },
                "response": {"status_code": resp.status_code, "body": _safe_json_or_text(resp)},
            }
            response_trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
            resp.raise_for_status()
            
            log_ok(f"[PIPELINE] AN {an}: imagem otimizada enviada para API ({len(jpeg_bytes)/1024:.1f} KB).")
            return True, True
            
        except Exception as e:
            log_erro(f"[PIPELINE] AN {an}: falha na otimização/envio da imagem: {e}")
            return (not getattr(config, "PIPELINE_STRICT", False), False)

    dicom_meta_files = sorted(destino_base.glob("metadado_*_dicom.json"))
    payload = {
        "an": an,
        "servidor": servidor,
        "study_uid": js.get("study_uid", ""),
        "patient_name": js.get("patient_name", ""),
        "study_desc": js.get("study_desc", ""),
        "modality": js.get("modality", ""),
        "model": getattr(config, "PIPELINE_MODEL", ""),
        "dicom_dir": str(destino_base),
        "metadata_cockpit_path": str(cockpit_meta) if cockpit_meta.exists() else "",
        "dicom_metadata_paths": [str(p) for p in dicom_meta_files],
        "storage_mode": config.STORAGE_MODE,
    }

    try:
        headers_json = {"Content-Type": "application/json", **headers}
        resp = requests.post(api_url, json=payload, headers=headers_json, timeout=(5, timeout))
        trace = {
            "request": {
                "url": api_url,
                "format": request_format,
                "an": an,
                "servidor": servidor,
                "exame": exame,
                "payload": payload,
            },
            "response": {"status_code": resp.status_code, "body": _safe_json_or_text(resp)},
        }
        response_trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        resp.raise_for_status()
        log_ok(f"[PIPELINE] AN {an}: payload JSON enviado para API.")
        return True, True
    except Exception as e:
        log_erro(f"[PIPELINE] AN {an}: falha no envio JSON para API: {e}")
        return (not getattr(config, "PIPELINE_STRICT", False), False)


def gravar_laudo_do_pipeline(an: str, destino_base: Path) -> bool:
    """
    Monta payload RTF a partir do pipeline_response.json e grava laudo no Cockpit.
    """
    if not getattr(config, "PIPELINE_AUTO_WRITE_REPORT", True):
        log_info(f"[PIPELINE] AN {an}: gravação automática de laudo desativada.")
        return True

    cockpit_meta_file = destino_base / "metadata_cockpit.json"
    if not cockpit_meta_file.exists():
        log_aviso(f"[PIPELINE] AN {an}: metadata_cockpit.json ausente. Laudo não foi gravado.")
        return not getattr(config, "PIPELINE_STRICT", False)

    pipeline_resp_file = destino_base / "pipeline_response.json"
    if not pipeline_resp_file.exists():
        log_aviso(f"[PIPELINE] AN {an}: pipeline_response.json ausente. Laudo não foi gravado.")
        return not getattr(config, "PIPELINE_STRICT", False)

    try:
        cockpit = json.loads(cockpit_meta_file.read_text(encoding="utf-8"))
    except Exception as e:
        log_erro(f"[PIPELINE] AN {an}: erro lendo metadata_cockpit.json: {e}")
        return not getattr(config, "PIPELINE_STRICT", False)

    id_laudo = cockpit.get("id_exame_pedido")
    if not id_laudo:
        log_erro(f"[PIPELINE] AN {an}: id_exame_pedido ausente no metadata_cockpit.")
        return not getattr(config, "PIPELINE_STRICT", False)

    medico_id = getattr(config, "PIPELINE_DEFAULT_MEDICO_ID", None)
    if not medico_id:
        log_erro(f"[PIPELINE] AN {an}: defina PIPELINE.default_medico_id ou MEDICO_EXECUTANTE_ID.")
        return not getattr(config, "PIPELINE_STRICT", False)

    title = getattr(config, "PIPELINE_REPORT_TITLE", "RADIOGRAFIA DE TÓRAX NO LEITO")
    payload_path = destino_base / "laudo_payload.json"

    montar_cmd = [
        sys.executable,
        str(config.BASE_DIR / "montar_laudo_rtf.py"),
        "--id-laudo", str(id_laudo),
        "--medico-id", str(medico_id),
        "--title", title,
        "--pipeline-response", str(pipeline_resp_file),
        "--payload-path", str(payload_path),
        "--pendente",
    ]
    try:
        montar_proc = subprocess.run(montar_cmd, capture_output=True, text=True, check=False)
    except Exception as e:
        log_erro(f"[PIPELINE] AN {an}: falha ao executar montar_laudo_rtf.py: {e}")
        return not getattr(config, "PIPELINE_STRICT", False)

    if montar_proc.returncode != 0:
        erro = (montar_proc.stderr or montar_proc.stdout or "").strip()
        log_erro(f"[PIPELINE] AN {an}: montar_laudo_rtf.py falhou: {erro}")
        return not getattr(config, "PIPELINE_STRICT", False)

    gravar_cmd = [
        sys.executable,
        str(config.BASE_DIR / "gravar_laudo.py"),
        str(id_laudo),
        "--payload-file", str(payload_path),
    ]

    try:
        gravar_proc = subprocess.run(gravar_cmd, capture_output=True, text=True, check=False)
    except Exception as e:
        log_erro(f"[PIPELINE] AN {an}: falha ao executar gravar_laudo.py: {e}")
        return not getattr(config, "PIPELINE_STRICT", False)

    if gravar_proc.returncode != 0:
        erro = (gravar_proc.stderr or gravar_proc.stdout or "").strip()
        log_erro(f"[PIPELINE] AN {an}: gravar_laudo.py falhou: {erro}")
        return not getattr(config, "PIPELINE_STRICT", False)

    log_ok(f"[PIPELINE] AN {an}: laudo gravado com sucesso (id_exame_pedido={id_laudo}).")
    return True


def processar_exame(an: str, servidor: str, destino_base: Path, js: dict) -> tuple[bool, str]:
    """
    Ponto de entrada do pipeline para um exame específico.
    Realiza a importação/geração dos metadados, envia via API e grava laudo.
    """
    meta_origem = config.COCKPIT_METADATA_DIR / f"{an}.json"
    if meta_origem.exists():
        try:
            if config.STORAGE_MODE in ["persistent", "pipeline"]:
                shutil.move(str(meta_origem), str(destino_base / "metadata_cockpit.json"))
            elif config.STORAGE_MODE == "transient" and pipeline_ativo_no_modo_atual():
                shutil.copy2(str(meta_origem), str(destino_base / "metadata_cockpit.json"))
        except Exception as e:
            log_debug(f"Erro ao mover metadados cockpit para pasta final: {e}")

    if getattr(config, 'SAVE_METADATA', False) and pydicom:
        series_processadas = set()
        for dcm_file in destino_base.glob("*.dcm"):
            try:
                ds = pydicom.dcmread(str(dcm_file), stop_before_pixels=True)
                series_uid = str(getattr(ds, "SeriesInstanceUID", ""))
                
                if series_uid in series_processadas:
                    continue
                series_processadas.add(series_uid)
                
                series_number = str(getattr(ds, "SeriesNumber", "unknown"))
                
                output_name = f"metadado_{series_number}_dicom.json"
                salvar_metadata_dicom(dcm_file, destino_base / output_name)
                
            except Exception as e:
                log_debug(f"Erro ao processar metadados de série: {e}")

    novo_status = js.get("status", "completo")
    sucesso = True

    if pipeline_ativo_no_modo_atual():
        entrega_ok, has_response = enviar_para_pipeline_api(an, servidor, destino_base, js)
        if not entrega_ok:
            novo_status = "pipeline_erro"
            sucesso = False
            return sucesso, novo_status
            
        if has_response:
            laudo_ok = gravar_laudo_do_pipeline(an, destino_base)
            if not laudo_ok:
                novo_status = "pipeline_laudo_erro"
                sucesso = False
        else:
            log_info(f"[PIPELINE] AN {an}: gravação de laudo pulada (sem resposta de API).")

    return sucesso, novo_status

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Execução direta do Pipeline (Metadados, API e Laudo)")
    parser.add_argument("servidor", help="Servidor (ex: HAC ou HBR)")
    parser.add_argument("an", help="Accession Number (AN)")
    parser.add_argument("--dir", help="Diretório base do DICOM (opcional). Se omitido, usa os diretórios padrão do config.")
    
    args = parser.parse_args()
    
    an = args.an
    servidor = args.servidor.upper()
    
    if args.dir:
        destino_base = Path(args.dir)
    else:
        if config.STORAGE_MODE == "transient":
            destino_base = config.TMP_DIR / an
        else:
            destino_base = config.OUTPUT_DICOM_DIR / an

    if not destino_base.exists() or not destino_base.is_dir():
        log_erro(f"Diretório não encontrado: {destino_base}")
        sys.exit(1)

    # Tenta ler o JSON de progresso existente para manter o contexto
    js = {}
    json_file = config.PROGRESS_DIR / f"{an}.json"
    if json_file.exists():
        try:
            js = json.loads(json_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    log_info(f"=== Iniciando Pipeline Standalone para AN: {an} ({servidor}) ===")
    log_info(f"Diretório base: {destino_base}")
    
    pipe_ok, final_status = processar_exame(an, servidor, destino_base, js)
    
    if final_status != "completo":
        js["status"] = final_status
        # Atualiza o JSON de progresso se ele existir, para refletir o status de erro pipeline localmente
        try:
            json_file.write_text(json.dumps(js, ensure_ascii=False, indent=2), encoding="utf-8")
        except:
            pass
            
    if pipe_ok:
        log_ok(f"Pipeline finalizado com sucesso. Status final: {final_status}")
    else:
        log_erro(f"Pipeline finalizado com erros. Status final: {final_status}")
        sys.exit(1)

if __name__ == "__main__":
    main()
