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
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

from datetime import datetime, timedelta
import config
from logger import log_info, log_ok, log_erro, log_debug, set_logfile
from fetcher import SCENARIO_RULES, gerar_payload


USERNAME_SELECTORS = [
    "#user",
    "#username",
    "input[name='user']",
    "input[name='username']",
    "input[type='email']",
    "input[autocomplete='username']",
    "input[type='text']",
]

PASSWORD_SELECTORS = [
    "#password",
    "input[name='password']",
    "input[type='password']",
    "input[autocomplete='current-password']",
]

SUBMIT_SELECTORS = [
    "#login-submit",
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Entrar')",
    "button:has-text('Login')",
]


def _find_visible_locator_in_page_or_frames(page, selectors: list[str], timeout_ms: int = 60_000):
    deadline = time.monotonic() + (timeout_ms / 1000)
    last_error = None

    while time.monotonic() < deadline:
        search_scopes = [page, *[f for f in page.frames if f != page.main_frame]]
        for scope in search_scopes:
            for sel in selectors:
                try:
                    loc = scope.locator(sel).first
                    if loc.count() > 0 and loc.is_visible(timeout=250):
                        return loc, sel
                except Exception as e:
                    last_error = e
                    continue
        page.wait_for_timeout(400)

    if last_error:
        log_debug(f"Nenhum seletor visível encontrado. Último erro: {last_error}")
    return None, None


def _click_submit_with_fallback(page):
    btn, sel = _find_visible_locator_in_page_or_frames(page, SUBMIT_SELECTORS, timeout_ms=10_000)
    if btn:
        btn.click()
        return sel

    page.keyboard.press("Enter")
    return "keyboard:Enter"


def _page_diag(page) -> str:
    try:
        title = page.title()
    except Exception:
        title = "<indisponível>"
    return f"url={page.url} | title={title}"


def fazer_login(page, take_screenshot=None):
    log_info("Abrindo tela de login")
    page.goto(config.URL_LOGIN, timeout=90_000, wait_until="domcontentloaded")
    try:
        page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass

    if take_screenshot:
        take_screenshot(page, "1_login_page")

    user_input, user_selector = _find_visible_locator_in_page_or_frames(page, USERNAME_SELECTORS, timeout_ms=90_000)
    if not user_input:
        raise RuntimeError(
            "Campo de usuário não encontrado após carregar login. "
            f"Diag: {_page_diag(page)}"
        )

    user_input.fill(config.USUARIO)
    log_debug(f"Campo de usuário preenchido com seletor: {user_selector}")
    if take_screenshot:
        take_screenshot(page, "2_user_filled")

    submit_user_selector = _click_submit_with_fallback(page)
    log_debug(f"Ação de submit após usuário: {submit_user_selector}")

    password_input, password_selector = _find_visible_locator_in_page_or_frames(page, PASSWORD_SELECTORS, timeout_ms=45_000)
    if not password_input:
        raise RuntimeError(
            "Campo de senha não encontrado após envio do usuário. "
            f"Diag: {_page_diag(page)}"
        )

    password_input.fill(config.SENHA)
    log_debug(f"Campo de senha preenchido com seletor: {password_selector}")
    if take_screenshot:
        take_screenshot(page, "3_pass_filled")

    submit_pass_selector = _click_submit_with_fallback(page)
    log_debug(f"Ação de submit após senha: {submit_pass_selector}")

    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:
        # Alguns ambientes mantêm conexões longas; não bloquear por isso.
        pass

    log_ok("Login concluído")
    if take_screenshot:
        take_screenshot(page, "4_login_success")


# ============================================================
# Função principal
# ============================================================

def preparar(cenarios: list[str]):
    log_info("Iniciando etapa preparatória (login + sessão + payloads)")

    debug_dir = config.TMP_DIR
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
            fazer_login(page, take_screenshot=take_screenshot)
        except Exception as e:
            # Em caso de erro, força screenshot mesmo se config for False
            if not config.DEBUG_SCREENSHOTS:
                try:
                    p_path = debug_dir / "error_login_failure.png"
                    page.screenshot(path=p_path)
                    log_info(f"Screenshot de erro salvo: {p_path}")
                except Exception as s_e:
                    log_erro(f"Erro ao salvar screenshot de falha: {s_e}")
            raise

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

            c_norm = str(c).strip().upper()
            rule = SCENARIO_RULES.get(c_norm)
            if not rule:
                log_erro(f"Cenário desconhecido: {c}. Ignorando.")
                continue
            
            try:
                payload = gerar_payload(dt_ini, dt_fim, rule)
                outfile = data_dir / f"payload_{c_norm}.json"
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
            fazer_login(page)
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
        fazer_login(page)

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
    
    parser = argparse.ArgumentParser(description="Ferramenta de Preparação e Login (Cockpit)", add_help=False)
    
    # Grupos
    arg_group = parser.add_argument_group("Argumentos")
    opt_group = parser.add_argument_group("Opções")
    
    arg_group.add_argument("cenarios", nargs="*", help="Lista de cenários ou arquivos JSON (para geração de payload legado)")
    
    opt_group.add_argument("--mapear-cenarios", action="store_true", help="Faz login e lista todos os cenários disponíveis no site")
    opt_group.add_argument("--login-only", action="store_true", help="Executa apenas autenticação (sem geração de payload)")
    opt_group.add_argument("-h", "--help", action="help", help="Mostra esta mensagem de ajuda e sai")
    
    args = parser.parse_args()

    if args.mapear_cenarios:
        mapear_cenarios()
    elif args.login_only:
        preparar([])
    else:
        # Se não houver cenários, prepara com lista vazia (apenas login)
        preparar(args.cenarios)


if __name__ == "__main__":
    main()
