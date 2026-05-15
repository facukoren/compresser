@echo off
setlocal

set "EXTENSIONS=.mp4 .mkv .mov .avi .webm .flv .wmv .m4v .ts .mpg .mpeg .m2ts"

echo Removiendo menu contextual de Compresser...
echo.

for %%E in (%EXTENSIONS%) do call :unregister "%%E"

echo.
echo ============================================================
echo  Listo. La opcion del menu contextual fue eliminada.
echo ============================================================
pause
exit /b 0

:unregister
set "EXT=%~1"
reg delete "HKCU\Software\Classes\SystemFileAssociations\%EXT%\shell\Compresser" /f >nul 2>&1
echo   [-] %EXT%
exit /b 0
