#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
downloader.py — Motor de Download WADO/DICOM
---------------------------------------------

Este módulo gerencia o download de exames DICOM via protocolo WADO.

FUNCIONALIDADES PRINCIPAIS:
1. Download paralelo de imagens DICOM usando ThreadPoolExecutor
2. Retry automático com fallback entre servidores (HAC → HBR)
3. Rastreamento de progresso via arquivos JSON
4. Suporte a modos de armazenamento:
   - Persistent: Mantém arquivos em disco (RadiAnt/Windows)
   - Transient: Move para OsiriX Incoming e remove temporários (OsiriX/macOS)
   - Pipeline: Mantém arquivos, força metadados e permite envio para API externa

MODOS DE USO:

1. Download único:
   python downloader.py HAC 12345678

2. Batch com servidor específico (lê ANs do clipboard):
   python downloader.py HAC

3. Batch com auto-detect (tenta HAC → HBR):
   python downloader.py

FUNÇÕES PRINCIPAIS:
- baixar_an(servidor, an, mostrar_progresso): Baixa um exame completo
- _baixar_sop(url, destino, extract_metadata): Baixa uma única imagem DICOM
- _ler_clipboard(): Lê lista de ANs da área de transferência

ARQUIVOS JSON DE PROGRESSO:
Cada exame gera um arquivo JSON em progresso/ com:
- an: Accession Number
- servidor: HBR ou HAC
- status: ativo, baixando, completo
- total: Número total de imagens
- baixadas: Número de imagens já baixadas
- velocidade: Taxa de download (img/s)
- historico: Lista de SOPInstanceUIDs já baixados
- patient_name, study_desc, modality: Metadados do paciente
"""

import os
import sys
import shutil
import json
import time
import argparse
import subprocess
import platform
import unicodedata
import sys
from pathlib import Path
from time import perf_counter
from concurrent.futures import ThreadPoolExecutor

import requests
import pydicom
import config
from logger import log_info, log_ok, log_erro, log_debug, log_finalizado, log_skip, log_aviso
from query import obter_metadata
from query import obter_metadata
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn, TransferSpeedColumn, ProgressColumn
from rich.text import Text

class SpeedColumn(ProgressColumn):
    """Renders speed in img/s."""
    def render(self, task: "Task") -> Text:
        speed = task.speed
        if speed is None:
            return Text("?", style="progress.data.speed")
        return Text(f"{speed:.1f} img/s", style="progress.data.speed")



# ============================================================
# JSON utilitários
# ============================================================

def _json_path(an: str) -> Path:
    return config.PROGRESS_DIR / f"{an}.json"


def _ler_json(an: str) -> dict:
    p = _json_path(an)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _gravar_json(an: str, data: dict):
    config.PROGRESS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _json_path(an).with_suffix(".json.tmp")
    final = _json_path(an)
    
    # Reordenar chaves para ficar bonito no arquivo
    # Ordem desejada: an, servidor, status, velocidade, total, baixadas, patient_name... [meta]... historico por ultimo
    ordenado = {}
    prioridade = ["an", "servidor", "status", "velocidade", "baixadas", "total", 
                  "patient_name", "study_desc", "modality", "study_uid"]
    
    for k in prioridade:
        if k in data:
            ordenado[k] = data[k]
            
    # Adiciona o resto (menos historico)
    for k, v in data.items():
        if k not in prioridade and k != "historico":
            ordenado[k] = v
            
    # Historico por último
    if "historico" in data:
        ordenado["historico"] = data["historico"]

    try:
        tmp.write_text(json.dumps(ordenado, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, final)
    except Exception as e:
        log_debug(f"[JSON] erro ao gravar {an}: {e}")


# ============================================================
# Inicialização de JSON
# ============================================================

def _iniciar_json(an: str, servidor: str, meta: dict) -> dict:
    base = {
        "an": an,
        "servidor": servidor,
        "study_uid": meta["study_uid"],
        "total": meta["total_instances"],
        "baixadas": 0,
        "velocidade": 0.0,
        "status": "ativo",
        "historico": [],
        # Metadados do paciente (serão populados no primeiro download)
        "patient_name": "—",
        "study_desc": "",
        "modality": "",
    }
    _gravar_json(an, base)
    return base


# ============================================================
# curl (agora requests)
# ============================================================

def _baixar_sop(url: str, destino: Path, extract_metadata: bool = False, verbose_error: bool = True):
    """
    Baixa SOP.
    Retorna (sucesso: bool, metadata: dict|None)
    """
    metadata_extracted = None

    # 2 tentativas por imagem
    for tentativa in range(1, 3):
        try:
            # timeout=(5, 30) -> 5s connect, 30s read
            with requests.get(url, stream=True, timeout=(5, 30)) as r:
                r.raise_for_status()
                
                destino.parent.mkdir(parents=True, exist_ok=True)

                with open(destino, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            
            # Validação / Processamento Pós-Download
            if destino.exists() and destino.stat().st_size > 0:
                
                # A) Extração de Metadados (apenas se solicitado)
                if extract_metadata:
                    try:
                        ds = pydicom.dcmread(destino, stop_before_pixels=True)
                        metadata_extracted = {
                            "patient_name": str(getattr(ds, "PatientName", "—")),
                            "study_desc": str(getattr(ds, "StudyDescription", "")),
                            "modality": str(getattr(ds, "Modality", ""))
                        }
                    except Exception as e_meta:
                        log_debug(f"Erro extraindo metadata: {e_meta}")

                # B) Lógica Storage Mode
                # Transient: Mover para Incoming
                # Persistent: Manter e (opcionalmente) Copiar
                
                final_ok = True

                if config.STORAGE_MODE == "transient":
                    if config.OSIRIX_INCOMING and config.OSIRIX_INCOMING.exists():
                        try:
                            shutil.move(str(destino), str(config.OSIRIX_INCOMING / destino.name))
                        except Exception as e_move:
                            if verbose_error: log_erro(f"Falha ao mover para OsiriX: {e_move}")
                            # Se não conseguiu mover, falha o processo? Ou deixa no Temp?
                            # Deixar no Temp é risco de encher disco. Melhor considerar erro se move falhar.
                            final_ok = False
                    else:
                        # Modo transient sem incoming configurado? Apenas apaga?
                        # Isso seria inútil. Logar erro.
                        if verbose_error: log_erro("Modo Transient ativo mas OSIRIX_INCOMING inválido.")
                        if destino.exists(): destino.unlink()
                        final_ok = False
                
                elif config.STORAGE_MODE in ["persistent", "pipeline"]:
                    # Lógica antiga de cópia para OsiriX (Legacy)
                    if config.VIEWER in ["osirix", "horos"] and config.OSIRIX_INCOMING and config.OSIRIX_INCOMING.exists():
                        try:
                            # Cópia simples
                            shutil.copy2(destino, config.OSIRIX_INCOMING / destino.name)
                        except Exception:
                            pass # Silent fail no persistent copy

                return final_ok, metadata_extracted
                
        except Exception as e:
            # ... tratamento de erro igual ...
            is_500 = False
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                if e.response.status_code == 500:
                    is_500 = True

            if tentativa == 2:
                if is_500:
                    log_debug(f"Erro SOP (final) [500]: {e}")
                elif verbose_error:
                    log_erro(f"Erro SOP (final): {e}")
            else:
                time.sleep(0.5)

            if destino.exists():
                try: destino.unlink()
                except OSError: pass

    return False, None


def _salvar_metadata_dicom(dcm_path: Path, output_json: Path):
    """
    Lê um arquivo DICOM e exporta seus metadados principais para JSON.
    Usa representação simplificada (Nome: Valor).
    """
    try:
        import json
        ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
        
        meta = {}
        # Itera sobre os elementos do dataset (excluindo sequências complexas e binários para brevidade)
        for elem in ds:
            if elem.VR in ["OB", "OW", "SQ"]: continue
            meta[elem.name] = str(elem.value)
            
        output_json.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except Exception as e:
        log_debug(f"Erro ao exportar metadados DICOM: {e}")
        return False


def _enviar_para_pipeline_api(an: str, servidor: str, destino_base: Path, js: dict) -> tuple[bool, bool]:
    """
    Envia payload simplificado para a API de pipeline, quando configurada.
    Se a API não estiver configurada, não bloqueia o fluxo de download.
    Retorna:
    - ok: se o fluxo pode seguir sem erro
    - has_response: se houve resposta da API gravada (pipeline_response.json)
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
    if ("TORAX" not in exame_norm) or ("PERFIL" in exame_norm):
        log_skip(f"[PIPELINE] AN {an}: exame '{exame}' fora do critério (requer TORAX e sem PERFIL).")
        return True, False

    request_format = getattr(config, "PIPELINE_REQUEST_FORMAT", "json")
    headers = {}
    token = getattr(config, "PIPELINE_API_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    timeout = max(1, int(getattr(config, "PIPELINE_TIMEOUT", 30)))
    response_trace_file = destino_base / "pipeline_response.json"

    def _safe_json_or_text(resp: requests.Response):
        try:
            return resp.json()
        except Exception:
            return resp.text

    def _select_pipeline_dcm(dicom_files: list[Path]) -> Path:
        """
        Seleciona o DICOM para envio no pipeline:
        - Se houver 2+ séries, usa o primeiro arquivo da 2ª série.
        - Senão, se houver 2+ arquivos, usa o 2º arquivo.
        - Senão, usa o único arquivo.
        """
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
                if series_num_raw.isdigit():
                    series_num = int(series_num_raw)
                if instance_num_raw.isdigit():
                    instance_num = int(instance_num_raw)
            except Exception:
                pass
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
                if age_unit == "Y":
                    age_value = f"{age_num}-years-old"
                elif age_unit == "M":
                    age_value = f"{age_num}-months-old"
                elif age_unit == "W":
                    age_value = f"{age_num}-weeks-old"
                elif age_unit == "D":
                    age_value = f"{age_num}-days-old"
        except Exception as e:
            log_debug(f"[PIPELINE] AN {an}: não foi possível extrair PatientAge: {e}")
        if not age_value:
            log_aviso(f"[PIPELINE] AN {an}: PatientAge ausente/indefinido no DICOM. Envio ignorado.")
            return True, False

        form_data = {
            "age": age_value,
            "prompt": getattr(config, "PIPELINE_PROMPT", ""),
        }
        # Remove campos vazios para não enviar dado inútil
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
                    "response": {
                        "status_code": resp.status_code,
                        "body": _safe_json_or_text(resp),
                    },
                }
                response_trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
                resp.raise_for_status()
            log_ok(f"[PIPELINE] AN {an}: multipart enviado para API ({dcm_file.name}).")
            return True, True
        except Exception as e:
            log_erro(f"[PIPELINE] AN {an}: falha no envio multipart: {e}")
            return (not getattr(config, "PIPELINE_STRICT", False), False)

    dicom_meta_files = sorted(destino_base.glob("metadado_*_dicom.json"))
    payload = {
        "an": an,
        "servidor": servidor,
        "study_uid": js.get("study_uid", ""),
        "patient_name": js.get("patient_name", ""),
        "study_desc": js.get("study_desc", ""),
        "modality": js.get("modality", ""),
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
            "response": {
                "status_code": resp.status_code,
                "body": _safe_json_or_text(resp),
            },
        }
        response_trace_file.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        resp.raise_for_status()
        log_ok(f"[PIPELINE] AN {an}: payload JSON enviado para API.")
        return True, True
    except Exception as e:
        log_erro(f"[PIPELINE] AN {an}: falha no envio JSON para API: {e}")
        return (not getattr(config, "PIPELINE_STRICT", False), False)


def _gravar_laudo_do_pipeline(an: str, destino_base: Path) -> bool:
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

    title = str(cockpit.get("exame") or cockpit.get("nm_exame") or "LAUDO")
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
    if getattr(config, "PIPELINE_USE_REVISAR", False):
        gravar_cmd.append("--revisar")

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


# ============================================================
# Baixar um único AN
# ============================================================

def baixar_an(servidor: str, an: str, mostrar_progresso: bool = True) -> bool:
    
    # ------------------------------------------------------------
    # 1. Consulta WADO (XML) - Source of Truth
    # ------------------------------------------------------------
    # Se o 'an' for composto (ex: AN_ID), extraímos apenas o AN para a query WADO
    pure_an = an.split("_")[0] if "_" in an else an

    try:
        meta = obter_metadata(pure_an, servidor)
    except requests.exceptions.ConnectionError:
        log_erro(f"[{servidor}] {an}: Servidor indisponível (Connection Refused).")
        return False
    except Exception as e:
        log_erro(f"[{servidor}] {an}: falha na query — {e}")
        return False

    total = meta["total_instances"]
    study_uid = meta["study_uid"]
    series = meta["series"]

    if total == 0:
        log_erro(f"[{servidor}] {an}: estudo sem imagens")
        return False

    # ------------------------------------------------------------
    # 2. Validação com Cache Local (JSON)
    # ------------------------------------------------------------
    js = _ler_json(an)
    
    # Se não existe JSON, cria novo com dados atuais
    if not js:
        js = _iniciar_json(an, servidor, meta)
    else:
        js["total"] = total
        _gravar_json(an, js)

    # Verifica quais já foram baixados baseado no histórico e na existência do arquivo
    # Se Transient: confia Apenas no Histórico (já que arquivos foram movidos)
    # Se Persistent: Revalida com disco
    
    if config.STORAGE_MODE == "transient":
        # Diretório base será temporário
        destino_base = config.TMP_DIR / an
    else:
        # Diretório base será persistente/local (persistent ou pipeline)
        # OUTPUT_DICOM_DIR já vem configurado com detecção automática de SO
        destino_base = config.OUTPUT_DICOM_DIR / an
    
    historico = set(js.get("historico", []))
    historico_validado = set()

    if config.STORAGE_MODE == "transient":
        # Confiança total no histórico JSON (assumimos que 'delivered' é verdade)
        historico_validado = historico
    else:
        # Revalidação Física (Persistent)
        for sop in historico:
            p = destino_base / f"{sop}.dcm"
            if p.exists() and p.stat().st_size > 0:
                 historico_validado.add(sop)
    
    historico = historico_validado
    baixadas = len(historico)
    faltantes = total - baixadas

    if faltantes == 0:
        log_skip(f"[{servidor}] AN {an} — já estava completo.")
        js["status"] = "completo"
        js["baixadas"] = total
        # js["historico"] já está atualizado
        _gravar_json(an, js)
        
        # Se mode transient, garantir que não sobra pasta vazia de rodadas anteriores
        if config.STORAGE_MODE == "transient" and destino_base.exists():
            try: shutil.rmtree(destino_base)
            except: pass
            
        return True

    # Agora sim criamos o diretório
    destino_base.mkdir(parents=True, exist_ok=True)

    log_info(f"[{servidor}] AN {an}: iniciando download ({faltantes} faltantes)...")

    srv = config.SERVERS[servidor]
    
    inicio = perf_counter()
    novos = 0
    
    # Flag para saber se precisamos extrair metadata (se JSON estiver vazio disso)
    precisa_metadata = (js.get("patient_name", "—") == "—")

    # lista de SOPs faltantes
    faltantes_list = []
    for s in series:
        suid = s["series_uid"]
        for sop in s["instances"]:
            if sop not in historico:
                faltantes_list.append((suid, sop))

    # ------------------------------------------------------------
    # BLOCO COM ThreadPoolExecutor
    # ------------------------------------------------------------
    try:
        with ThreadPoolExecutor(max_workers=config.DOWNLOAD_WORKERS) as pool:
            futures = []

            for i, (suid, sop) in enumerate(faltantes_list):

                url = (
                    f"http://{srv['server']}:{srv['wado_port']}/{srv['wado_path']}"
                    f"?requestType=WADO"
                    f"&studyUID={study_uid}"
                    f"&seriesUID={suid}"
                    f"&objectUID={sop}"
                    f"&contentType=application/dicom"
                )

                nome_arquivo = f"{sop}.dcm"
                destino = destino_base / nome_arquivo
                
                # Pedir metadata apenas para o primeiro da fila, se precisarmos
                extrair_agora = (precisa_metadata and i == 0)

                futures.append((sop, pool.submit(
                    _baixar_sop, url, destino, extract_metadata=extrair_agora, verbose_error=True
                )))

            # Consumir resultados com RICH
            if mostrar_progresso:
                progress_columns = [
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    SpeedColumn(),
                    TimeRemainingColumn(),
                ]
                with Progress(*progress_columns, transient=True) as progress:
                    task_id = progress.add_task(f"[{servidor}] Baixando {an}", total=len(futures))
                    
                    for sop, fut in futures:
                        try:
                            ok, meta_retornado = fut.result()
                        except (KeyboardInterrupt, RuntimeError) as e:
                            # ... (tratamento de erro inalterado) ...
                            if isinstance(e, RuntimeError) and "schedule new futures" not in str(e):
                                raise e
                            log_erro("Interrupção detectada (Shutdown) — encerrando imediatamente.")
                            raise KeyboardInterrupt

                        if ok:
                            historico.add(sop)
                            novos += 1
                            if meta_retornado:
                                js.update(meta_retornado)
                                precisa_metadata = False

                        vel = (novos) / (perf_counter() - inicio + 0.001)
                        js.update({
                            "baixadas": len(historico),
                            "total": total,
                            "velocidade": round(vel, 1),
                            "status": "baixando",
                            "historico": list(historico),
                        })
                        _gravar_json(an, js)
                        
                        progress.update(task_id, advance=1)
            else:
                # Sem progresso visual (loop simples)
                for sop, fut in futures:
                     # Copiar lógica de processamento do resultado aqui caso precise (ou refatorar)
                     # Para simplificar e evitar duplicação no prompt, assumimos que no-progress é raro no CLI interativo
                     # mas logicamente deveria processar igual.
                     try:
                        ok, meta_retornado = fut.result()
                        if ok:
                            historico.add(sop)
                            novos += 1
                            if meta_retornado: js.update(meta_retornado)
                        
                        vel = (novos) / (perf_counter() - inicio + 0.001)
                        js.update({"baixadas": len(historico), "total": total, "velocidade": round(vel, 1), "status": "baixando", "historico": list(historico)})
                        _gravar_json(an, js)
                     except: pass

    except KeyboardInterrupt:
        return False

    # ------------------------------------------------------------
    # Avaliação final
    # ------------------------------------------------------------
    completas = len(historico)

    if completas >= total:
        vel_final = novos / (perf_counter() - inicio + 0.001)
        js.update({
            "baixadas": completas,
            "velocidade": round(vel_final, 1),
            "status": "completo",
            "historico": list(historico),
        })
        _gravar_json(an, js)
        
        # Limpeza do diretório TMP se estiver vazio ou sobrar lixo (Transient)
        if config.STORAGE_MODE == "transient":
            try:
                # Remove a pasta do AN em tmp se estiver vazia (shutil.move removeu arquivos)
                # Se sobrar algo (erros), rmtree limpa.
                if destino_base.exists():
                    shutil.rmtree(destino_base)
            except: pass

        log_finalizado(f"[{servidor}] AN {an} — completo ({vel_final:.1f} img/s)")
        
        # ------------------------------------------------------------
        # Entrega de Metadados (Cockpit e DICOM)
        # ------------------------------------------------------------
        # 1. Metadado Cockpit (subjson)
        meta_origem = config.COCKPIT_METADATA_DIR / f"{an}.json"
        if meta_origem.exists():
            try:
                # Se houver metadado no cache, move para a pasta final (evita duplicata)
                if config.STORAGE_MODE in ["persistent", "pipeline"]:
                    shutil.move(str(meta_origem), str(destino_base / "metadata_cockpit.json"))
            except Exception as e:
                log_debug(f"Erro ao mover metadados cockpit para pasta final: {e}")

        # 2. Metadado DICOM (por série: metadado_{SeriesNumber}_dicom.json)
        if getattr(config, 'SAVE_METADATA', False):
            # Agrupa imagens por SeriesInstanceUID e extrai SeriesNumber de cada
            series_processadas = set()
            for dcm_file in destino_base.glob("*.dcm"):
                try:
                    ds = pydicom.dcmread(str(dcm_file), stop_before_pixels=True)
                    series_uid = str(getattr(ds, "SeriesInstanceUID", ""))
                    
                    # Evita processar a mesma série múltiplas vezes
                    if series_uid in series_processadas:
                        continue
                    series_processadas.add(series_uid)
                    
                    # Obtém SeriesNumber (tag 0020,0011)
                    series_number = str(getattr(ds, "SeriesNumber", "unknown"))
                    
                    # Salva metadado da série
                    output_name = f"metadado_{series_number}_dicom.json"
                    _salvar_metadata_dicom(dcm_file, destino_base / output_name)
                    
                except Exception as e:
                    log_debug(f"Erro ao processar metadados de série: {e}")

        if config.STORAGE_MODE == "pipeline":
            entrega_ok, has_response = _enviar_para_pipeline_api(an, servidor, destino_base, js)
            if not entrega_ok:
                js["status"] = "pipeline_erro"
                _gravar_json(an, js)
                return False
            if has_response:
                laudo_ok = _gravar_laudo_do_pipeline(an, destino_base)
                if not laudo_ok:
                    js["status"] = "pipeline_laudo_erro"
                    _gravar_json(an, js)
                    return False
            else:
                log_info(f"[PIPELINE] AN {an}: gravação de laudo pulada (sem resposta de API).")

        return True

    # ------------------------------------------------------------
    # Retry parcial
    # ------------------------------------------------------------
    # ------------------------------------------------------------
    # Retry parcial (Removido persistência)
    # ------------------------------------------------------------
    # Se chegou aqui, é porque completas < total
    log_erro(f"[{servidor}] AN {an} incompleto ({completas}/{total}).")

    # Se mode transient, limpar se sobrou lixo (parcial ou vazio)
    if config.STORAGE_MODE == "transient" and destino_base.exists():
         try: shutil.rmtree(destino_base)
         except: pass

    return False




# ============================================================
# Clipboard
# ============================================================

def _ler_clipboard() -> list[str]:
    """Lê do clipboard (Mac/Windows) e retorna lista de ANs únicos não vazios."""
    text = ""
    system = platform.system()
    
    try:
        if system == "Darwin":  # macOS
            text = subprocess.check_output("pbpaste", encoding="utf-8")
        elif system == "Windows":
            # Powershell Get-Clipboard
            text = subprocess.check_output(["powershell", "Get-Clipboard"], encoding="utf-8")
        else:
            log_erro("Sistema não suportado para leitura de clipboard automático.")
            return []
    except Exception as e:
        log_erro(f"Erro ao ler clipboard: {e}")
        return []

    # Processar linhas
    ans = []
    for line in text.splitlines():
        clean = line.strip()
        if clean and clean not in ans:
            ans.append(clean)
    
    return ans


# ============================================================
# CLI — baixa 1 AN ou Batch do Clipboard
# ============================================================

def main():
    description = "Motor de Download DICOM (WADO)"
    # add_help=False para customizar a mensagem de -h
    parser = argparse.ArgumentParser(description=description, add_help=False)

    # Grupos para traduzir cabeçalhos
    pos_group = parser.add_argument_group("Argumentos")
    opt_group = parser.add_argument_group("Opções")

    pos_group.add_argument("servidor", nargs="?", help="HBR ou HAC (opcional em Batch)")
    pos_group.add_argument("an", nargs="?", help="Accession Number (opcional). Se omitido, lê do clipboard.")
    
    opt_group.add_argument("--no-progress", "-np", action="store_true", help="Desativar barra de progresso")
    opt_group.add_argument("--metadado", action="store_true", help="Salva metadados Cockpit/DICOM")
    opt_group.add_argument("-h", "--help", action="help", help="Mostra esta mensagem de ajuda e sai")
    
    args = parser.parse_args()

    # Override config se flag presente
    if args.metadado:
        config.SAVE_METADATA = True

    # 1. Caso 'python downloader.py' (sem args) -> Batch Auto-Detect (HAC -> HBR)
    if not args.servidor:
        log_info("[BATCH AUTO] Lendo ANs da área de transferência...")
        lista_ans = _ler_clipboard()
        if not lista_ans:
            log_erro("Nenhum AN encontrado na área de transferência.")
            return
        
        total = len(lista_ans)
        log_info(f"[BATCH AUTO] {total} ANs encontrados.")
        
        sucessos = 0
        falhas = 0
        
        for idx, an in enumerate(lista_ans, 1):
            log_info(f"--- Processando {idx}/{total}: {an} ---")
            
            # Tenta HAC primeiro
            ok = baixar_an("HAC", an, mostrar_progresso=not args.no_progress)
            
            if not ok:
                log_info(f" -> Falha no HAC. Tentando HBR para {an}...")
                ok = baixar_an("HBR", an, mostrar_progresso=not args.no_progress)
            
            if ok:
                sucessos += 1
            else:
                falhas += 1
        
        log_finalizado(f"[BATCH AUTO] Fim. Sucessos: {sucessos} | Falhas: {falhas}")
        return

    # 2. Caso 'python downloader.py SERVER' (sem AN) -> Batch Server Específico
    if args.servidor and not args.an:
        servidor = args.servidor.upper()
        if servidor not in config.SERVERS:
            log_erro(f"Servidor '{servidor}' inválido. Use HBR ou HAC.")
            return

        log_info(f"[BATCH {servidor}] Lendo ANs da área de transferência...")
        lista_ans = _ler_clipboard()
        if not lista_ans:
            log_erro("Nenhum AN encontrado na área de transferência.")
            return

        total = len(lista_ans)
        log_info(f"[BATCH {servidor}] {total} ANs encontrados.")
        
        sucessos = 0
        falhas = 0

        for idx, an in enumerate(lista_ans, 1):
            log_info(f"--- Processando {idx}/{total}: {an} ---")
            ok = baixar_an(servidor, an, mostrar_progresso=not args.no_progress)
            if ok:
                sucessos += 1
            else:
                falhas += 1

        log_finalizado(f"[BATCH {servidor}] Fim. Sucessos: {sucessos} | Falhas: {falhas}")
        return

    # 3. Caso 'python downloader.py SERVER AN' -> Single
    if args.servidor and args.an:
        servidor = args.servidor.upper()
        # Se servidor for um AN (usuário inverteu ou omitiu server?), melhor validar.
        # Mas assumindo uso correto:
        if servidor not in config.SERVERS:
             # Tentar ser esperto? Não, melhor erro.
            log_erro(f"Servidor '{servidor}' inválido. Use HBR ou HAC.")
            return
        
        baixar_an(servidor, args.an, mostrar_progresso=not args.no_progress)


if __name__ == "__main__":
    main()
