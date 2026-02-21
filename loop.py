#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
loop.py — Orquestrador Cockpit (Versão Nativa/Threaded)
-------------------------------------------------------

Este módulo implementa o loop principal de monitoramento e download automático.
É o ponto de entrada para execução sem interface gráfica (CLI/headless).

FLUXO DE EXECUÇÃO:
1. (Opcional) Executa prepare.py UMA VEZ para login e captura de sessão
2. Loop contínuo:
   • Verifica se há threads de download ativas
     - Se sim: aguarda (blocking)
     - Se não: executa fetcher para buscar novos exames
   • Com novos dados, dispara Threads separadas para HBR e HAC
   • Cada Thread chama downloader.baixar_an() sequencialmente para sua lista
   • Aguarda intervalo configurado (LOOP_INTERVAL) antes do próximo ciclo

MODOS DE USO:

1. Modo padrão (usa cenários do config.ini):
   python loop.py

2. Com cenários específicos (arquivos JSON em queries/):
   python loop.py queries/plantao-rx.json queries/monitor.json

3. Pular etapa de preparação (usar sessão existente):
   python loop.py --no-prepare

4. Via nox.py CLI:
   python nox.py --cli

CLASSES:
- LoopController: Gerencia controle de fluxo (pause/resume/stop)

FUNÇÕES PRINCIPAIS:
- main(**kwargs): Ponto de entrada principal do loop
- worker_download(servidor, lista_ans, controller): Worker thread para downloads
- verificar_retencao_exames(): Mantém apenas N exames mais recentes
- limpar_antigos(dias): Remove arquivos de progresso antigos
"""

import sys
import os
import time
import shutil
import argparse
import threading
import subprocess
from pathlib import Path

import config
from logger import log_info, log_erro, log_debug
import fetcher
import downloader
import pipeline


# ============================================================
# Controller (Interface para GUI)
# ============================================================

class LoopController:
    def __init__(self, success_limit: int | None = None):
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set() # Inicialmente rodando (não pausado)
        self._lock = threading.Lock()
        self._success_count = 0
        self._success_limit = success_limit if success_limit and success_limit > 0 else None
    
    def stop(self):
        """Sinaliza parada total do loop."""
        self._stop_event.set()
        # Garante que não fique travado no pause
        self._pause_event.set()

    def pause(self):
        """Pausa o loop."""
        self._pause_event.clear()

    def resume(self):
        """Retoma execução."""
        self._pause_event.set()

    def set_success_limit(self, limit: int | None):
        with self._lock:
            self._success_limit = limit if limit and limit > 0 else None

    def register_success(self) -> tuple[int, int | None, bool]:
        """
        Incrementa contador global de sucessos.
        Retorna (count_atual, limite, atingiu_limite).
        """
        with self._lock:
            self._success_count += 1
            count = self._success_count
            limit = self._success_limit
            reached = bool(limit and count >= limit)
            if reached:
                self._stop_event.set()
                self._pause_event.set()
            return count, limit, reached

    @property
    def should_stop(self):
        return self._stop_event.is_set()

    def wait_if_paused(self):
        """Bloqueia se estiver pausado. Retorna False se parar durante wait."""
        while not self._pause_event.is_set():
            if self.should_stop:
                return False
            time.sleep(0.5)
        return True


# ============================================================
# Worker
# ============================================================

# Variável global para delay entre downloads (controlada via CLI)
DOWNLOAD_DELAY = 0

def worker_download(servidor: str, lista_ans: list, controller=None):
    """
    Processa uma lista de ANs sequencialmente.
    Chamado em Thread separada.
    """
    global DOWNLOAD_DELAY
    
    total = len(lista_ans)
    log_info(f"[{servidor}] Iniciando lote com {total} exames.")

    sucessos = 0
    erros = 0

    for i, an in enumerate(lista_ans, 1):
        # Verifica Pause/Stop entre exames
        if controller:
            if controller.should_stop:
                log_info(f"[{servidor}] Parando (Shutdown)...")
                break

            if not controller.wait_if_paused():
                log_info(f"[{servidor}] Interrompido pelo controlador.")
                break

        try:
            log_info(f"[{servidor}] ({i}/{total}) Processando AN {an}...")
            # Download Normal
            ok = downloader.baixar_an(servidor, an, mostrar_progresso=False)
            
            if ok:
                des_base = config.TMP_DIR / an if config.STORAGE_MODE == "transient" else config.OUTPUT_DICOM_DIR / an
                js = downloader._ler_json(an)
                pipe_ok, final_status = pipeline.processar_exame(an, servidor, des_base, js)
                
                if final_status != "completo":
                    js["status"] = final_status
                    downloader._gravar_json(an, js)
                
                if config.STORAGE_MODE == "transient" and des_base.exists():
                    try: shutil.rmtree(des_base)
                    except Exception as e: log_erro(f"Erro limpando transient em {an}: {e}")
                
                if pipe_ok:
                    sucessos += 1
                    if controller:
                        count, limit, reached = controller.register_success()
                        if limit:
                            log_info(f"[{servidor}] Sucesso global: {count}/{limit}")
                        if reached:
                            log_info(f"[{servidor}] Limite global de sucessos atingido ({count}/{limit}). Encerrando.")
                            break
            else:
                erros += 1
            
            # Delay entre downloads (se configurado)
            if DOWNLOAD_DELAY > 0 and i < total:
                time.sleep(DOWNLOAD_DELAY)

        except Exception as e:
            if "interpreter shutdown" in str(e):
                break
            log_erro(f"[{servidor}] Exceção no AN {an}: {e}")
            erros += 1

    log_info(f"[{servidor}] Lote finalizado. OK: {sucessos}, Err/Incomp: {erros}")


# ============================================================
# Manutenção
# ============================================================

def limpar_antigos(dias=7):
    """
    Remove arquivos .json de progresso antigos para não acumular lixo.
    """
    limite = time.time() - (dias * 86400)
    removidos = 0
    
    if not config.PROGRESS_DIR.exists():
        return

    for p in config.PROGRESS_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < limite:
                p.unlink()
                removidos += 1
        except Exception as e:
            log_erro(f"Erro ao limpar {p.name}: {e}")

    if removidos > 0:
        log_info(f"Manutenção: {removidos} arquivos de progresso antigos (> {dias} dias) foram removidos.")

    # Também limpa metadados cockpit órfãos/antigos
    if config.COCKPIT_METADATA_DIR.exists():
        for p in config.COCKPIT_METADATA_DIR.glob("*.json"):
            try:
                if p.stat().st_mtime < limite:
                    p.unlink()
            except Exception as e:
                log_debug(f"Erro limpando metadado antigo {p.name}: {e}")


def verificar_retencao_exames():
    """
    Mantém apenas os N exames mais recentes.
    - Persistent/Pipeline: Baseado nas pastas em OUTPUT_DICOM_DIR.
    - Transient: Baseado nos arquivos JSON em PROGRESS_DIR.
    """
    limite = config.MAX_EXAMES
    
    # --- MODO TRANSIENT (Gerenciamento apenas por JSON) ---
    if config.STORAGE_MODE == "transient":
        if not config.PROGRESS_DIR.exists():
            return
            
        jsons = sorted(
            [j for j in config.PROGRESS_DIR.glob("*.json")],
            key=lambda x: x.stat().st_mtime
        )
        
        total = len(jsons)
        excesso = total - limite
        
        if excesso > 0:
            log_info(f"Retenção (Transient): Limpando {excesso} registros antigos de histórico...")
            for j in jsons[:excesso]:
                try:
                    j.unlink()
                except Exception as e:
                    log_erro(f"Erro ao remover JSON {j.name}: {e}")
        return

    # --- MODOS LOCAIS (Persistent/Pipeline): Gerenciamento por Pastas + JSON Sync ---
    base = config.OUTPUT_DICOM_DIR
    if not base.exists():
        return

    # Lista apenas diretórios
    pastas = sorted(
        [p for p in base.iterdir() if p.is_dir()],
        key=lambda x: x.stat().st_mtime
    )
    
    total = len(pastas)
    excesso = total - limite
    
    if excesso > 0:
        log_info(f"Retenção: Encontrados {total} exames (Limite: {limite}). Removendo {excesso} antigos...")
        
        # Remove os primeiros 'excesso' (os mais antigos)
        for p in pastas[:excesso]:
            try:
                shutil.rmtree(p)
                log_info(f" -> Removido Pasta: {p.name}")
                
                # Remove também o JSON de progresso
                json_progresso = config.PROGRESS_DIR / f"{p.name}.json"
                if json_progresso.exists():
                    os.remove(json_progresso)
                    log_info(f" -> Removido JSON: {json_progresso.name}")
                    
            except Exception as e:
                log_erro(f"Erro ao remover {p.name}: {e}")

    # --- Limpeza de JSONs órfãos (Apenas Persistent) ---
    # Se o usuário apagou a pasta manualmente, o JSON deve sumir também.
    json_dir = config.PROGRESS_DIR
    if json_dir.exists():
        for j in json_dir.glob("*.json"):
            an = j.stem
            pasta_exame = base / an
            if not pasta_exame.exists():
                try:
                    os.remove(j)
                    log_info(f"Limpeza: JSON órfão removido ({j.name})")
                except Exception as e:
                    log_erro(f"Erro ao remover JSON órfão {j.name}: {e}")
        
        # Limpa COCKPIT_METADATA_DIR órfão
        if config.COCKPIT_METADATA_DIR.exists():
            for j in config.COCKPIT_METADATA_DIR.glob("*.json"):
                if not (base / j.stem).exists():
                    try: j.unlink()
                    except Exception as e:
                        log_debug(f"Erro limpando metadado órfão {j.name}: {e}")


def _resolve_scenarios(raw_inputs: list[str]) -> tuple[list[str], list[str]]:
    """
    Resolve entradas de cenário para:
    - scenario_names: nomes lógicos (MONITOR, MONITOR_RX...) usados no prepare
    - scenario_files: caminhos JSON de payload para fetcher
    """
    scenario_names: list[str] = []
    scenario_files: list[str] = []

    for item in raw_inputs:
        token = str(item).strip()
        if not token:
            continue

        p = Path(token)
        if token.lower().endswith(".json") or p.exists():
            scenario_files.append(str(p))
            continue

        normalized = token.upper().replace(".JSON", "")
        scenario_names.append(normalized)
        scenario_files.append(str(config.DATA_DIR / f"payload_{normalized}.json"))

    return scenario_names, scenario_files


def _validate_scenario_files(files: list[str]) -> list[str]:
    missing = []
    for f in files:
        if not Path(f).exists():
            missing.append(f)
    return missing


# ============================================================
# Main Loop
# ============================================================

def main(**kwargs):
    parser = argparse.ArgumentParser(description="Orquestrador Nox (Loop)", add_help=False)
    
    arg_group = parser.add_argument_group("Argumentos")
    opt_group = parser.add_argument_group("Opções")

    arg_group.add_argument("cenarios", nargs="*", help="Lista de cenários (ex: MONITOR) ou arquivos JSON")
    opt_group.add_argument("--no-prepare", action="store_true", help="Pular etapa de preparação (Login/Sessão)")
    opt_group.add_argument("--metadado", action="store_true", help="Ativa exportação de metadados Cockpit/DICOM")
    opt_group.add_argument("--delay", type=float, default=0, help="Delay em segundos entre cada download de AN (ex: 1.5)")
    opt_group.add_argument("--limit", type=int, default=0, help="Para após N downloads bem-sucedidos (global HBR+HAC)")
    opt_group.add_argument("--fetch-limit", type=int, default=0, help="Limite máximo de ANs por ciclo de fetch.")
    opt_group.add_argument("--once", action="store_true", help="Executa apenas um ciclo de fetch+download e encerra.")
    opt_group.add_argument("--storage-mode", choices=["transient", "persistent", "pipeline"], help="Override do storage_mode em runtime.")
    pipe_toggle = opt_group.add_mutually_exclusive_group()
    pipe_toggle.add_argument("--pipeline-enabled", action="store_true", help="Força pipeline habilitado em runtime.")
    pipe_toggle.add_argument("--pipeline-disabled", action="store_true", help="Força pipeline desabilitado em runtime.")
    opt_group.add_argument("--pipeline-on-transient", action="store_true", help="Permite pipeline também em storage_mode transient.")
    opt_group.add_argument("--pipeline-api-url", help="Override da URL de API do pipeline.")
    opt_group.add_argument("--pipeline-request-format", choices=["json", "multipart_single_file"], help="Formato de request para pipeline.")
    opt_group.add_argument("--pipeline-strict", action="store_true", help="Falha AN quando pipeline/API falha.")
    opt_group.add_argument("--pipeline-include", help="Critérios obrigatórios no nome do exame (CSV, ex: TORAX).")
    opt_group.add_argument("--pipeline-exclude", help="Critérios de exclusão no nome do exame (CSV, ex: PERFIL).")
    opt_group.add_argument("-h", "--help", action="help", help="Mostra esta mensagem de ajuda e sai")
    
    has_external_controller = bool(kwargs.get("controller"))

    def fatal(msg: str, code: int = 1):
        if has_external_controller:
            raise RuntimeError(msg)
        log_erro(msg)
        sys.exit(code)

    # Se chamado via GUI, args podem vir vazios ou customizados
    try:
        if "args" in kwargs:
            args = parser.parse_args(kwargs["args"])
        else:
            args = parser.parse_args()
    except SystemExit:
        if has_external_controller:
            raise RuntimeError("Argumentos inválidos para loop.")
        raise
    
    # Override config se flag presente
    if args.metadado:
        config.SAVE_METADATA = True
    if args.delay < 0:
        fatal("Parâmetro --delay não pode ser negativo.")
    if args.limit < 0:
        fatal("Parâmetro --limit não pode ser negativo.")
    if args.fetch_limit < 0:
        fatal("Parâmetro --fetch-limit não pode ser negativo.")

    if args.storage_mode:
        config.STORAGE_MODE = args.storage_mode
    if args.pipeline_enabled:
        config.PIPELINE_ENABLED = True
    if args.pipeline_disabled:
        config.PIPELINE_ENABLED = False
    if args.pipeline_on_transient:
        config.PIPELINE_ON_TRANSIENT = True
    if args.pipeline_api_url:
        config.PIPELINE_API_URL = args.pipeline_api_url.strip()
    if args.pipeline_request_format:
        config.PIPELINE_REQUEST_FORMAT = args.pipeline_request_format
    if args.pipeline_strict:
        config.PIPELINE_STRICT = True
    if args.pipeline_include is not None:
        config.PIPELINE_INCLUDE_TERMS = [t.strip().upper() for t in args.pipeline_include.split(",") if t.strip()]
    if args.pipeline_exclude is not None:
        config.PIPELINE_EXCLUDE_TERMS = [t.strip().upper() for t in args.pipeline_exclude.split(",") if t.strip()]
    
    # Seta delay global para threads
    global DOWNLOAD_DELAY
    DOWNLOAD_DELAY = args.delay if args.delay > 0 else 0
    if DOWNLOAD_DELAY > 0:
        log_info(f"Delay entre downloads: {DOWNLOAD_DELAY}s")
    if args.limit > 0:
        log_info(f"Limite global de sucessos: {args.limit}")

    fetch_limit = args.fetch_limit if args.fetch_limit > 0 else (args.limit if args.limit > 0 else None)
    if fetch_limit:
        log_info(f"Limite de fetch por ciclo: {fetch_limit}")

    if config.STORAGE_MODE == "pipeline" or (config.STORAGE_MODE == "transient" and getattr(config, "PIPELINE_ON_TRANSIENT", False)):
        config.SAVE_METADATA = True

    raw_inputs = list(args.cenarios) if args.cenarios else list(config.SCENARIOS)
    if not raw_inputs:
        fatal("Nenhum cenário configurado. Informe cenários na CLI ou ajuste SETTINGS.scenarios no config.ini.")

    scenario_names, cenarios = _resolve_scenarios(raw_inputs)

    log_info("=== ORQUESTRADOR NOX ===")
    log_info(f"Cenários: {', '.join(cenarios)}")
    log_info(f"Intervalo de check: {config.LOOP_INTERVAL}s")
    log_info(f"Storage: {config.STORAGE_MODE} | Pipeline: {'ON' if config.PIPELINE_ENABLED else 'OFF'} | Pipeline em transient: {'ON' if getattr(config, 'PIPELINE_ON_TRANSIENT', False) else 'OFF'}")

    # Limpeza inicial
    limpar_antigos()

    # 1) PREPARE (Executa apenas uma vez)
    if not args.no_prepare:
        try:
            cmd = [sys.executable, "prepare.py"]
            if scenario_names:
                cmd.extend(scenario_names)
            else:
                cmd.append("--login-only")
            log_info(f"Executando processo de preparação (Login)...")
            
            subprocess.run(cmd, check=True)
            
        except subprocess.CalledProcessError as e:
            fatal(f"Falha fatal no prepare.py (Exit Code {e.returncode})")
        except Exception as e:
            fatal(f"Erro ao invocar prepare.py: {e}")
    else:
        log_info("Prepare pulado (--no-prepare).")

    missing_files = _validate_scenario_files(cenarios)
    if missing_files:
        faltantes = ", ".join(missing_files)
        fatal(f"Payload(s) não encontrado(s): {faltantes}")

    # Variáveis de controle de Threads
    thread_hbr = None
    thread_hac = None
    ultimo_check = 0
    INTERVALO = config.LOOP_INTERVAL

    # 2) LOOP INFINITO
    controller = kwargs.get("controller") or LoopController(success_limit=args.limit)
    if kwargs.get("controller"):
        controller.set_success_limit(args.limit)

    try:
        while not controller.should_stop:
            # Check de pause
            if not controller.wait_if_paused():
                break

            # Verificar se threads estão rodando
            hbr_ativo = thread_hbr and thread_hbr.is_alive()
            hac_ativo = thread_hac and thread_hac.is_alive()

            if hbr_ativo or hac_ativo:
                time.sleep(5)
                continue

            # Controle de tempo: respeitar intervalo mínimo entre checks
            if not args.once and ultimo_check > 0:
                decorrido = time.time() - ultimo_check
            else:
                decorrido = INTERVALO
            if decorrido < INTERVALO and not args.once:
                espera_total = int(INTERVALO - decorrido)
                if espera_total > 0:
                    for i in range(espera_total, 0, -1):
                        if controller.should_stop:
                            break
                        try:
                            sys.stdout.write(f"\rAguardando {i}s para próximo ciclo...   ")
                            sys.stdout.flush()
                        except (IOError, AttributeError):
                            pass
                        time.sleep(1)
                    
                    try:
                        sys.stdout.write("\r" + " " * 40 + "\r")  
                        sys.stdout.flush()
                    except (IOError, AttributeError):
                        pass

            ultimo_check = time.time()
            
            log_info("--- Novo ciclo de verificação ---")
            
            # Manutenção de espaço
            verificar_retencao_exames()
            
            # 1. Fetch
            try:
                dados = fetcher.fetch_varios_arquivos(cenarios, limite=fetch_limit)
            except Exception as e:
                log_erro(f"Erro no fetcher: {e}")
                if args.once:
                    break
                log_info(f"Aguardando {config.LOOP_INTERVAL}s antes de tentar novamente...")
                time.sleep(config.LOOP_INTERVAL)
                continue

            lista_hbr = dados.get("HBR", [])
            lista_hac = dados.get("HAC", [])

            if not lista_hbr and not lista_hac:
                log_info(f"Nenhum exame encontrado.")
                if args.once:
                    break
                continue

            log_info(f"Fila para processar: HBR={len(lista_hbr)}, HAC={len(lista_hac)}")

            # 2. Disparar Threads
            if lista_hbr:
                thread_hbr = threading.Thread(target=worker_download, args=("HBR", lista_hbr, controller), daemon=True)
                thread_hbr.start()

            if lista_hac:
                thread_hac = threading.Thread(target=worker_download, args=("HAC", lista_hac, controller), daemon=True)
                thread_hac.start()

            if args.once:
                for t in (thread_hbr, thread_hac):
                    if t and t.is_alive():
                        t.join()
                log_info("Modo --once: ciclo único finalizado.")
                break

    except KeyboardInterrupt:
        log_info("\nInterrupção (Ctrl+C). Parando threads...")
        controller.stop()
        
        # Aguardar encerramento gracioso
        for t_name, t_obj in (("HBR", thread_hbr), ("HAC", thread_hac)):
            if t_obj and t_obj.is_alive():
                while t_obj.is_alive():
                    try:
                        t_obj.join(timeout=0.5)
                    except KeyboardInterrupt:
                        log_info(f"Interrupção adicional recebida durante join de {t_name}.")
                        break
        log_info("Shutdown completo.")
        return

    # Encerramento por stop programático (ex.: limite de sucessos)
    if controller.should_stop:
        for t_name, t_obj in (("HBR", thread_hbr), ("HAC", thread_hac)):
            if t_obj and t_obj.is_alive():
                while t_obj.is_alive():
                    try:
                        t_obj.join(timeout=0.5)
                    except KeyboardInterrupt:
                        log_info(f"Interrupção adicional recebida durante join de {t_name}.")
                        break
        log_info("Loop finalizado por condição de parada.")


if __name__ == "__main__":
    main()
