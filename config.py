#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py — Configuration Loader
----------------------------------------

Este módulo gerencia as configurações do Nox/RadiAnt Assistant.
As configurações são carregadas prioritariamente do arquivo `config.ini`.

OPÇÕES DO ARQUIVO DE CONFIGURAÇÃO (config.ini):

[HBR] / [HAC]
  server    : IP ou Hostname do servidor WADO (Ex: 10.36.254.61)
  port      : Porta do serviço WADO (Default: 1000)
  path      : Caminho do serviço (Default: WADO/AETILE)

[OPERATIONAL SYSTEM]
  system    : Sistema operacional em uso. Opções: 'windows', 'linux', 'macos'.
              Define qual diretório DICOM será usado (radiant_dicom ou linux_dicom).

[PATHS]
  persistent_dir  : (Opcional) Diretório local de saída para modos persistent/pipeline.
                    Se definido, tem prioridade sobre radiant_dicom/linux_dicom.
  radiant_dicom   : Diretório onde os exames baixados serão salvos (Persistent) ou montados (Transient).
                    Ex: C:\\DICOM (Windows) ou /Users/user/DICOM (Mac)
  linux_dicom     : Diretório de saída para ambientes Linux headless. Pode ser caminho absoluto ou
                    relativo ao script. Default: data/DICOM. Usado como fallback quando radiant_dicom
                    não for acessível (ex: path Windows em servidor Linux).
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
  storage_mode  : (Opcional) Estratégia de entrega: 'transient', 'persistent' ou 'pipeline'.
  save_metadata : (Opcional) true/false para exportar metadados DICOM. ('metadado' legado ainda funciona)
  scenarios     : Lista de cenários do Cockpit para monitorar. Ex: "MONITOR MONITOR_RX".

[PIPELINE]
  enabled       : Ativa/desativa envio HTTP do modo pipeline.
  api_url       : Endpoint de envio do payload.
  api_token     : Token Bearer opcional.
  timeout       : Timeout de request (segundos). Default: 30.
  strict        : Se true, falha o AN quando o envio da API falhar.

[AUTH]
  user : Usuário do Cockpit.
  pass : Senha do Cockpit.

CONSTANTES INTERNAS:
  SERVERS           : Dicionário com configurações de HBR e HAC.
  DOWNLOAD_WORKERS  : Mapeia [SETTINGS] threads.
  RADIANT_DICOM_DIR : Path object de [PATHS] radiant_dicom (Windows).
  LINUX_DICOM_DIR   : Path object de [PATHS] linux_dicom (Linux/macOS).
  OUTPUT_DICOM_DIR  : Diretório efetivo usado (auto-detectado por SO).
  STORAGE_MODE      : Modo de armazenamento efetivo.
  VERSION           : Versão atual do aplicativo.
"""

import sys
import os
import platform
import configparser
from pathlib import Path
from prompt_translation import THORAX_XRAY_TRANSLATION_PROMPT

# === Versão do Aplicativo ===
VERSION = "2.1.0"

# === Caminho base e arquivo INI ===
BASE_DIR = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.ini"

# === Leitura do config.ini ===
parser = configparser.ConfigParser()
if not CONFIG_FILE.exists():
    print(f"[AVISO] Arquivo {CONFIG_FILE} não encontrado. Usando defaults.")

parser.read(CONFIG_FILE, encoding="utf-8")

def get(section: str, key: str, default=None):
    """
    Lê valor de configuração do arquivo INI com fallback seguro.
    
    Args:
        section: Nome da seção no config.ini (ex: 'PATHS', 'SETTINGS')
        key: Chave da configuração dentro da seção
        default: Valor padrão caso a chave não exista
    
    Returns:
        str: Valor da configuração ou default se não encontrado
    """
    return parser.get(section, key, fallback=default)

def getint(section: str, key: str, default=None):
    """
    Lê valor inteiro de configuração do arquivo INI com fallback seguro.
    
    Args:
        section: Nome da seção no config.ini (ex: 'SETTINGS')
        key: Chave da configuração dentro da seção
        default: Valor padrão caso a chave não exista ou não seja inteiro válido
    
    Returns:
        int: Valor da configuração convertido para inteiro ou default
    """
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

# Diretório para payloads e demais dados da API Cockpit
DATA_DIR = BASE_DIR / "data"

# Diretório para artefatos de autenticação
AUTH_DIR = BASE_DIR / "auth"

# Diretório para metadados individuais do Cockpit (subjson)
COCKPIT_METADATA_DIR = DATA_DIR / "cockpit"

# Garantir estrutura
for d in [TMP_DIR, PROGRESS_DIR, DATA_DIR, AUTH_DIR, LOG_DIR, COCKPIT_METADATA_DIR]:
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
# Diretórios de Saída DICOM
# ============================================================

# Legacy paths mantidos por compatibilidade
RADIANT_DICOM_DIR = Path(get("PATHS", "radiant_dicom", r"C:\\DICOM"))
_linux_dicom_raw = get("PATHS", "linux_dicom", "data/DICOM")
LINUX_DICOM_DIR = Path(_linux_dicom_raw) if Path(_linux_dicom_raw).is_absolute() else BASE_DIR / _linux_dicom_raw

# Lê configuração de SO do INI (mantido para fallback legado)
SYSTEM_CONFIG = get("OPERATIONAL SYSTEM", "system", "windows").lower()

# Novo path explícito (preferencial): evita depender de SO para decisão de destino
_persistent_raw = get("PATHS", "persistent_dir", "").strip()
if not _persistent_raw:
    _persistent_raw = get("PATHS", "output_dicom", "").strip()

if _persistent_raw:
    OUTPUT_DICOM_DIR = Path(_persistent_raw) if Path(_persistent_raw).is_absolute() else BASE_DIR / _persistent_raw
elif SYSTEM_CONFIG == "windows":
    OUTPUT_DICOM_DIR = RADIANT_DICOM_DIR
else:
    OUTPUT_DICOM_DIR = LINUX_DICOM_DIR

# OsiriX Incoming:
# - Preferência por configuração explícita por plataforma (legado)
# - Fallback para a outra chave, evitando dependência rígida de SO
if platform.system() == "Windows":
    _incoming_path = get("PATHS", "osirix_incoming_mapped", "").strip()
    if not _incoming_path:
        _incoming_path = get("PATHS", "osirix_incoming", "").strip()
else:
    _incoming_path = get("PATHS", "osirix_incoming", "").strip()
    if not _incoming_path:
        _incoming_path = get("PATHS", "osirix_incoming_mapped", "").strip()

OSIRIX_INCOMING = Path(_incoming_path) if _incoming_path else Path("")

RADIANT_EXE = get("PATHS", "radiant_exe", r"C:\Program Files\RadiAntViewer\RadiAntViewer.exe")


# ============================================================
# Configurações da API Cockpit (Hardcoded / Code-only)
# ============================================================

URL_BASE = "https://cockpitweb.redeimpar.com.br:17000"

URL_LOGIN      = f"{URL_BASE}/ris/laudo/user/login"
URL_WORKLIST   = f"{URL_BASE}/ris/laudo/app/worklist"

SESSION_FILE        = AUTH_DIR / "session.json"
LOCALSTORAGE_FILE   = AUTH_DIR / "localstorage_monitor.json"



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
_save_metadata = get("SETTINGS", "save_metadata", "")
if _save_metadata == "":
    _save_metadata = get("SETTINGS", "metadado", "false")
SAVE_METADATA = _save_metadata.lower() == "true"

# Criar diretório de saída DICOM (OUTPUT_DICOM_DIR já foi definido com detecção de SO)
OUTPUT_DICOM_DIR.mkdir(parents=True, exist_ok=True)


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

# Detecção de Storage Mode (Persistent vs Transient vs Pipeline)
# - Persistent: Mantém arquivos DICOM em disco (ideal para RadiAnt/Windows)
# - Transient: Move arquivos para OsiriX Incoming e remove temporários (ideal para OsiriX/macOS)
# - Pipeline: Mantém arquivos + metadados para integração com API externa
_storage_conf = get("SETTINGS", "storage_mode", "").lower()
if _storage_conf in ["transient", "persistent", "pipeline"]:
    # Usuário especificou explicitamente no config.ini
    STORAGE_MODE = _storage_conf
else:
    # Detecção automática baseada no visualizador configurado
    # OsiriX/Horos: Usa Transient (move para Incoming, não mantém cópia local)
    # RadiAnt: Usa Persistent (mantém arquivos em RADIANT_DICOM_DIR)
    if SYSTEM_CONFIG == "linux":
        # No Linux, sempre usamos Persistent (linux_dicom) para evitar bypass do path configurado
        STORAGE_MODE = "persistent"
    elif VIEWER in ["osirix", "horos"]:
        STORAGE_MODE = "transient"
    else:
        STORAGE_MODE = "persistent"

# Pipeline sempre exige metadados para empacotamento/integração
if STORAGE_MODE == "pipeline":
    SAVE_METADATA = True

# Configuração de integração de pipeline (envio externo)
PIPELINE_ENABLED = get("PIPELINE", "enabled", "true").lower() == "true"
PIPELINE_API_URL = get("PIPELINE", "api_url", "").strip()
PIPELINE_API_TOKEN = get("PIPELINE", "api_token", "").strip()
PIPELINE_TIMEOUT = getint("PIPELINE", "timeout", 30)
PIPELINE_STRICT = get("PIPELINE", "strict", "false").lower() == "true"
PIPELINE_REQUEST_FORMAT = get("PIPELINE", "request_format", "json").strip().lower()
PIPELINE_PROMPT = get("PIPELINE", "prompt", "").strip() or THORAX_XRAY_TRANSLATION_PROMPT
PIPELINE_AUTO_WRITE_REPORT = get("PIPELINE", "auto_write_report", "true").lower() == "true"
PIPELINE_USE_REVISAR = get("PIPELINE", "use_revisar", "false").lower() == "true"
_pipeline_default_medico_id = get("PIPELINE", "default_medico_id", "").strip()
if not _pipeline_default_medico_id:
    _pipeline_default_medico_id = str(os.environ.get("MEDICO_EXECUTANTE_ID", "")).strip()
if not _pipeline_default_medico_id:
    _pipeline_default_medico_id = "165111"
PIPELINE_DEFAULT_MEDICO_ID = int(_pipeline_default_medico_id) if _pipeline_default_medico_id.isdigit() else 165111

# Flag para screenshots de debug (Playwright)
DEBUG_SCREENSHOTS = False  # Ative para salvar screenshots de debug em data/debug/

# === Compatibilidade com scripts antigos que importam SERVER/WADO_PORT direto ===
SERVER    = SERVERS["HBR"]["server"]
WADO_PORT = SERVERS["HBR"]["wado_port"]
WADO_PATH = SERVERS["HBR"]["wado_path"]

# === Diagnóstico ===
if __name__ == "__main__":
    print(f"--- Configuração Carregada ---")
    print(f"INI File:         {CONFIG_FILE}")
    print(f"Sistema Config:   {SYSTEM_CONFIG}")
    print(f"HBR:              {SERVERS['HBR']['server']}")
    print(f"HAC:              {SERVERS['HAC']['server']}")
    print(f"Web Base URL:     {URL_BASE}")
    print(f"DICOM Output:     {OUTPUT_DICOM_DIR}")
    print(f"Storage Mode:     {STORAGE_MODE}")
    print(f"Save Metadata:    {SAVE_METADATA}")
    print(f"Threads:          {DOWNLOAD_WORKERS}")
    print(f"User:             {USUARIO}")
