@echo off
cd /d "%~dp0"

REM Tenta usar o pythonw do venv local (Windows) em .nox
if exist "%USERPROFILE%\.nox\Scripts\pythonw.exe" (
    start "" "%USERPROFILE%\.nox\Scripts\pythonw.exe" nox.py
) else (
    REM Fallback para pythonw do sistema ou .venv antigo
    if exist ".venv\Scripts\pythonw.exe" (
         start "" ".venv\Scripts\pythonw.exe" nox.py
    ) else (
         start "" pythonw nox.py
    )
)
exit
