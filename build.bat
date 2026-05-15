@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv" (
    echo [!] No hay entorno virtual. Corriendo setup.bat primero...
    call setup.bat
    if errorlevel 1 exit /b 1
)

call .venv\Scripts\activate.bat

echo [1/4] Instalando PyInstaller...
python -m pip install -q pyinstaller

echo [2/4] Verificando ffmpeg para embeber...
if not exist "ffmpeg\ffmpeg.exe" (
    echo [!] No hay ffmpeg en la carpeta ffmpeg\
    echo     Corré la app una vez con setup.bat para que lo descargue,
    echo     o pegalo manualmente.
    pause
    exit /b 1
)
if not exist "ffmpeg\ffprobe.exe" (
    echo [!] No hay ffprobe en la carpeta ffmpeg\
    pause
    exit /b 1
)

echo [3/4] Limpiando builds anteriores...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist Compresser.spec del /q Compresser.spec

echo [4/4] Empaquetando Compresser.exe con ffmpeg embebido (3-5 min)...
pyinstaller ^
  --onefile ^
  --windowed ^
  --name Compresser ^
  --collect-data customtkinter ^
  --collect-data tkinterdnd2 ^
  --hidden-import pynvml ^
  --hidden-import psutil ^
  --hidden-import winotify ^
  --collect-data winotify ^
  --add-binary "ffmpeg\ffmpeg.exe;ffmpeg" ^
  --add-binary "ffmpeg\ffprobe.exe;ffmpeg" ^
  --noconfirm ^
  --log-level WARN ^
  compresser.py

if errorlevel 1 (
    echo.
    echo [X] Falló el empaquetado.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Listo: dist\Compresser.exe
echo ============================================================
echo  - Archivo unico, ~170 MB con ffmpeg embebido.
echo  - Funciona OFFLINE en cualquier Windows 10/11 64-bit.
echo  - Cero descargas, cero setup, cero internet.
echo  - Doble click y va.
echo ============================================================
pause
