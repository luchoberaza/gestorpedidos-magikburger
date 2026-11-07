@echo off
rem Arma el instalador completo (Instalar-Magik.exe) con Python embebido.
rem Para armarlo y ademas subirlo a GitHub:  build_launcher.bat -Publicar
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0armar-instalador.ps1" %*
echo.
pause
