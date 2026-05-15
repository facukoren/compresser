@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv" (
    call setup.bat
    exit /b
)
call .venv\Scripts\activate.bat
start "" pythonw compresser.py
