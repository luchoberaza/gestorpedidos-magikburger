@echo off
setlocal

cd /d "%~dp0"

echo ================================
echo  MagikBurger Launcher - BUILD
echo ================================
echo.

set LOGFILE=%cd%\build.log
echo ================================ > "%LOGFILE%"
echo  BUILD LOG - %date% %time%      >> "%LOGFILE%"
echo ================================ >> "%LOGFILE%"
echo Carpeta: %cd% >> "%LOGFILE%"
echo. >> "%LOGFILE%"

where node >nul 2>nul
if %errorlevel% neq 0 (
  echo [ERROR] Node.js no esta instalado.
  echo [ERROR] Node.js no esta instalado. >> "%LOGFILE%"
  echo Instala Node.js LTS desde https://nodejs.org
  pause
  exit /b 1
)

where npm >nul 2>nul
if %errorlevel% neq 0 (
  echo [ERROR] npm no esta disponible.
  echo [ERROR] npm no esta disponible. >> "%LOGFILE%"
  pause
  exit /b 1
)

echo [INFO] Versiones: >> "%LOGFILE%"
node -v >> "%LOGFILE%" 2>&1
npm -v >> "%LOGFILE%" 2>&1
echo. >> "%LOGFILE%"

if not exist package.json (
  echo [ERROR] No encuentro package.json en esta carpeta (launcher).
  echo [ERROR] No encuentro package.json en %cd% >> "%LOGFILE%"
  pause
  exit /b 1
)

echo [INFO] Instalando/verificando dependencias... >> "%LOGFILE%"
if not exist node_modules (
  echo [INFO] npm install (primera vez)...
  echo [INFO] npm install (primera vez)... >> "%LOGFILE%"
  call npm install >> "%LOGFILE%" 2>&1
  if %errorlevel% neq 0 (
    echo [ERROR] Fallo npm install. Mira build.log
    echo [ERROR] Fallo npm install. >> "%LOGFILE%"
    pause
    exit /b 1
  )
) else (
  echo [INFO] node_modules ya existe. >> "%LOGFILE%"
)

echo. >> "%LOGFILE%"
echo [INFO] Corriendo build (npm run dist)... >> "%LOGFILE%"
echo [INFO] Generando instalador .exe...
call npm run dist >> "%LOGFILE%" 2>&1
if %errorlevel% neq 0 (
  echo [ERROR] Fallo npm run dist. Mira build.log
  echo [ERROR] Fallo npm run dist. >> "%LOGFILE%"
  pause
  exit /b 1
)

echo. >> "%LOGFILE%"
echo [OK] Build terminado. >> "%LOGFILE%"

if exist dist (
  echo [OK] Carpeta dist creada: %cd%\dist
  echo [OK] dist existe. >> "%LOGFILE%"
) else (
  echo [ERROR] No se creo dist. Algo raro paso. Mira build.log
  echo [ERROR] dist NO existe. >> "%LOGFILE%"
)

echo.
echo Listo. Log: %LOGFILE%
pause
exit /b 0
