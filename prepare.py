#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
prepare.py — etapa preparatória do pipeline Cockpit
---------------------------------------------------
Funções:
1. Realiza login com Playwright.
2. Extrai token JWT e cookies.
3. Salva session.json e localstorage.json.
4. Carrega cenários solicitados e salva payloads correspondentes.

Uso:
    python prepare.py MONITOR MONITOR_RX
"""

import json
import sys
from pathlib import Path
import re
from playwright.sync_api import sync_playwright, TimeoutError

from datetime import datetime, timedelta
import config
from logger import log_info, log_ok, log_erro, log_debug, set_logfile
from fetcher import SCENARIO_RULES, gerar_payload


# ============================================================
# Função principal
# ============================================================

def preparar(cenarios: list[str]):
    log_info("Iniciando etapa preparatória (login + sessão + payloads)")

    debug_dir = config.LOG_DIR
    debug_dir.mkdir(parents=True, exist_ok=True)

    def take_screenshot(p, name):
        if not config.DEBUG_SCREENSHOTS:
            return
        try:
            path = debug_dir / f"{name}.png"
            p.screenshot(path=path)
            log_info(f"Screenshot salvo: {path}")
        except Exception as e:
            log_erro(f"Erro ao salvar screenshot {name}: {e}")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-ipv6"]
        )
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        # -------------------------------------------------------
        # LOGIN
        # -------------------------------------------------------
        try:
            log_info("Abrindo tela de login")
            page.goto(config.URL_LOGIN, timeout=60_000)
            take_screenshot(page, "1_login_page")

            page.fill("#user", config.USUARIO)
            take_screenshot(page, "2_user_filled")
            page.click("#login-submit")

            page.wait_for_selector("#password")
            page.fill("#password", config.SENHA)
            take_screenshot(page, "3_pass_filled")
            page.click("#login-submit")
            
            page.wait_for_load_state("networkidle")
            log_ok("Login concluído")
            take_screenshot(page, "4_login_success")
        except Exception as e:
            # Em caso de erro, força screenshot mesmo se config for False
            if not config.DEBUG_SCREENSHOTS:
                try:
                    p_path = debug_dir / "error_login_failure.png"
                    page.screenshot(path=p_path)
                    log_info(f"Screenshot de erro salvo: {p_path}")
                except Exception as s_e:
                    log_erro(f"Erro ao salvar screenshot de falha: {s_e}")
            raise e

        # -------------------------------------------------------
        # TOKEN
        # -------------------------------------------------------
        log_info("Extraindo token JWT e localStorage")
        
        key = "laudo-remoto_current_user"
        found = False
        raw_ls = {}

        # Retry logic: Aguardar token persistir no localStorage
        for _ in range(20): # 10 segundos
            raw_ls = page.evaluate("() => ({...localStorage})")
            if key in raw_ls:
                found = True
                break
            page.wait_for_timeout(500)

        if not found:
             # Tenta uma última vez ou loga o que tem
             raw_ls = page.evaluate("() => ({...localStorage})")
        
        # Gravar localStorage (mesmo se falhar, para debug)
        config.LOCALSTORAGE_FILE.write_text(
            json.dumps(raw_ls, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        log_ok(f"localStorage salvo em {config.LOCALSTORAGE_FILE}")

        if key not in raw_ls:
            raise RuntimeError("Token não encontrado no localStorage (Timeout aguardando persistência).")

        token = json.loads(raw_ls[key]).get("token")
        if not token:
            raise RuntimeError("Token JWT não encontrado ou inválido")

        # -------------------------------------------------------
        # SALVAR SESSÃO
        # -------------------------------------------------------
        session = {
            "cookies": page.context.cookies(),
            "headers": {
                "User-Agent": page.evaluate("navigator.userAgent"),
                "Authorization": f"Bearer {token}",
            }
        }

        config.SESSION_FILE.write_text(
            json.dumps(session, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        log_ok(f"Sessão salva em {config.SESSION_FILE}")

        # -------------------------------------------------------
        # ABRIR WORKLIST (Não é mais necessário para login, apenas se quiséssemos validar acesso)
        # -------------------------------------------------------
        # log_info("Abrindo Worklist")
        # page.goto(config.URL_WORKLIST)
        # page.wait_for_load_state("networkidle")

        log_ok("Autenticação renovada com sucesso (Sessão + LocalStorage).")

        # -------------------------------------------------------
        # DADOS / PAYLOADS
        # -------------------------------------------------------
        if not cenarios:
             log_info("Nenhum cenário solicitado. Apenas login realizado.")
             return

        log_info(f"Gerando payloads para: {cenarios}")
        
        # Datas Padrão (D-1 a D0)
        agora = datetime.now()
        ontem = agora - timedelta(days=1)
        dt_ini = ontem.strftime("%Y-%m-%d")
        dt_fim = agora.strftime("%Y-%m-%d")

        data_dir = config.DATA_DIR
        data_dir.mkdir(parents=True, exist_ok=True)

        for c in cenarios:
            # Se for arquivo JSON, ignora geração de payload (assume que já existe)
            if c.lower().endswith(".json") or Path(c).exists():
                log_info(f"Argumento '{Path(c).name}' identificado como arquivo. Pulando geração de payload (Legacy).")
                continue

            rule = SCENARIO_RULES.get(c)
            if not rule:
                log_erro(f"Cenário desconhecido: {c}. Ignorando.")
                continue
            
            try:
                payload = gerar_payload(dt_ini, dt_fim, rule)
                outfile = data_dir / f"payload_{c}.json"
                outfile.write_text(
                    json.dumps(payload, indent=2, ensure_ascii=False),
                    encoding="utf-8"
                )
                log_ok(f"Payload salvo: {outfile}")
            except Exception as e:
                log_erro(f"Erro ao gerar payload {c}: {e}")




def listar_cenarios() -> list[str]:
    """
    Realiza login e retorna lista de nomes de cenários disponíveis.
    """
    log_info("Listando cenários disponíveis...")
    cenarios_encontrados = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-ipv6"]
        )
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        try:
            page.goto(config.URL_LOGIN, timeout=60_000)
            page.fill("#user", config.USUARIO)
            page.click("#login-submit")
            page.wait_for_selector("#password")
            page.fill("#password", config.SENHA)
            page.click("#login-submit")
            try:
                # Login wait
                page.wait_for_load_state("networkidle", timeout=30000)
            except: pass
            
            # Navegar para Worklist se já não estiver lá (login as vezes redireciona para home)
            if config.URL_WORKLIST not in page.url:
                page.goto(config.URL_WORKLIST)
                try:
                    page.wait_for_load_state("networkidle", timeout=30000)
                except: pass

            # -------------------------------------------------------
            # ACESSAR LISTA
            # -------------------------------------------------------
            page.goto(config.URL_WORKLIST)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except: pass

            try:
                # Clica no botão Cenários (Lógica idêntica ao mapear_cenarios que funcionou)
                page.get_by_role("button", name="Cenários").click()
                page.wait_for_selector("#scene_content", timeout=10000)
                page.wait_for_timeout(1500) # Wait for animation/hydration
                
                # Coleta todos os textos
                raw_items = page.locator("#scene_content li").all_inner_texts()
                
                # Filtragem de "sujeira" (headers, dividers, 'Destaques')
                ignorar = ["Destaques", "Editar Destaques", "Fav", "Cenários", "Ações", "Origem", "Nativos"]
                
                for item in raw_items:
                    texto = item.strip()
                    if texto and texto not in ignorar:
                        cenarios_encontrados.append(texto)
                        
            except Exception as e:
                log_erro(f"Erro ao acessar menu: {e}")

        except Exception as e:
            log_erro(f"Erro no processo de listagem: {e}")
        finally:
            browser.close()
            
    return sorted(list(set(cenarios_encontrados)))
# ============================================================
# MAPPING
# ============================================================

def mapear_cenarios():
    log_info("Iniciando mapeamento de cenários...")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-ipv6"]
        )
        context = browser.new_context(ignore_https_errors=True)
        page = context.new_page()

        # -------------------------------------------------------
        # LOGIN (Simplificado)
        # -------------------------------------------------------
        log_info("Efetuando login para listar cenários")
        page.goto(config.URL_LOGIN, timeout=60_000)

        page.fill("#user", config.USUARIO)
        page.click("#login-submit")

        page.wait_for_selector("#password")
        page.fill("#password", config.SENHA)
        page.click("#login-submit")
        page.wait_for_load_state("networkidle")
        log_ok("Login concluído")

        # -------------------------------------------------------
        # ACESSAR LISTA
        # -------------------------------------------------------
        log_info("Abrindo Worklist para capturar lista")
        page.goto(config.URL_WORKLIST)
        page.wait_for_load_state("networkidle")

        botao_menu = "Cenários"
        try:
            page.get_by_role("button", name=botao_menu).click()
            page.wait_for_selector("#scene_content")
            # Pequena pausa para garantir renderização da lista
            page.wait_for_timeout(1000)
        except Exception as e:
            log_erro(f"Falha ao abrir menu de cenários: {e}")
            return

        # Tenta capturar itens da lista. 
        # Estrutura provável: #scene_content > ul > li ou similar.
        # Vamos pegar todo o texto por segurança e separar por linhas,
        # ou tentar seletores comuns.
        
        # Tentativa 1: Elementos 'li'
        itens = page.locator("#scene_content li").all_inner_texts()
        
        if not itens:
            # Tentativa 2: Textos diretos se não houver li
            content_text = page.locator("#scene_content").inner_text()
            if content_text:
                itens = [line.strip() for line in content_text.split('\n') if line.strip()]

        if not itens:
            log_erro("Nenhum cenário encontrado ou estrutura desconhecida.")
            return

        # Forçar saída UTF-8 no stdout para captura correta
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except:
            pass

        print("\n--- CENÁRIOS DISPONÍVEIS ---")
        for item in itens:
            print(f"- {item}")
        print("----------------------------\n")
        
        log_ok(f"Mapeamento finalizado. {len(itens)} cenários encontrados.")



# ============================================================
# CLI
# ============================================================

def main():
    import argparse # Garantir import local caso não tenha no topo
    set_logfile(config.LOG_DIR / "prepare.log")
    
    parser = argparse.ArgumentParser(description="Ferramenta de Preparação e Login (Cockpit)", add_help=False)
    
    # Grupos
    arg_group = parser.add_argument_group("Argumentos")
    opt_group = parser.add_argument_group("Opções")
    
    arg_group.add_argument("cenarios", nargs="*", help="Lista de cenários ou arquivos JSON (para geração de payload legado)")
    
    opt_group.add_argument("--mapear-cenarios", action="store_true", help="Faz login e lista todos os cenários disponíveis no site")
    opt_group.add_argument("-h", "--help", action="help", help="Mostra esta mensagem de ajuda e sai")
    
    args = parser.parse_args()

    if args.mapear_cenarios:
        mapear_cenarios()
    else:
        # Se não houver cenários, prepara com lista vazia (apenas login)
        preparar(args.cenarios)


if __name__ == "__main__":
    main()