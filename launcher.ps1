<#
.SYNOPSIS
    Launcher Auto-Update para NOX
.DESCRIPTION
    1. Atualiza o código via Git.
    2. Ativa o ambiente virtual.
    3. Inicia o Nox.
#>

Write-Host "--- NOX LAUNCHER ---" -ForegroundColor Cyan
Write-Host "Verificando atualizações..."

# Tenta atualizar
try {
    git pull origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Não foi possível atualizar o código. Verifique sua internet ou credenciais do Git."
        Write-Warning "Iniciando versão atual..."
    } else {
        Write-Host "Código atualizado com sucesso!" -ForegroundColor Green
    }
} catch {
    Write-Warning "Git não encontrado ou erro de execução."
}

# Caminho do VENV (Ajuste se necessário)
$VenvPath = "$env:USERPROFILE\.nox\Scripts\Activate.ps1"

if (Test-Path $VenvPath) {
    Write-Host "Ativando ambiente virtual..."
    . $VenvPath
} else {
    Write-Warning "Ambiente virtual não encontrado em $VenvPath"
    Write-Warning "Tentando rodar com Python do sistema..."
}

# Inicia o App
Write-Host "Iniciando Nox..." -ForegroundColor Cyan
python nox.py

# Mantém janela aberta se der erro
if ($LASTEXITCODE -ne 0) {
    Read-Host "Pressione ENTER para sair..."
}
