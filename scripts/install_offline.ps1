param(
    [string]$VenvPath = ".venv",
    [string]$RequirementsPath = "requirements.txt",
    [string]$WheelhousePath = "wheelhouse",
    [string]$PythonCommand = "python",
    [switch]$UsePreparedVenv
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process -Force

if (!(Test-Path $RequirementsPath)) {
    throw "Requirements file not found: $RequirementsPath"
}
if (!(Test-Path $WheelhousePath)) {
    throw "Wheelhouse directory not found: $WheelhousePath"
}

if ($UsePreparedVenv -and (Test-Path ".venv-offline")) {
    Write-Host "[1/4] Using prepared .venv-offline from package..."
    $VenvPath = ".venv-offline"
} elseif (!(Test-Path $VenvPath)) {
    Write-Host "[1/4] Creating virtual environment: $VenvPath"
    & $PythonCommand -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) {
        throw "venv creation failed."
    }
} else {
    Write-Host "[1/4] Virtual environment already exists: $VenvPath"
}

$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
if (!(Test-Path $PythonExe)) {
    throw "Python executable not found in venv: $PythonExe"
}

Write-Host "[2/4] Installing dependencies from local wheelhouse..."
& $PythonExe -m pip install --no-index --find-links=$WheelhousePath -r $RequirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "offline pip install failed."
}

Write-Host "[3/4] Creating local config files when missing..."
if (!(Test-Path "config.json") -and (Test-Path "config.example.json")) {
    Copy-Item "config.example.json" "config.json"
}
if (!(Test-Path "web_settings.json") -and (Test-Path "web_settings.example.json")) {
    Copy-Item "web_settings.example.json" "web_settings.json"
}
New-Item -ItemType Directory -Force -Path "Data" | Out-Null
New-Item -ItemType Directory -Force -Path "strategy_metrics_dbs" | Out-Null

Write-Host "[4/4] Installation complete."
Write-Host "Start app:"
Write-Host "  .\$VenvPath\Scripts\Activate.ps1"
Write-Host "  python app.py"
Write-Host "Then open http://127.0.0.1:5001"
