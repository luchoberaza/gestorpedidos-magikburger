<#
Arma el instalador completo de Magik: launcher de Electron + backend + Python embebido
con todas sus dependencias. La PC del local no necesita tener nada instalado.

Uso (doble clic en build_launcher.bat, o desde esta carpeta):
  powershell -ExecutionPolicy Bypass -File armar-instalador.ps1            # solo arma
  powershell -ExecutionPolicy Bypass -File armar-instalador.ps1 -Publicar  # arma y lo sube a GitHub

Resultado: dist\Instalar-Magik.exe
Link fijo de descarga (después de -Publicar):
  https://github.com/luchoberaza/gestorpedidos-magikburger/releases/download/instalador/Instalar-Magik.exe
#>
param([switch]$Publicar)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # sin esto Invoke-WebRequest es lentísimo en PowerShell 5.1
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Python embebido: misma versión que el entorno de desarrollo (donde corren las pruebas).
$PythonVersion = '3.13.14'
$PythonSha256  = '90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907'
$Repo          = 'luchoberaza/gestorpedidos-magikburger'
$TagInstalador = 'instalador'
$NombreExe     = 'Instalar-Magik.exe'

$Launcher   = $PSScriptRoot
$Raiz       = Split-Path -Parent $Launcher
$DirPython  = Join-Path $Launcher 'python'
$Descargas  = Join-Path $Launcher 'descargas'
$Requisitos = Join-Path $Raiz 'requirements.txt'

function Paso([string]$texto) { Write-Host ''; Write-Host "==> $texto" -ForegroundColor Cyan }
function Fallar([string]$texto) { Write-Host "ERROR: $texto" -ForegroundColor Red; exit 1 }
function Revisar([string]$que) { if ($LASTEXITCODE -ne 0) { Fallar "$que (código $LASTEXITCODE)" } }

Set-Location -LiteralPath $Launcher

# ---------------------------------------------------------------------------
Paso "Python embebido $PythonVersion"
New-Item -ItemType Directory -Force -Path $Descargas | Out-Null
$zip = Join-Path $Descargas "python-$PythonVersion-embed-amd64.zip"
if (-not (Test-Path -LiteralPath $zip)) {
    $url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Write-Host "Descargando $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
}
$hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $zip).Hash.ToLower()
if ($hash -ne $PythonSha256) {
    Remove-Item -LiteralPath $zip -Force
    Fallar "el zip de Python no coincide con el SHA256 esperado ($hash). Se borró; volvé a correr el script."
}

if (Test-Path -LiteralPath $DirPython) { Remove-Item -LiteralPath $DirPython -Recurse -Force }
Expand-Archive -LiteralPath $zip -DestinationPath $DirPython

$firma = Get-AuthenticodeSignature -LiteralPath (Join-Path $DirPython 'python.exe')
if ($firma.Status -ne 'Valid' -or $firma.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
    Fallar "python.exe no tiene una firma válida de la Python Software Foundation ($($firma.Status))."
}

# El Python embebido solo busca módulos en las rutas de su archivo ._pth: se suma site-packages.
$partes = $PythonVersion.Split('.')
$pth = Join-Path $DirPython ("python{0}{1}._pth" -f $partes[0], $partes[1])
if (-not (Test-Path -LiteralPath $pth)) { Fallar "no encuentro $pth dentro del zip de Python" }
Add-Content -LiteralPath $pth -Value 'Lib\site-packages' -Encoding ASCII

# ---------------------------------------------------------------------------
Paso 'Dependencias (requirements.txt) dentro del Python embebido'
$pyHost = Join-Path $Raiz '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pyHost)) { $pyHost = 'python' }
$sitePackages = Join-Path $DirPython 'Lib\site-packages'
& $pyHost -m pip install --disable-pip-version-check --no-warn-script-location `
    --target $sitePackages --platform win_amd64 --python-version ("{0}.{1}" -f $partes[0], $partes[1]) `
    --implementation cp --only-binary=:all: -r $Requisitos
Revisar 'pip install'

$pyEmbebido = Join-Path $DirPython 'python.exe'
& $pyEmbebido -c "import flask, reportlab, PIL, sqlite3, ctypes; from importlib.metadata import version as v; print('Python embebido OK: Flask', v('flask'), '· reportlab', v('reportlab'), '· Pillow', v('pillow'))"
Revisar 'el Python embebido no importa las dependencias'

# ---------------------------------------------------------------------------
Paso 'Interfaz compilada'
if (-not (Test-Path -LiteralPath (Join-Path $Raiz 'static\dist\app.js'))) {
    Fallar 'falta static\dist\app.js: compilá la interfaz (cd frontend; npm install; npm run build).'
}

# ---------------------------------------------------------------------------
Paso 'Instalador (electron-builder)'
if (-not (Test-Path -LiteralPath (Join-Path $Launcher 'node_modules'))) {
    & npm install
    Revisar 'npm install'
}
& npm run dist
Revisar 'npm run dist'

$exe = Join-Path $Launcher "dist\$NombreExe"
if (-not (Test-Path -LiteralPath $exe)) { Fallar "no se generó $exe" }
$mb = [math]::Round((Get-Item -LiteralPath $exe).Length / 1MB, 1)
Write-Host "Listo: $exe ($mb MB)" -ForegroundColor Green

# ---------------------------------------------------------------------------
if ($Publicar) {
    Paso "Publicando en GitHub ($Repo, release '$TagInstalador')"
    # La release del instalador NO puede quedar como 'latest': el launcher busca las
    # actualizaciones (OTA) en la última release, y esas son las b<número>.
    $ErrorActionPreference = 'Continue'
    & gh release view $TagInstalador --repo $Repo --json tagName 2>$null | Out-Null
    $existe = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'

    if ($existe) {
        & gh release upload $TagInstalador $exe --repo $Repo --clobber
        Revisar 'gh release upload'
    } else {
        $notas = "Instalador completo de Magik: incluye Python y todo lo necesario. " +
                 "Se descarga $NombreExe, se ejecuta y la app queda instalada y abierta. " +
                 "Las actualizaciones llegan solas al abrir la app."
        & gh release create $TagInstalador $exe --repo $Repo --latest=false `
            --title 'Instalador de Magik' --notes $notas
        Revisar 'gh release create'
    }
    Write-Host "Descarga: https://github.com/$Repo/releases/download/$TagInstalador/$NombreExe" -ForegroundColor Green
}
