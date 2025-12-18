#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
transfer_scenarios.py

Permite copiar (clonar) cenários do mesmo usuário ou transferir para outro usuário.

Uso:
    python transfer_scenarios.py --cenario "NOME" [--novo-nome "NOVO_NOME"] [--target-user "..."]
"""

import sys
import json
import argparse
import time
from playwright.sync_api import sync_playwright

import config
from logger import log_info, log_ok, log_erro, log_aviso

# ==============================================================================
# CLASSES / FUNÇÕES DE APOIO
# ==============================================================================

class CockpitSession:
    def __init__(self, headless=True):
        self.headless = headless
        self.playwright = None
        self.browser = None
        self.page = None
        self.token = None
        self.headers = {}

    def start(self):
        self.playwright = sync_playwright().start()
        # Adicionando argumento para ignorar erros de certificado no nível do browser
        self.browser = self.playwright.chromium.launch(
            headless=self.headless, 
            args=["--ignore-certificate-errors"]
        )
        # Criando contexto com ignore_https_errors explicito
        context = self.browser.new_context(ignore_https_errors=True)
        self.page = context.new_page()

    def stop(self):
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def login(self, user, password):
        log_info(f"Logando usuário: {user}")
        page = self.page
        page.goto(config.URL_LOGIN, timeout=60_000)

        page.fill("#user", user)
        page.click("#login-submit")
        
        try:
            page.wait_for_selector("#password", timeout=5000)
        except Exception:
            # As vezes o fluxo muda ou já redireciona?
            pass

        if page.is_visible("#password"):
            page.fill("#password", password)
            page.click("#login-submit")
        
        try:
            page.wait_for_load_state("networkidle", timeout=20000)
        except:
            pass
        
        # Validar se logou capturando token
        log_info("Extraindo token...")
        try:
            raw_ls = page.evaluate("() => ({...localStorage})")
            key = "laudo-remoto_current_user"
            if key not in raw_ls:
                # Tenta esperar um pouco mais
                time.sleep(2)
                raw_ls = page.evaluate("() => ({...localStorage})")
            
            if key in raw_ls:
                data = json.loads(raw_ls[key])
                self.token = data.get("token")
                if self.token:
                    self.headers = {
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/plain, */*"
                    }
                    log_ok(f"Token obtido com sucesso.")
                    return True
        except Exception as e:
            log_erro(f"Erro ao extrair token: {e}")
        
        log_erro("Login falhou ou token não encontrado.")
        return False

    def get_cenarios(self):
        """Lista todos os cenários via API"""
        log_info("Buscando lista de cenários...")
        url = f"{config.URL_BASE}/ris/laudo/api/v1/cenario/listar"
        
        try:
            # Envia objeto vazio para garantir que o server entenda como JSON body
            response = self.page.request.post(url, headers=self.headers, data={})
            if response.status == 200:
                data = response.json()
                log_info(f"Total de cenários encontrados: {len(data)}")
                return data
            else:
                log_erro(f"Erro ao listar cenários: {response.status} {response.status_text()}")
                log_erro(f"Body: {response.text()}")
                return []
        except Exception as e:
            log_erro(f"Exception em get_cenarios: {e}")
            return []

    def create_cenario(self, payload):
        """Cria um novo cenário via API"""
        log_info(f"Criando cenário: {payload.get('nm_cenario')}")
        url = f"{config.URL_BASE}/ris/laudo/api/v1/cenario/inserir"
        
        try:
            response = self.page.request.post(url, headers=self.headers, data=payload)
            
            if response.status in [200, 201, 204]:
                try:
                    resp_json = response.json()
                    log_ok("Cenário criado com sucesso!")
                    return resp_json
                except Exception:
                    log_ok(f"Cenário criado com sucesso! (Status {response.status})")
                    return {"status": "ok", "raw": response.text()}
            else:
                log_erro(f"Erro ao criar cenário: {response.status} {response.status_text}")
                log_erro(response.text())
                return None
        except Exception as e:
            log_erro(f"Exception em create_cenario: {e}")
            return None

# ==============================================================================
# MAIN
# ==============================================================================

def transferir(source_user, source_pass, target_user, target_pass, cenario_name, new_name=None):
    
    # --- PASSO 1: LOGIN ORIGEM ---
    session_src = CockpitSession(headless=True)
    try:
        session_src.start()
        if not session_src.login(source_user, source_pass):
            return
        
        scenarios = session_src.get_cenarios()
        target_scenario = next((c for c in scenarios if c["nm_cenario"] == cenario_name), None)
        
        if not target_scenario:
            log_erro(f"Cenário '{cenario_name}' não encontrado na conta de origem.")
            log_info("Cenários disponíveis:")
            for c in scenarios:
                print(f" - {c['nm_cenario']}")
            return

        log_ok(f"Cenário '{cenario_name}' encontrado. Preparando payload...")
        
        # Preparar Payload
        # Removemos ID e alteramos o nome
        payload = {
            "cd_color": target_scenario.get("cd_color", ""),
            "tp_favorito": "N", # Resetar favorito
            "ds_permissao": 0,
            "nm_cenario": new_name if new_name else (cenario_name if source_user.lower() != target_user.lower() else f"{cenario_name} (CPY)"),
            "id_grupo": 0,
            "sn_publico": "N", # Forçar privado
            "sn_avanco_automatico": target_scenario.get("sn_avanco_automatico", "N"),
            "colunas": target_scenario.get("colunas", []),
            "filtros": target_scenario.get("filtros", {})
        }
        
    finally:
        session_src.stop()

    # --- PASSO 2: LOGIN DESTINO ---
    # Se usuario/senha forem iguais, poderíamos reusar sessão, mas para segurança do código
    # vamos sempre fazer novo login (garante estado limpo se for trocar de user).
    
    log_info(f"Iniciando transferência para usuário: {target_user}")
    
    session_dst = CockpitSession(headless=True)
    try:
        session_dst.start()
        if not session_dst.login(target_user, target_pass):
            return
        
        # Verificar se já existe com mesmo nome para evitar duplicação confusa?
        # A API deve permitir nomes duplicados ou retornar erro. Vamos tentar criar direto.
        
        session_dst.create_cenario(payload)
        
    finally:
        session_dst.stop()


def main():
    parser = argparse.ArgumentParser(description="Transferir ou Clonar Cenários do Cockpit")
    parser.add_argument("--cenario", required=True, help="Nome exato do cenário a ser copiado")
    parser.add_argument("--novo-nome", help="Novo nome para o cenário (opcional)")
    
    parser.add_argument("--source-user", help="Usuário de origem")
    parser.add_argument("--source-pass", help="Senha de origem")
    
    parser.add_argument("--target-user", help="Usuário de destino")
    parser.add_argument("--target-pass", help="Senha de destino")
    
    args = parser.parse_args()

    # Fallback para Config se não informado
    s_user = args.source_user if args.source_user else config.USUARIO
    s_pass = args.source_pass if args.source_pass else config.SENHA
    
    # Se target não informado, assume o mesmo do source (Clone/Backup)
    t_user = args.target_user if args.target_user else s_user
    t_pass = args.target_pass if args.target_pass else s_pass
    
    if not s_user or not s_pass:
        log_erro("Credenciais de origem não encontradas (argumentos ou config.ini).")
        return

    transferir(s_user, s_pass, t_user, t_pass, args.cenario, args.novo_nome)

if __name__ == "__main__":
    main()
