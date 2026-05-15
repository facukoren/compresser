@echo off
setlocal
cd /d "%~dp0"

set "EXE=%~dp0Compresser.exe"
if not exist "%EXE%" (
    echo [X] No encuentro Compresser.exe en esta carpeta.
    echo     Pone este .bat al lado del .exe y volve a correrlo.
    pause
    exit /b 1
)

set "EXTENSIONS=.mp4 .mkv .mov .avi .webm .flv .wmv .m4v .ts .mpg .mpeg .m2ts"

echo Registrando menu contextual para Compresser...
echo Ruta del exe: %EXE%
echo.

for %%E in (%EXTENSIONS%) do call :register "%%E"

echo.
echo ============================================================
echo  Listo. Hace click derecho en cualquier video y vas a ver:
echo     "Comprimir con Compresser"
echo.
echo  En Windows 11, puede aparecer dentro de
echo  "Mostrar mas opciones" (menu clasico, Shift+F10).
echo.
echo  Si moves el .exe a otra carpeta, volve a correr este .bat.
echo ============================================================
pause
exit /b 0

:register
set "EXT=%~1"
reg add "HKCU\Software\Classes\SystemFileAssociations\%EXT%\shell\Compresser" /ve /d "Comprimir con Compresser" /f >nul
reg add "HKCU\Software\Classes\SystemFileAssociations\%EXT%\shell\Compresser" /v "Icon" /d "%EXE%,0" /f >nul
reg add "HKCU\Software\Classes\SystemFileAssociations\%EXT%\shell\Compresser\command" /ve /d "\"%EXE%\" \"%%1\"" /f >nul
echo   [OK] %EXT%
exit /b 0
