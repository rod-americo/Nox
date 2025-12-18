#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
loop.py — Orquestrador Cockpit (Versão Nativa/Threaded)
-------------------------------------------------------

Fluxo:
1. (Opcional) Executa prepare.py UMA VEZ.
2. Loop contínuo:
   • Verifica se há threads de download ativas.
     - Se sim: aguarda (blocking).
     - Se não: executa fetcher.
   • Com novos dados, dispara Threads separadas para HBR e HAC.
   • Cada Thread chama downloader.baixar_an() sequencialmente para sua lista.
"""

import sys
import os
import time
import shutil
import argparse
import threading
from datetime import datetime

import config
from logger import log_info, log_erro
import prepare
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

def worker_download(servidor: str, lista_ans: list, controller=None):
    """
    Processa uma lista de ANs sequencialmente.
    Chamado em Thread separada.
    """
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


def verificar_retencao_exames():
    """
    Mantém apenas os N exames mais recentes.
    - Persistent: Baseado nas pastas em RADIANT_DICOM_DIR.
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

    # --- MODO PERSISTENT (Gerenciamento por Pastas + JSON Sync) ---
    base = config.RADIANT_DICOM_DIR
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


# ============================================================
# Main Loop
# ============================================================

def main(**kwargs):
    parser = argparse.ArgumentParser()
    parser.add_argument("cenarios", nargs="*", help="Cenários Cockpit (ex.: MONITOR MONITOR_RX)")
    parser.add_argument("--no-prepare", action="store_true", help="Pular etapa de preparação")
    
    # Se chamado via GUI, args podem vir vazios ou customizados
    if "args" in kwargs:
        args = parser.parse_args(kwargs["args"])
    else:
        # Se sys.argv estiver vazio de argumentos úteis (ex: rodando via import), default para MONITOR
        # Se sys.argv estiver vazio de argumentos úteis (ex: rodando via import), não força MONITOR aqui
        # Deixa o parser rodar vazio e pegamos do config abaixo
        pass
        args = parser.parse_args()
    
    # Prioridade: 1. Argumentos CLI | 2. Config.ini | 3. Hardcoded MONITOR
    cenarios = args.cenarios or config.SCENARIOS or ["MONITOR"]

    log_info("=== ORQUESTRADOR NOX ===")
    log_info(f"Cenários: {', '.join(cenarios)}")
    log_info(f"Intervalo de check: {config.LOOP_INTERVAL}s")

    # Limpeza inicial
    limpar_antigos()

    # 1) PREPARE (Executa apenas uma vez)
    if not args.no_prepare:
        try:
            prepare.preparar(cenarios)
        except Exception as e:
            log_erro(f"Falha fatal no prepare.py: {e}")
            log_erro(f"Falha fatal no prepare.py: {e}")
            # Se tiver controller (GUI), não dar sys.exit total, apenas raise para o loop pegar
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
                dados = fetcher.fetch_varios(cenarios)
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