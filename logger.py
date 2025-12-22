# logger.py

from datetime import datetime
import sys

# ====== Configuração ======

DEBUG_MODE = False

# ====== Cores ANSI ======
# ====== Cores ANSI ======
import os

if os.environ.get("NO_COLOR"):
    _COLORS = {
        "DEBUG": "", "INFO": "", "OK": "", "AVISO": "", "ERRO": "",
        "FINALIZADO": "", "SKIP": "", "RESET": ""
    }
else:
    _COLORS = {
        "DEBUG": "\033[35m",        # Roxo
        "INFO": "\033[37m",         # Branco
        "OK": "\033[32m",           # Verde
        "AVISO": "\033[33m",        # Amarelo
        "ERRO": "\033[91m",         # Vermelho
        "FINALIZADO": "\033[96m",   # Ciano claro
        "SKIP": "\033[90m",         # Cinza
        "RESET": "\033[0m"
    }

# ====== Redirecionamento de Stdout/Stderr ======

# ====== Redirecionamento de Stdout/Stderr ======

_original_stdout = sys.stdout
_original_stderr = sys.stderr
_redirection_active = False
_logfile = None
_gui_callback = None

class StreamTee:
    """Classe auxiliar para duplicar saída para o arquivo de log."""
    def __init__(self, stream, is_stderr=False):
        self.stream = stream
        self.is_stderr = is_stderr
        self.encoding = getattr(stream, 'encoding', 'utf-8')

    def write(self, message):
        # 1. Escreve no stream original (tela)
        self.stream.write(message)
        self.stream.flush()

        # 2. Escreve no arquivo de log (se configurado)
        if _logfile:
            try:
                # Se for mensagem crua (não formatada pelo log()), grava direto
                # Tentamos evitar duplicidade se log() já escreveu.
                # Mas log() escreve direto no arquivo, então se log() usar _original_stdout,
                # não passará por aqui.
                # Se for um print() solto, passa por aqui.
                with open(_logfile, "a", encoding="utf-8") as f:
                    f.write(message)
            except:
                pass

    def flush(self):
        self.stream.flush()
        if _logfile:
            try:
                with open(_logfile, "a", encoding="utf-8") as f:
                    f.flush()
            except:
                pass
    
    def isatty(self):
        return self.stream.isatty()

def set_gui_callback(func):
    """Registra uma função(msg, tipo) para receber logs na GUI."""
    global _gui_callback
    _gui_callback = func

def set_logfile(path):
    """Define arquivo de destino e ativa redirecionamento de stdout/stderr."""
    global _logfile, _redirection_active
    _logfile = path
    
    if not _redirection_active:
        sys.stdout = StreamTee(_original_stdout)
        sys.stderr = StreamTee(_original_stderr, is_stderr=True)
        _redirection_active = True

# ====== Função principal ======
def log(msg: str, tipo: str = "INFO") -> None:
    tipo = tipo.upper()
    if tipo == "DEBUG" and not DEBUG_MODE:
        return

    ts = datetime.now().strftime("%H:%M:%S")
    cor_msg = _COLORS.get(tipo, "")
    cor_ts = _COLORS["RESET"]
    reset = _COLORS["RESET"]
    
    # Usa os streams ORIGINAIS para evitar passar pelo StreamTee (e duplicar no arquivo)
    stream = _original_stderr if tipo == "ERRO" else _original_stdout

    # 1. Terminal (Colorido)
    print(f"{cor_ts}[{ts}] {cor_msg}[{tipo}] {msg}{reset}", file=stream, flush=True)

    # 2. GUI (se houver)
    if _gui_callback:
        try:
            _gui_callback(ts, tipo, msg)
        except Exception:
            pass

    # 3. Arquivo (Limpo)
    if _logfile:
        try:
            with open(_logfile, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] [{tipo}] {msg}\n")
        except Exception:
            pass

# ====== Wrappers por nível ======
def log_debug(msg: str):       log(msg, "DEBUG")
def log_info(msg: str):        log(msg, "INFO")
def log_ok(msg: str):          log(msg, "OK")
def log_aviso(msg: str):       log(msg, "AVISO")
def log_erro(msg: str):        log(msg, "ERRO")
def log_finalizado(msg: str):  log(msg, "FINALIZADO")
def log_skip(msg: str):        log(msg, "SKIP")
def log_ignorado(msg: str):    log(msg, "DEBUG")