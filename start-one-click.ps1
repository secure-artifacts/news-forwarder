param([switch]$NoBrowser)

$ErrorActionPreference = 'Stop'

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppUrl = 'http://127.0.0.1:8000'
$HealthUrl = "$AppUrl/api/status"
$Python = Join-Path $ProjectDir '.venv\Scripts\python.exe'
$Requirements = Join-Path $ProjectDir 'requirements.txt'
$OutputLog = Join-Path $ProjectDir 'server.out.log'
$ErrorLog = Join-Path $ProjectDir 'server.err.log'

Set-Location -LiteralPath $ProjectDir

function Test-NewsApp {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 3
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

function Open-NewsApp {
    if (-not $NoBrowser) {
        Start-Process $AppUrl
    }
}

if (Test-NewsApp) {
    Write-Host 'News Forwarder is already running. Opening the web page...' -ForegroundColor Green
    Open-NewsApp
    exit 0
}

if (-not (Test-Path -LiteralPath $Python)) {
    Write-Host 'First run: creating the Python environment...' -ForegroundColor Cyan
    & python -m venv (Join-Path $ProjectDir '.venv')
    if ($LASTEXITCODE -ne 0) {
        throw 'Cannot create the Python environment. Install Python 3.11 or later.'
    }
}

& $Python -c 'import fastapi, uvicorn, httpx, yaml, google.auth, tzdata' 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'First run: installing dependencies. Please wait...' -ForegroundColor Cyan
    & $Python -m pip install --disable-pip-version-check -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        throw 'Dependency installation failed. Check the network connection.'
    }
}

Write-Host 'Starting News Forwarder in the background...' -ForegroundColor Cyan
$process = Start-Process `
    -FilePath $Python `
    -ArgumentList 'main.py', 'serve' `
    -WorkingDirectory $ProjectDir `
    -RedirectStandardOutput $OutputLog `
    -RedirectStandardError $ErrorLog `
    -WindowStyle Hidden `
    -PassThru

for ($attempt = 1; $attempt -le 90; $attempt++) {
    if (Test-NewsApp) {
        Write-Host 'Startup complete. Opening the web page...' -ForegroundColor Green
        Open-NewsApp
        exit 0
    }
    if ($process.HasExited) {
        throw "The application exited during startup. Check: $ErrorLog"
    }
    Start-Sleep -Seconds 1
}

throw "Startup timed out. Check: $ErrorLog"
