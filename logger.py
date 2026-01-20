"""
LOGGER v2 — PADRÃO OPERACIONAL

INSTRUÇÕES IMPORTANTES (PARA HUMANOS E IA):

1. OBJETIVO
   Este módulo implementa um logger leve, determinístico e extensível,
   adequado para automações, CLIs, bots, pipelines e serviços.

2. PRINCÍPIOS DE PROJETO
   - NÃO monkey-patch stdout/stderr por padrão.
   - Separação clara entre:
       a) saída de terminal (colorida),
       b) arquivo de log (limpo),
       c) GUI ou callback externo (opcional).
   - Arquivo de log deve ser aberto UMA única vez.
   - Falhas no logger NUNCA devem ser silenciosas.
   - Logger não deve depender de JSON, BD ou infraestrutura externa.

3. NÍVEIS DE LOG
   - Níveis são hierárquicos e comparáveis.
   - LOG_LEVEL controla a verbosidade global.
   - Níveis customizados (OK, FINALIZADO, SKIP) fazem parte do domínio.

4. STDOUT / STDERR
   - stdout: fluxo normal e informativo.
   - stderr: erros reais.
   - Tee (duplicação stdout/stderr → arquivo) só deve ser ativado
     explicitamente via enable_stdout_tee().

5. USO RECOMENDADO
   - Cada projeto deve configurar seu próprio arquivo de log.
   - Logs estruturais/auditoria devem ir para arquivo ou BD externo.
   - Logs de debug interativo podem usar tee.

6. EXTENSÃO FUTURA (PERMITIDO)
   - Handler para banco de dados.
   - Inclusão de contexto (job_id, accession_number, request_id).
   - Integração com collectors (rsyslog, Loki, etc).

7. EXTENSÃO NÃO PERMITIDA
   - Monkey-patch implícito de sys.stdout/sys.stderr.
   - Captura silenciosa de exceções.
   - Escrita em /tmp como destino principal.
"""
"""
PACOTE: corerad_logging

Este arquivo (logs.py) faz parte do pacote Python `corerad_logging`,
instalado via pip (preferencialmente em modo editável: pip install -e .).

USO CORRETO:
    from corerad_logging.logs import log_info, log_erro, set_logfile, set_level

NÃO COPIAR ESTE ARQUIVO PARA OUTROS PROJETOS.
NÃO CRIAR VARIANTES LOCAIS.
QUALQUER ALTERAÇÃO DEVE SER FEITA NO REPOSITÓRIO DO PACOTE.

VERSÃO:
    - Versionamento semântico (SemVer).
    - Alterações compatíveis: v0.x.y
    - Quebras de API: v1.0.0+

ESCOPO:
    - Logger base compartilhado entre múltiplos projetos.
    - Não depende de JSON, BD ou frameworks externos.
    - Projetado para automações, CLIs, serviços e pipelines.

FONTE DA VERDADE:
    Repositório Git: github.com/rod-americo/corerad_logging
"""

from datetime import datetime
import sys
import os
from typing import Optional, Callable, TextIO

# =========================
# Níveis de log (ordinais)
# =========================

LEVELS = {
    "DEBUG": 10,
    "SKIP": 15,
    "INFO": 20,
    "OK": 25,
    "AVISO": 30,
    "ERRO": 40,
    "FINALIZADO": 50,
}

LOG_LEVEL = LEVELS.get(os.getenv("LOG_LEVEL", "INFO").upper(), 20)

# =========================
# Cores ANSI
# =========================

if os.environ.get("NO_COLOR"):
    COLORS = {k: "" for k in LEVELS} | {"RESET": ""}
else:
    COLORS = {
        "DEBUG": "\033[35m",
        "INFO": "\033[37m",
        "OK": "\033[32m",
        "AVISO": "\033[33m",
        "ERRO": "\033[91m",
        "FINALIZADO": "\033[96m",
        "SKIP": "\033[90m",
        "RESET": "\033[0m",
    }

# =========================
# Estado interno
# =========================

_logfile_handle: Optional[TextIO] = None
_gui_callback: Optional[Callable[[str, str, str], None]] = None
_tee_enabled = False

_original_stdout = sys.stdout
_original_stderr = sys.stderr

# =========================
# Tee opcional
# =========================

class TeeStream:
    def __init__(self, stream: TextIO):
        self.stream = stream
        self.encoding = getattr(stream, "encoding", "utf-8")

    def write(self, msg: str):
        self.stream.write(msg)
        self.stream.flush()
        if _logfile_handle:
            _logfile_handle.write(msg)

    def flush(self):
        self.stream.flush()
        if _logfile_handle:
            _logfile_handle.flush()

    def isatty(self):
        return self.stream.isatty()

# =========================
# Configuração pública
# =========================

def set_level(level: str):
    global LOG_LEVEL
    LOG_LEVEL = LEVELS.get(level.upper(), LOG_LEVEL)

def set_logfile(path: str):
    global _logfile_handle
    _logfile_handle = open(path, "a", encoding="utf-8", buffering=1)

def enable_stdout_tee():
    global _tee_enabled
    if not _tee_enabled:
        sys.stdout = TeeStream(_original_stdout)
        sys.stderr = TeeStream(_original_stderr)
        _tee_enabled = True

def set_gui_callback(func: Callable[[str, str, str], None]):
    global _gui_callback
    _gui_callback = func

def close_logger():
    global _logfile_handle
    if _logfile_handle:
        _logfile_handle.close()
        _logfile_handle = None

# =========================
# Core do logger
# =========================

def log(msg: str, tipo: str = "INFO"):
    tipo = tipo.upper()
    level = LEVELS.get(tipo, LEVELS["INFO"])

    if level < LOG_LEVEL:
        return

    ts = datetime.now().strftime("%H:%M:%S")
    color = COLORS.get(tipo, "")
    reset = COLORS["RESET"]

    stream = _original_stderr if tipo == "ERRO" else _original_stdout

    # Terminal
    print(f"[{ts}] {color}[{tipo}] {msg}{reset}", file=stream, flush=True)

    # GUI
    if _gui_callback:
        try:
            _gui_callback(ts, tipo, msg)
        except Exception as e:
            _original_stderr.write(f"[LOGGER][GUI ERROR] {e}\n")

    # Arquivo
    if _logfile_handle:
        try:
            _logfile_handle.write(f"[{ts}] [{tipo}] {msg}\n")
        except Exception as e:
            _original_stderr.write(f"[LOGGER][FILE ERROR] {e}\n")

# =========================
# Wrappers semânticos
# =========================

def log_debug(msg):      log(msg, "DEBUG")
def log_info(msg):       log(msg, "INFO")
def log_ok(msg):         log(msg, "OK")
def log_aviso(msg):      log(msg, "AVISO")
def log_erro(msg):       log(msg, "ERRO")
def log_finalizado(msg): log(msg, "FINALIZADO")
def log_skip(msg):       log(msg, "SKIP")