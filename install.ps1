# ============================================================
# Instalação de Python + dependências + Playwright (Windows)
# venv: $HOME\.nox
# ============================================================

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (Get-Command "python" -ErrorAction SilentlyContinue) {
    Write-Host "[OK] Python já está instalado."
}
else {
    Write-Host "[INFO] Python não detectado. Iniciando instalação via Winget..."
    # Tenta instalar Python 3.12 especificamente para evitar ambiguidades com versão 3 genérica
    # Remove restrição de source que pode falhar em algumas configs
    winget install 9NQ7512CXL7T
}

Write-Host "[INFO] Ajustando PATH do Python..."
# Tenta recarregar variaveis de ambiente sem reiniciar shell
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

$python = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
if (-not $python) {
    $python = (Get-Command py.exe -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    Write-Host "[AVISO] Python não encontrado no PATH atual. Talvez seja necessário reiniciar o terminal." -ForegroundColor Yellow
}
else {
    Write-Host "[OK] Python encontrado: $python"
}

$user_home = $env:USERPROFILE
$venv = Join-Path $user_home ".nox"

if (-not (Test-Path $venv)) {
    Write-Host "[INFO] Criando venv em $venv ..."
    python -m venv $venv
}
else {
    Write-Host "[INFO] Venv já existe em $venv"
}

Write-Host "[INFO] Ativando venv..."
$activate = Join-Path $venv "Scripts\Activate.ps1"
if (Test-Path $activate) {
    . $activate
}
else {
    Write-Host "[ERRO] Venv corrompido ou erro na criação." -ForegroundColor Red
    exit 1
}

Write-Host "[INFO] Atualizando pip..."
python -m pip install --upgrade pip

Write-Host "[INFO] Instalando dependências (Nox)..."
# pydicom: manipulação DICOM, flet: GUI, requests: WADO, playwright: Auth, tqdm: Progresso
pip install flet requests playwright tqdm pydicom

Write-Host "[INFO] Instalando navegadores do Playwright..."
playwright install chromium

Write-Host "[OK] Instalação concluída com sucesso."
Write-Host "Para iniciar: . $venv\Scripts\Activate.ps1; python nox.py" -ForegroundColor Cyan