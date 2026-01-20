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
from playwright.sync_api import sync_playwright, TimeoutError

import config
from logger import log_info, log_ok, log_erro, set_logfile


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
        # ABRIR WORKLIST
        # -------------------------------------------------------
        log_info("Abrindo Worklist")
        page.goto(config.URL_WORKLIST)
        page.wait_for_load_state("networkidle")

        botao_menu = "Cenários"

        # -------------------------------------------------------
        # LOOP DE CENÁRIOS
        # -------------------------------------------------------
        for nome in cenarios:
            log_info(f"Processando cenário: {nome}")

            captured_payload = None

            # interceptador
            def intercept(req):
                nonlocal captured_payload
                if "/worklist/listar" in req.url and req.method == "POST":
                    try:
                        captured_payload = req.post_data_json
                    except Exception:
                        pass

            page.on("request", intercept)

            # abrir menu
            try:
                page.get_by_role("button", name=botao_menu).click()
                page.wait_for_selector("#scene_content")
            except Exception:
                log_erro(f"Falha ao abrir menu '{botao_menu}'")
                continue

            # clicar cenário
            try:
                log_info(f"Clicando em '{nome}' (repr={repr(nome)})")
                page.get_by_text(nome, exact=False).click()
            except Exception as e:
                log_erro(f"Cenário '{nome}' (repr={repr(nome)}) não encontrado na interface (Click falhou): {e}")
                continue

            try:
                page.wait_for_load_state("networkidle")
            except Exception as e:
                log_aviso(f"Timeout/Erro aguardando carregamento após clicar em '{nome}': {e}")
                # Não faz continue, tenta capturar payload assim mesmo

            # aguardar payload
            for _ in range(60):
                if captured_payload:
                    break
                page.wait_for_timeout(100)

            if not captured_payload:
                log_erro(f"Não foi possível capturar payload do cenário {nome}")
                continue

            # salvar
            outfile = config.DATA_DIR / f"payload_{nome}.json"
            outfile.write_text(
                json.dumps(captured_payload, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            log_ok(f"Payload salvo: {outfile}")

            botao_menu = nome

        log_ok("Captura concluída.")



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
    if len(sys.argv) < 2:
        print("uso: python prepare.py <CENÁRIO_1> ... OU --mapear-cenarios")
        sys.exit(1)

    if "--mapear-cenarios" in sys.argv:
        set_logfile(config.LOG_DIR / "prepare.log")
        mapear_cenarios()
    else:
        set_logfile(config.LOG_DIR / "prepare.log")
        cenarios = [c.strip() for c in sys.argv[1:]]
        preparar(cenarios)


if __name__ == "__main__":
    main()