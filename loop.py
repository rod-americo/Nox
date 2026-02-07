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
from datetime import datetime

import config
from logger import log_info, log_erro
import fetcher
import downloader


# ============================================================
# Controller (Interface para GUI)
# ============================================================

class LoopController:
    def __init__(self):
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set() # Inicialmente rodando (não pausado)
    
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
                sucessos += 1
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
            except: pass


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
                    except: pass


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
    opt_group.add_argument("-h", "--help", action="help", help="Mostra esta mensagem de ajuda e sai")
    
    # Se chamado via GUI, args podem vir vazios ou customizados
    if "args" in kwargs:
        args = parser.parse_args(kwargs["args"])
    else:
        args = parser.parse_args()
    
    # Override config se flag presente
    if args.metadado:
        config.SAVE_METADATA = True
    
    # Seta delay global para threads
    global DOWNLOAD_DELAY
    if args.delay > 0:
        DOWNLOAD_DELAY = args.delay
        log_info(f"Delay entre downloads: {DOWNLOAD_DELAY}s")
    
    # Prioridade: 1. Argumentos CLI, 2. config.SCENARIOS
    if args.cenarios:
        final_scenarios = []
        for item in args.cenarios:
            # 1. Se for arquivo ou caminho existente, usa direto
            if item.lower().endswith(".json") or os.path.exists(item):
                final_scenarios.append(item)
            else:
                # 2. Se for nome simples, mapeia para data/payload_{NAME}.json
                # (Assumindo que prepare.py gerou este arquivo anteriormente)
                p = config.DATA_DIR / f"payload_{item}.json"
                final_scenarios.append(str(p))
        
        cenarios = final_scenarios
    else:
        # Sem argumentos: usa config.SCENARIOS e converte para queries/*.json
        from pathlib import Path
        queries_dir = Path("queries").resolve()
        cenarios = []
        
        for scenario_name in config.SCENARIOS:
            # Remove extensão .json se já tiver
            clean_name = scenario_name.replace(".json", "")
            # Constrói caminho completo
            json_path = queries_dir / f"{clean_name}.json"
            cenarios.append(str(json_path))

    log_info("=== ORQUESTRADOR NOX ===")
    log_info(f"Cenários: {', '.join(cenarios)}")
    log_info(f"Intervalo de check: {config.LOOP_INTERVAL}s")

    # Limpeza inicial
    limpar_antigos()

    # 1) PREPARE (Executa apenas uma vez)
    # 1) PREPARE (Executa apenas uma vez)
    if not args.no_prepare:
        try:
            # Invoca prepare.py como subprocesso para isolar contexto (Playwright/Async)
            # NÃO passa argumentos para prepare.py (apenas login/sessão), 
            # pois loop agora trabalha com Arquivos JSON já prontos.
            cmd = [sys.executable, "prepare.py"]
            log_info(f"Executando processo de preparação (Login)...")
            
            subprocess.run(cmd, check=True)
            
        except subprocess.CalledProcessError as e:
            log_erro(f"Falha fatal no prepare.py (Exit Code {e.returncode})")
            # Se tiver controller (GUI), raise para ser tratado
            if 'controller' in kwargs: 
                raise RuntimeError(f"Prepare falhou com código {e.returncode}")
            sys.exit(1)
        except Exception as e:
            log_erro(f"Erro ao invocar prepare.py: {e}")
            if 'controller' in kwargs:
                raise e
            sys.exit(1)
    else:
        log_info("Prepare pulado (--no-prepare).")

    # Variáveis de controle de Threads
    thread_hbr = None
    thread_hac = None
    ultimo_check = 0
    INTERVALO = config.LOOP_INTERVAL

    # 2) LOOP INFINITO
    controller = kwargs.get("controller") or LoopController()

    try:
        while not controller.should_stop:
            # Check de pause
            if not controller.wait_if_paused():
                break
            # Verificar se threads estão rodando
            hbr_ativo = thread_hbr and thread_hbr.is_alive()
            hac_ativo = thread_hac and thread_hac.is_alive()

            if hbr_ativo or hac_ativo:
                # Se ocupado, aguarda brevemente e checa de novo
                # status = []
                # if hbr_ativo: status.append("HBR: Baixando")
                # if hac_ativo: status.append("HAC: Baixando")
                time.sleep(5)
                continue

            # --- SE CHEGOU AQUI, ESTÁ LIVRE ---
            
            # Controle de tempo: respeitar intervalo mínimo entre checks
            agora = time.time()
            decorrido = agora - ultimo_check
            if decorrido < INTERVALO:
                espera_total = int(INTERVALO - decorrido)
                if espera_total > 0:
                    # Contagem regressiva visual
                    for i in range(espera_total, 0, -1):
                        if controller.should_stop:
                            break
                        try:
                            sys.stdout.write(f"\rAguardando {i}s para próximo ciclo...   ")
                            sys.stdout.flush()
                        except (IOError, AttributeError):
                            pass
                        time.sleep(1)
                    
                    # Se parou, limpa e sai
                    try:
                        sys.stdout.write("\r" + " " * 40 + "\r")  
                        sys.stdout.flush()
                    except (IOError, AttributeError):
                        pass
            
            # Correção para garantir que o tempo bateu
            if (time.time() - ultimo_check) < INTERVALO:
                 pass # Já esperamos o suficiente no loop acima

            ultimo_check = time.time()
            
            log_info("--- Novo ciclo de verificação ---")
            
            # Manutenção de espaço
            verificar_retencao_exames()
            
            # 1. Fetch
            try:
                # Agora usa fetch por ARQUIVOS, não mais por nomes de cenários
                dados = fetcher.fetch_varios_arquivos(cenarios)
            except Exception as e:
                log_erro(f"Erro no fetcher: {e}")
                log_info(f"Aguardando {config.LOOP_INTERVAL}s antes de tentar novamente...")
                time.sleep(config.LOOP_INTERVAL)
                continue

            lista_hbr = dados.get("HBR", [])
            lista_hac = dados.get("HAC", [])

            if not lista_hbr and not lista_hac:
                log_info(f"Nenhum exame encontrado.")
                continue

            log_info(f"Fila para processar: HBR={len(lista_hbr)}, HAC={len(lista_hac)}")

            # 2. Disparar Threads
            if lista_hbr:
                thread_hbr = threading.Thread(target=worker_download, args=("HBR", lista_hbr, controller))
                thread_hbr.start()

            if lista_hac:
                thread_hac = threading.Thread(target=worker_download, args=("HAC", lista_hac, controller))
                thread_hac.start()
            
            # Volta para o início do while, onde cairá no 'hbr_ativo or hac_ativo' e ficará esperando

    except KeyboardInterrupt:
        log_info("\nInterrupção (Ctrl+C). Parando threads...")
        controller.stop()
        
        # Aguardar encerramento gracioso
        if thread_hbr and thread_hbr.is_alive():
            thread_hbr.join()
        if thread_hac and thread_hac.is_alive():
            thread_hac.join()
        log_info("Shutdown completo.")


if __name__ == "__main__":
    main()
