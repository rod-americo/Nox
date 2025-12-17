#!/bin/bash

# ==========================================
# Instalação ambiente macOS (Nox)
# Homebrew + Python 3 + Dependências
# venv: ~/.nox
# ==========================================

set -e

# --- 1. Checar Homebrew ---
if ! command -v brew &> /dev/null; then
    echo "[AVISO] Homebrew não encontrado. Iniciando instalação..."
    echo "[INFO] Você pode precisar digitar sua senha de sudo."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # Tenta adicionar ao path na sessão atual se for Apple Silicon
    if [ -f "/opt/homebrew/bin/brew" ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    echo "[OK] Homebrew já instalado."
fi

# --- 2. Checar Python 3 ---
if ! command -v python3 &> /dev/null; then
    echo "[INFO] Instalando Python 3 via brew..."
    brew install python
else
    echo "[OK] Python 3 encontrado."
fi

# --- 3. Virtual Environment ---
# Usa diretorio fixo ~/.nox para padronizar com scripts de start
VENV_DIR="$HOME/.nox"

if [ ! -d "$VENV_DIR" ]; then
    echo "[INFO] Criando venv em $VENV_DIR..."
    python3 -m venv "$VENV_DIR"
else
    echo "[OK] Venv já existe em $VENV_DIR"
fi

# --- 4. Instalar Pacotes ---
echo "[INFO] Ativando venv e instalando dependências..."
source "$VENV_DIR/bin/activate"

pip install --upgrade pip
# Dependências essenciais do Nox
pip install flet requests playwright tqdm pydicom

# --- 5. Playwright ---
echo "[INFO] Instalando navegadores do Playwright..."
playwright install chromium

echo ""
echo "[OK] Instalação concluída!"
echo "Para iniciar: ./nox.sh  (ou 'python nox.py' dentro do venv)"