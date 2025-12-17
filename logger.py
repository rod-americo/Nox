# logger.py

from datetime import datetime
import sys

# ====== Configuração ======

DEBUG_MODE = False

# ====== Cores ANSI ======
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

# ====== Callback para GUI ======
_gui_callback = None

def set_gui_callback(func):
    """Registra uma função(msg, tipo) para receber logs na GUI."""
    global _gui_callback
    _gui_callback = func

# ====== Função principal ======
def log(msg: str, tipo: str = "INFO") -> None:
    tipo = tipo.upper()
    if tipo == "DEBUG" and not DEBUG_MODE:
        return

    ts = datetime.now().strftime("%H:%M:%S")
    cor_msg = _COLORS.get(tipo, "")
    cor_ts = _COLORS["RESET"]
    reset = _COLORS["RESET"]
    
    stream = sys.stderr if tipo == "ERRO" else sys.stdout

    # 1. Terminal
    # Formato: [HH:MM:SS] [TIPO] Mensagem
    print(f"{cor_ts}[{ts}] {cor_msg}[{tipo}] {msg}{reset}", file=stream, flush=True)

    # 2. GUI (se houver)
    if _gui_callback:
        # Envia limpo ou formatado? Melhor enviar dados crus e GUI formata se quiser.
        # Mas para simplificar o widget de texto, vamos enviar a string formatada sem cor ANSI ou algo intermediário.
        # Vamos enviar (ts, tipo, msg)
        try:
            _gui_callback(ts, tipo, msg)
        except Exception:
            pass  # Evitar crash se GUI falhar

# ====== Wrappers por nível ======
def log_debug(msg: str):       log(msg, "DEBUG")
def log_info(msg: str):        log(msg, "INFO")
def log_ok(msg: str):          log(msg, "OK")
def log_aviso(msg: str):       log(msg, "AVISO")
def log_erro(msg: str):        log(msg, "ERRO")
def log_finalizado(msg: str):  log(msg, "FINALIZADO")
def log_skip(msg: str):        log(msg, "SKIP")
def log_ignorado(msg: str):    log(msg, "DEBUG")