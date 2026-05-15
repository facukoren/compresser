@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python no esta instalado. Abriendo descarga...
    start https://www.python.org/downloads/
    echo.
    echo Instala Python 3.10+ marcando "Add Python to PATH" y volve a correr este archivo.
    pause
    exit /b 1
)

if not exist ".venv" (
    echo Creando entorno virtual...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

echo Instalando dependencias...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt

echo.
echo Iniciando Compresser...
pythonw compresser.py
