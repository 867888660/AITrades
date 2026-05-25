param(
    [string]$RequirementsPath = "requirements.txt",
    [string]$OutputDir = "dist",
    [string]$PackageName = "polymarket_datatube_offline.zip",
    [string]$PythonCommand = "python",
    [switch]$IncludePreparedVenv
)

$ErrorActionPreference = "Stop"

$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

if (!(Test-Path $RequirementsPath)) {
    throw "Requirements file not found: $RequirementsPath"
}

$Wheelhouse = Join-Path $Root "wheelhouse"
$Dist = Join-Path $Root $OutputDir
$Stage = Join-Path $Dist "offline_package"
$ZipPath = Join-Path $Dist $PackageName

New-Item -ItemType Directory -Force -Path $Wheelhouse | Out-Null
New-Item -ItemType Directory -Force -Path $Dist | Out-Null

Write-Host "[1/5] Downloading wheels to wheelhouse..."
& $PythonCommand -m pip download -d $Wheelhouse -r $RequirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "pip download failed."
}

if ($IncludePreparedVenv) {
    $PreparedVenv = Join-Path $Root ".venv-offline"
    if (Test-Path $PreparedVenv) {
        Remove-Item -Recurse -Force $PreparedVenv
    }
    Write-Host "[2/5] Creating optional prepared venv..."
    & $PythonCommand -m venv $PreparedVenv
    if ($LASTEXITCODE -ne 0) {
        throw "venv creation failed."
    }
    $PreparedPip = Join-Path $PreparedVenv "Scripts\python.exe"
    & $PreparedPip -m pip install --no-index --find-links=$Wheelhouse -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "offline install into prepared venv failed."
    }
} else {
    Write-Host "[2/5] Skipping prepared venv. The installer will create .venv on the target machine."
}

if (Test-Path $Stage) {
    Remove-Item -Recurse -Force $Stage
}
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

Write-Host "[3/5] Staging publishable files..."
$ExcludeDirs = @(
    ".git", ".claude", ".vscode", "__pycache__", "Data", "strategy_metrics_dbs",
    "dist", "wheelhouse", ".venv", ".venv-offline", "venv", "env"
)
$ExcludeFiles = @(
    "config.json", "web_settings.json", "web_settings.secrets.json", ".datatube_secret.key",
    "output.log", "project.zip", "polymarket_active_markets_cache.json",
    "*.db", "*.db-shm", "*.db-wal", "*.sqlite", "*.sqlite3", "*.zip"
)

Get-ChildItem -Force $Root | ForEach-Object {
    if ($_.PSIsContainer) {
        if ($ExcludeDirs -contains $_.Name) {
            return
        }
        Copy-Item -Recurse -Force $_.FullName (Join-Path $Stage $_.Name)
    } else {
        $skip = $false
        foreach ($pattern in $ExcludeFiles) {
            if ($_.Name -like $pattern) {
                $skip = $true
                break
            }
        }
        if (!$skip) {
            Copy-Item -Force $_.FullName (Join-Path $Stage $_.Name)
        }
    }
}

Copy-Item -Recurse -Force $Wheelhouse (Join-Path $Stage "wheelhouse")
if ($IncludePreparedVenv) {
    Copy-Item -Recurse -Force (Join-Path $Root ".venv-offline") (Join-Path $Stage ".venv-offline")
}

Write-Host "[4/5] Creating zip..."
if (Test-Path $ZipPath) {
    Remove-Item -Force $ZipPath
}
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -Force

Write-Host "[5/5] Done."
Write-Host "Offline package: $ZipPath"
Write-Host "On target machine: unzip, then run .\scripts\install_offline.ps1"
