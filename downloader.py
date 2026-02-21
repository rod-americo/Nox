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
from pydicom import config as pydicom_config
pydicom_config.settings.reading_validation_mode = pydicom_config.IGNORE
import config
from logger import log_info, log_ok, log_erro, log_debug, log_finalizado, log_skip, log_aviso
from query import obter_metadata
from pipeline import pipeline_ativo_no_modo_atual
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
                            incoming_target = config.OSIRIX_INCOMING / destino.name
                            if pipeline_ativo_no_modo_atual():
                                # transient + pipeline: entrega ao viewer sem perder a cópia local temporária
                                shutil.copy2(destino, incoming_target)
                            else:
                                shutil.move(str(destino), str(incoming_target))
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



# ============================================================
# Baixar um único AN
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
        
        log_finalizado(f"[{servidor}] AN {an} — completo ({vel_final:.1f} img/s)")
        
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
         try:
             shutil.rmtree(destino_base)
         except Exception as e:
             log_debug(f"Erro limpando temporário parcial de {an}: {e}")

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
            if ok:
                import pipeline
                import config
                import shutil
                des_cli = config.TMP_DIR / an if config.STORAGE_MODE == "transient" else config.OUTPUT_DICOM_DIR / an
                cli_js = _ler_json(an)
                pipe_ok, final_status = pipeline.processar_exame(an, "HAC", des_cli, cli_js)
                if final_status != "completo":
                    cli_js["status"] = final_status
                    _gravar_json(an, cli_js)
                if not pipe_ok: ok = False
                if config.STORAGE_MODE == "transient" and des_cli.exists():
                    try: shutil.rmtree(des_cli)
                    except: pass
            
            if not ok:
                log_info(f" -> Falha no HAC. Tentando HBR para {an}...")
            ok = baixar_an("HBR", an, mostrar_progresso=not args.no_progress)
            if ok:
                import pipeline
                import config
                import shutil
                des_cli = config.TMP_DIR / an if config.STORAGE_MODE == "transient" else config.OUTPUT_DICOM_DIR / an
                cli_js = _ler_json(an)
                pipe_ok, final_status = pipeline.processar_exame(an, "HBR", des_cli, cli_js)
                if final_status != "completo":
                    cli_js["status"] = final_status
                    _gravar_json(an, cli_js)
                if not pipe_ok: ok = False
                if config.STORAGE_MODE == "transient" and des_cli.exists():
                    try: shutil.rmtree(des_cli)
                    except: pass
            
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
                import pipeline
                import config
                import shutil
                des_cli = config.TMP_DIR / an if config.STORAGE_MODE == "transient" else config.OUTPUT_DICOM_DIR / an
                cli_js = _ler_json(an)
                pipe_ok, final_status = pipeline.processar_exame(an, servidor, des_cli, cli_js)
                if final_status != "completo":
                    cli_js["status"] = final_status
                    _gravar_json(an, cli_js)
                if not pipe_ok: ok = False
                if config.STORAGE_MODE == "transient" and des_cli.exists():
                    try: shutil.rmtree(des_cli)
                    except: pass
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
        
                ok = baixar_an(servidor, an, mostrar_progresso=not args.no_progress)
        if ok:
            import pipeline
            import config
            import shutil
            des_cli = config.TMP_DIR / an if config.STORAGE_MODE == "transient" else config.OUTPUT_DICOM_DIR / an
            cli_js = _ler_json(an)
            pipe_ok, final_status = pipeline.processar_exame(an, servidor, des_cli, cli_js)
            if final_status != "completo":
                cli_js["status"] = final_status
                    _gravar_json(an, cli_js)
            if not pipe_ok: ok = False
            if config.STORAGE_MODE == "transient" and des_cli.exists():
                    try: shutil.rmtree(des_cli)
                    except: pass


if __name__ == "__main__":
    main()
