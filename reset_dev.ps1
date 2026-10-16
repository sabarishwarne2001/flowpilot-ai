[CmdletBinding()]
param(
    [switch]$KeepRedis,
    [switch]$NoRestart,
    [switch]$DryRun,
    [switch]$Force
)

# Use Continue so native CLI stderr (Docker info) does not crash PowerShell
$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest

$RepoRoot   = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BackendDir = Join-Path $RepoRoot 'backend'
$VenvPython = Join-Path $BackendDir '.venv\Scripts\python.exe'
$RunDir     = Join-Path $RepoRoot '.dev'
$PidFile    = Join-Path $RunDir 'pids.json'

function Write-Step  { param([string]$m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok    { param([string]$m) Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Warn2 { param([string]$m) Write-Host "    [!]  $m" -ForegroundColor Yellow }
function Write-Fail  { param([string]$m) Write-Host "    [x]  $m" -ForegroundColor Red }

Write-Host ''
Write-Host '  Servabo — environment reset' -ForegroundColor White
Write-Host '  --------------------------------' -ForegroundColor DarkGray

if (-not (Test-Path $VenvPython)) {
    Write-Fail "No virtual environment at backend\.venv."
    Write-Host '         Run .\start_dev.ps1 once first.' -ForegroundColor Yellow
    exit 1
}

# --- 1. Stop all existing python / uvicorn / worker processes ---
Write-Step '1/4  Stopping existing fleet'

if (Test-Path $PidFile) {
    try {
        $tracked = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
        foreach ($entry in $tracked.PSObject.Properties) {
            $procId = 0
            if ([int]::TryParse([string]$entry.Value, [ref]$procId)) {
                Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
                Write-Ok "stopped $($entry.Name) (pid $procId)"
            }
        }
    } catch { }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

# Also forcefully kill any orphan python processes running worker or uvicorn
Get-Process python -ErrorAction SilentlyContinue | ForEach-Object {
    try { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue } catch {}
}
Start-Sleep -Seconds 2
Write-Ok "Worker and API processes cleared."

# --- 2. Backing services ---
# >>> one-release names: Docker data made before the rename (FlowPilot became Servabo on
# 2026-10-16) lives in volumes named flowpilot_*. Keep using them, so nothing looks lost; README
# "Local data from before the rename" says how to move to the new names. Remove next release.
function Use-PreRenameDockerData {
    if ($env:COMPOSE_PROJECT_NAME) { return $false }
    docker volume inspect flowpilot_postgres_data 2>&1 | Out-Null
    $hasOld = ($LASTEXITCODE -eq 0)
    docker volume inspect servabo_postgres_data 2>&1 | Out-Null
    $hasNew = ($LASTEXITCODE -eq 0)
    if ($hasOld -and -not $hasNew) {
        $env:COMPOSE_PROJECT_NAME = 'flowpilot'
        foreach ($c in @('flowpilot-db', 'flowpilot-redis', 'flowpilot-minio', 'flowpilot-minio-init')) {
            docker rm -f $c 2>&1 | Out-Null
        }
        return $true
    }
    return $false
}
# <<< one-release names
Write-Step '2/4  Verifying backing services'

Push-Location $BackendDir
try {
    if (Use-PreRenameDockerData) { Write-Warn2 'Using your Docker data from before the rename (volumes flowpilot_*); see README.' }
    if ($DryRun) {
        Write-Ok 'would ensure db, redis, minio are up'
    } else {
        # Temporarily redirect stderr to prevent PowerShell NativeCommandError
        $prevErrorAction = $global:ErrorActionPreference
        $global:ErrorActionPreference = 'SilentlyContinue'
        docker compose up -d db redis minio minio-init | Out-Null
        $global:ErrorActionPreference = $prevErrorAction
        Write-Ok 'db, redis, minio running'
        Start-Sleep -Seconds 3
    }
} finally { Pop-Location }

# --- 3. Reset database, redis, and seed data ---
Write-Step '3/4  Resetting Postgres, Redis, and storage'

$resetArgs = @('scripts\reset_dev_environment.py')
if ($DryRun)    { $resetArgs += '--dry-run' }
if ($KeepRedis) { $resetArgs += '--keep-redis' }
if ($Force)     { $resetArgs += '--yes' }

Push-Location $BackendDir
try {
    & $VenvPython @resetArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Fail "reset_dev_environment.py exited with error code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
} finally { Pop-Location }

# --- 4. Restart the Stack ---
Write-Step '4/4  Restarting the fleet'

if ($DryRun) {
    Write-Ok 'would run .\start_dev.ps1'
    Write-Host "`n  Dry run complete. Nothing was changed.`n" -ForegroundColor DarkGray
    exit 0
}

if ($NoRestart) {
    Write-Warn2 'Skipped restart due to -NoRestart.'
    Write-Host "`n  Start it yourself with: .\start_dev.ps1`n" -ForegroundColor DarkGray
    exit 0
}

$launcher = Join-Path $RepoRoot 'start_dev.ps1'
if (-not (Test-Path $launcher)) {
    Write-Warn2 'start_dev.ps1 not found; start the fleet manually.'
    exit 0
}

& $launcher -SkipMigrations
