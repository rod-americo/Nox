#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
radiant/config.py — Configuration Loader
----------------------------------------

Este módulo gerencia as configurações do Nox/RadiAnt Assistant.
As configurações são carregadas prioritariamente do arquivo `config.ini`.

OPÇÕES DO ARQUIVO DE CONFIGURAÇÃO (config.ini):

[HBR] / [HAC]
  server    : IP ou Hostname do servidor WADO (Ex: 10.36.254.61)
  port      : Porta do serviço WADO (Default: 1000)
  path      : Caminho do serviço (Default: WADO/AETILE)

[PATHS]
  radiant_dicom   : Diretório onde os exames baixados serão salvos (Persistent) ou montados (Transient).
                    Ex: C:\\DICOM (Windows) ou /Users/user/DICOM (Mac)
  osirix_incoming : (Opcional) Diretório de entrada ("Incoming") do OsiriX/Horos.
                    Se definido e STORAGE_MODE=transient, os arquivos são movidos para cá.
  radiant_exe     : Caminho do executável do RadiAnt (para abertura automática).

[SETTINGS]
  threads       : Número de downloads simultâneos (Workers). Default: 15.
  retries       : Tentativas de download por imagem antes de falhar. Default: 4.
  retry_wait    : Tempo de espera (em segundos) entre tentativas. Default: 30.
  loop_interval : Intervalo (segundos) entre verificações automáticas no modo Monitor. Default: 150.
  theme         : Tema da interface gráfica. Opções: 'dark', 'light' ou 'system'. Default: 'dark'.
  max_exames    : Quantidade máxima de exames mantidos no histórico/disco. Default: 50.
  viewer        : Visualizador preferencial. Opções: 'radiant', 'osirix', 'horos'.
                  Afeta o STORAGE_MODE default (Transient para OsiriX, Persistent para RadiAnt).
  scenarios     : Lista de cenários do Cockpit para monitorar. Ex: "MONITOR MONITOR_RX".

[AUTH]
  user : Usuário do Cockpit.
  pass : Senha do Cockpit.

CONSTANTES INTERNAS:
  SERVERS          : Dicionário com configurações de HBR e HAC.
  DOWNLOAD_WORKERS : Mapeia [SETTINGS] threads.
  RADIANT_DICOM_DIR: Path object de [PATHS] radiant_dicom.
  STORAGE_MODE     : Modo de armazenamento efetivo.
  VERSION          : Versão atual do aplicativo.
"""

import sys
import os
import platform
import configparser
from pathlib import Path

# === Versão do Aplicativo ===
VERSION = "1.0.5"

# === Caminho base e arquivo INI ===
BASE_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.ini"

# === Leitura do config.ini ===
parser = configparser.ConfigParser()
if not CONFIG_FILE.exists():
    print(f"[AVISO] Arquivo {CONFIG_FILE} não encontrado. Usando defaults.")

parser.read(CONFIG_FILE, encoding="utf-8")

def get(section: str, key: str, default=None):
    """Leitura segura com fallback."""
    return parser.get(section, key, fallback=default)

def getint(section: str, key: str, default=None):
    """Leitura segura de inteiro com fallback."""
    return parser.getint(section, key, fallback=default)

# ============================================================
# Caminhos principais (Internos do Script)
# ============================================================

# Diretório de downloads temporários
TMP_DIR = BASE_DIR / "tmp"

# Diretório de progresso (.json por AN)
PROGRESS_DIR = BASE_DIR / "progresso"

# Diretório de logs
LOG_DIR = BASE_DIR / "logs"

# Diretório para payloads, sessão e demais dados da API Cockpit
DATA_DIR = BASE_DIR / "data"

# Garantir estrutura
for d in [TMP_DIR, PROGRESS_DIR, DATA_DIR, LOG_DIR]:
    d.mkdir(parents=True, exist_ok=True)


# ============================================================
# Configurações WADO (Lidas do INI)
# ============================================================

SERVERS = {
    "HBR": {
        "server":    get("HBR", "server", "wado-hbr.redeimpar.com.br"),
        "wado_port": get("HBR", "port", "1000"),
        "wado_path": get("HBR", "path", "WADO/AETILE"),
    },
    "HAC": {
        "server":    get("HAC", "server", "wado-hac.redeimpar.com.br"),
        "wado_port": get("HAC", "port", "1000"),
        "wado_path": get("HAC", "path", "WADO/AETILE"),
    }
}

# Quantidade de workers paralelos (ThreadPoolExecutor)
DOWNLOAD_WORKERS = int(get("SETTINGS", "threads", "15"))

# Retry
MAX_RETRY    = int(get("SETTINGS", "retries", "4"))
RETRY_ESPERA = int(get("SETTINGS", "retry_wait", "30"))


# ============================================================
# Configurações Externas (Paths do INI)
# ============================================================

# Onde salvar os DICOMs (C:\DICOM ou Network Share)
RADIANT_DICOM_DIR = Path(get("PATHS", "radiant_dicom", r"C:\DICOM")) 


# OS-dependent OsiriX Incoming
if platform.system() == "Windows":
    _incoming_path = get("PATHS", "osirix_incoming_mapped", "")
else:
    _incoming_path = get("PATHS", "osirix_incoming", "")

OSIRIX_INCOMING = Path(_incoming_path) if _incoming_path else Path("")

RADIANT_EXE = get("PATHS", "radiant_exe", r"C:\Program Files\RadiAntViewer\RadiAntViewer.exe")


# ============================================================
# Configurações da API Cockpit (Hardcoded / Code-only)
# ============================================================

URL_BASE = "https://cockpitweb.redeimpar.com.br:17000"

URL_LOGIN      = f"{URL_BASE}/ris/laudo/user/login"
URL_WORKLIST   = f"{URL_BASE}/ris/laudo/app/worklist"

SESSION_FILE        = DATA_DIR / "session.json"
LOCALSTORAGE_FILE   = DATA_DIR / "localstorage_monitor.json"



# ============================================================
# Credenciais
# ============================================================

# Tenta ler do INI primeiro
USUARIO = get("AUTH", "user", "")
SENHA   = get("AUTH", "pass", "")

# Se não estiver no INI, tenta var de ambiente (compatibilidade)
if not USUARIO:
    USUARIO = os.environ.get("USUARIO", "")
if not SENHA:
    SENHA = os.environ.get("SENHA", "")

if not USUARIO or not SENHA:
    print("[AVISO] Credenciais (USUARIO/SENHA) não encontradas em config.ini [AUTH] ou .env")


# ============================================================
# Loop / Monitoramento
# ============================================================

LOOP_INTERVAL = getint("SETTINGS", "loop_interval", 150)
MAX_EXAMES    = getint("SETTINGS", "max_exames", 50)
SLIDER_MAX    = getint("SETTINGS", "slider_max", 200) # Limite superior do slider na GUI
_raw_theme = get("SETTINGS", "theme", "dark").lower()
THEME = "light" if _raw_theme == "light" else "dark"
VIEWER        = get("SETTINGS", "viewer", "radiant").lower()
_viewer_display = "OsiriX" if VIEWER == "osirix" else "RadiAnt"
TITLE         = f"Assistente :: {_viewer_display} :: Mezo"

if VIEWER == "radiant":
    RADIANT_DICOM_DIR.mkdir(parents=True, exist_ok=True)

# Parse de cenários robusto (suporta JSON, vírgula ou espaço)
_raw_scenarios = get("SETTINGS", "scenarios", "MONITOR")
try:
    import json
    SCENARIOS = json.loads(_raw_scenarios)
    if not isinstance(SCENARIOS, list):
         # Se for string JSON válida mas não lista, força erro para cair no except
         raise ValueError
except:
    # Fallback: Vírgula ou Espaço
    if "," in _raw_scenarios:
        SCENARIOS = [s.strip().strip('"\'') for s in _raw_scenarios.split(",") if s.strip()]
    else:
        SCENARIOS = [s.strip().strip('"\'') for s in _raw_scenarios.split()]

_storage_conf = get("SETTINGS", "storage_mode", "").lower()
if _storage_conf in ["transient", "persistent"]:
    STORAGE_MODE = _storage_conf
else:
    # Default Inteligente
    if VIEWER in ["osirix", "horos"]:
        STORAGE_MODE = "transient"
    else:
        STORAGE_MODE = "persistent"
DEBUG_SCREENSHOTS = False  # Ative para salvar screenshots de debug em data/debug/

# === Compatibilidade com scripts antigos que importam SERVER/WADO_PORT direto ===
SERVER    = SERVERS["HBR"]["server"]
WADO_PORT = SERVERS["HBR"]["wado_port"]
WADO_PATH = SERVERS["HBR"]["wado_path"]

# === Diagnóstico ===
if __name__ == "__main__":
    print(f"--- Configuração Carregada ---")
    print(f"INI File:      {CONFIG_FILE}")
    print(f"HBR:           {SERVERS['HBR']['server']}")
    print(f"HAC:           {SERVERS['HAC']['server']}")
    print(f"Web Base URL:  {URL_BASE}")
    print(f"DICOM Output:  {RADIANT_DICOM_DIR}")
    print(f"Threads:       {DOWNLOAD_WORKERS}")
    print(f"User:          {USUARIO}")