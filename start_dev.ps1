<#
.SYNOPSIS
    FlowPilot AI — single-command local development launcher (Windows).

.DESCRIPTION
    Brings up the complete stack from a clean checkout:
      1. Verifies Docker, Python 3.12 and Node.js.
      2. Generates backend/.env and frontend/.env with validated defaults.
      3. Starts db (pgvector), redis, minio and minio-init.
      4. Runs alembic upgrade head.
      5. Seeds price books, quota tiers and the bootstrap admin.
      6. Starts the API and a single supervised worker (--loop all).
      7. Starts the Vite dev server.
      8. Automatically opens the browser to http://localhost:5173.
#>

[CmdletBinding()]
param(
    [switch]$Reset,
    [switch]$NoFrontend,
    [switch]$Stop,
    [switch]$SkipMigrations,
    [int]$ApiPort = 8000,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = 'Continue'
Set-StrictMode -Version Latest
$env:PYTHONUNBUFFERED = "1"

$RepoRoot    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BackendDir  = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$RunDir      = Join-Path $RepoRoot '.dev'
$VenvDir     = Join-Path $BackendDir '.venv'
$PidFile     = Join-Path $RunDir 'pids.json'
$LogDir      = Join-Path $RunDir 'logs'

function Write-Step   { param([string]$m) Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok     { param([string]$m) Write-Host "    [ok] $m" -ForegroundColor Green }
function Write-Warn2  { param([string]$m) Write-Host "    [!]  $m" -ForegroundColor Yellow }
function Write-Fail   { param([string]$m) Write-Host "    [x]  $m" -ForegroundColor Red }

function Fail-Hard {
    param([string]$Message, [string]$Remedy = $null)
    Write-Fail $Message
    if ($Remedy) { Write-Host "         $Remedy" -ForegroundColor Yellow }
    exit 1
}

function New-HexSecret {
    param([int]$Bytes = 32)
    $buffer = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return ($buffer | ForEach-Object { $_.ToString('x2') }) -join ''
}

function New-FernetKey {
    $buffer = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buffer)
    return [Convert]::ToBase64String($buffer).Replace('+', '-').Replace('/', '_')
}

function Test-PortOpen {
    param([string]$TargetHost = '127.0.0.1', [int]$Port, [int]$TimeoutMs = 1000)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($TargetHost, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch { return $false } finally { $client.Close() }
}

function Wait-ForPort {
    param([string]$Label, [int]$Port, [int]$TimeoutSeconds = 90)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortOpen -Port $Port) { Write-Ok "$Label is accepting connections on $Port"; return $true }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

function Get-EnvValue {
    param([string]$Path, [string]$Key)
    if (-not (Test-Path $Path)) { return $null }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^\s*$([regex]::Escape($Key))\s*=\s*(.*)$") {
            return $Matches[1].Trim().Trim('"')
        }
    }
    return $null
}

function Set-EnvValue {
    param([string]$Path, [string]$Key, [string]$Value)
    $lines = if (Test-Path $Path) { @(Get-Content -LiteralPath $Path) } else { @() }
    $pattern = "^\s*$([regex]::Escape($Key))\s*="
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match $pattern) { $found = $true; "$Key=$Value" } else { $line }
    }
    if (-not $found) { $out = @($out) + "$Key=$Value" }
    Set-Content -LiteralPath $Path -Value $out -Encoding UTF8
}

function Stop-Tracked {
    if (-not (Test-Path $PidFile)) { Write-Warn2 'No tracked processes.'; return }
    $tracked = Get-Content -LiteralPath $PidFile -Raw | ConvertFrom-Json
    foreach ($entry in $tracked.PSObject.Properties) {
        $procId = [int]$entry.Value
        try {
            $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
            if ($proc) {
                Write-Host "    stopping $($entry.Name) (pid $procId)"
                Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Write-Ok 'Tracked processes stopped.'
}

Write-Host ''
Write-Host '  FlowPilot AI — local development launcher' -ForegroundColor White
Write-Host '  ----------------------------------------' -ForegroundColor DarkGray

if ($Stop) {
    Write-Step 'Stopping tracked processes'
    Stop-Tracked
    Write-Step 'Stopping Docker backing services'
    Push-Location $BackendDir
    try { docker compose stop db redis minio 2>&1 | Out-Null; Write-Ok 'Containers stopped.' }
    finally { Pop-Location }
    exit 0
}

New-Item -ItemType Directory -Force -Path $RunDir, $LogDir | Out-Null

# --- 1. Prerequisites ------------------------------------------------------
Write-Step '1/8  Verifying prerequisites'

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail-Hard 'Docker is not on PATH.' 'Install Docker Desktop: https://www.docker.com/products/docker-desktop'
}
try { docker info 2>&1 | Out-Null } catch { }
if ($LASTEXITCODE -ne 0) {
    Fail-Hard 'The Docker daemon is not responding.' 'Start Docker Desktop and wait for the whale icon to settle.'
}
Write-Ok 'Docker daemon is up.'

$pythonExe = $null
foreach ($candidate in @('py -3.12', 'python3.12', 'python')) {
    $parts = $candidate.Split(' ')
    $cmd = Get-Command $parts[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $version = & $parts[0] @($parts[1..($parts.Length - 1)]) --version 2>&1
    if ($version -match '3\.1[2-9]') { $pythonExe = $candidate; Write-Ok "Python: $version"; break }
}
if (-not $pythonExe) { Fail-Hard 'Python 3.12+ not found.' 'Install from https://www.python.org/downloads/' }

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    if ($NoFrontend) { Write-Warn2 'Node.js absent; continuing because -NoFrontend was passed.' }
    else { Fail-Hard 'Node.js is not on PATH.' 'Install Node 20+ LTS: https://nodejs.org' }
} else {
    Write-Ok "Node: $(node --version)"
}

# --- 2. Virtual environment ------------------------------------------------
Write-Step '2/8  Python virtual environment'

$venvPython = Join-Path $VenvDir 'Scripts\python.exe'
if (-not (Test-Path $venvPython)) {
    Write-Host '    creating backend/.venv ...'
    $parts = $pythonExe.Split(' ')
    & $parts[0] @($parts[1..($parts.Length - 1)]) -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { Fail-Hard 'venv creation failed.' }
}
Write-Ok 'Virtual environment present.'

# --- 3. Environment files --------------------------------------------------
Write-Step '3/8  Environment configuration'

$backendEnv = Join-Path $BackendDir '.env'
if (-not (Test-Path $backendEnv)) {
    Copy-Item (Join-Path $BackendDir '.env.example') $backendEnv
    Write-Ok 'Created backend/.env from .env.example.'
}

$generated = @()
foreach ($key in @('JWT_SECRET_KEY', 'API_KEY_PEPPER', 'REDIS_IDENTITY_PEPPER')) {
    if ([string]::IsNullOrWhiteSpace((Get-EnvValue $backendEnv $key))) {
        Set-EnvValue $backendEnv $key (New-HexSecret 32)
        $generated += $key
    }
}
if ([string]::IsNullOrWhiteSpace((Get-EnvValue $backendEnv 'EMAIL_ENCRYPTION_KEYS'))) {
    Set-EnvValue $backendEnv 'EMAIL_ENCRYPTION_KEYS' (New-FernetKey)
    $generated += 'EMAIL_ENCRYPTION_KEYS'
}
if ($generated.Count -gt 0) { Write-Ok "Generated secrets: $($generated -join ', ')" }

$hostDefaults = @{
    'ENVIRONMENT'           = 'development'
    'POSTGRES_HOST'         = 'localhost'
    'POSTGRES_PORT'         = '5432'
    'POSTGRES_USER'         = 'postgres'
    'POSTGRES_PASSWORD'     = 'postgres'
    'POSTGRES_DB'           = 'flowpilot'
    'REDIS_URL'             = 'redis://localhost:6379/0'
    'STORAGE_BACKEND'       = 'minio'
    'S3_BUCKET'             = 'flowpilot-dev'
    'S3_ENDPOINT_URL'       = 'http://localhost:9000'
    'S3_ACCESS_KEY_ID'      = 'minioadmin'
    'S3_SECRET_ACCESS_KEY'  = 'minioadmin'
    'AWS_ACCESS_KEY_ID'     = 'minioadmin'
    'AWS_SECRET_ACCESS_KEY' = 'minioadmin'
    'SERVICE_ROLE'          = 'web'
    'FRONTEND_URL'          = "http://localhost:$FrontendPort"
    'RERANKER_ENABLED'      = 'false'
}
foreach ($pair in $hostDefaults.GetEnumerator()) {
    if ([string]::IsNullOrWhiteSpace((Get-EnvValue $backendEnv $pair.Key))) {
        Set-EnvValue $backendEnv $pair.Key $pair.Value
    }
}

$currentPgHost = Get-EnvValue $backendEnv 'POSTGRES_HOST'
if ($currentPgHost -in @('db', 'postgres')) {
    Set-EnvValue $backendEnv 'POSTGRES_HOST' 'localhost'
}
$currentS3 = Get-EnvValue $backendEnv 'S3_ENDPOINT_URL'
if ($currentS3 -and $currentS3 -match '://minio(:|/|$)') {
    Set-EnvValue $backendEnv 'S3_ENDPOINT_URL' 'http://localhost:9000'
}
Write-Ok 'backend/.env validated for host execution.'

$frontendEnv = Join-Path $FrontendDir '.env'
if (-not (Test-Path $frontendEnv)) {
    Copy-Item (Join-Path $FrontendDir '.env.example') $frontendEnv
    Write-Ok 'Created frontend/.env from .env.example.'
}
# ARCH-30 Tranche 1 (T4-F5). VITE_API_URL belongs in .env.development.local,
# which Vite loads for the dev server only. Written to .env it was ALSO loaded by
# `vite build`, baking this machine's localhost API into production bundles;
# vite.config.ts now refuses that build. Any copy an earlier run left in .env is
# stripped, once, so an existing checkout builds again.
$frontendDevEnv = Join-Path $FrontendDir '.env.development.local'
if (Select-String -Path $frontendEnv -Pattern '^\s*VITE_API_URL\s*=' -Quiet) {
    $kept = @(Get-Content $frontendEnv | Where-Object { $_ -notmatch '^\s*VITE_API_URL\s*=' })
    Set-Content -Path $frontendEnv -Value $kept -Encoding UTF8
    Write-Ok 'Moved VITE_API_URL out of frontend/.env.'
}
if (Test-Path $frontendDevEnv) {
    Set-EnvValue $frontendDevEnv 'VITE_API_URL' "http://localhost:$ApiPort/api/v1"
} else {
    Set-Content -Path $frontendDevEnv -Value "VITE_API_URL=http://localhost:$ApiPort/api/v1" -Encoding UTF8
}

# --- 4. Backing services ---------------------------------------------------
Write-Step '4/8  Docker backing services'

Push-Location $BackendDir
try {
    if ($Reset) {
        Write-Warn2 'Reset requested: destroying volumes.'
        docker compose down -v 2>&1 | Out-Null
    }
    docker compose up -d db redis minio minio-init
    if ($LASTEXITCODE -ne 0) { Fail-Hard 'docker compose up failed.' }
} finally { Pop-Location }

if (-not (Wait-ForPort 'PostgreSQL' 5432 120)) { Fail-Hard 'PostgreSQL did not open port 5432.' }
if (-not (Wait-ForPort 'Redis'      6379  60)) { Fail-Hard 'Redis did not open port 6379.' }
if (-not (Wait-ForPort 'MinIO'      9000  90)) { Fail-Hard 'MinIO did not open port 9000.' }

Start-Sleep -Seconds 2

# --- 5. Migrations ---------------------------------------------------------
Write-Step '5/8  Database migrations'

Push-Location $BackendDir
try {
    if ($SkipMigrations) {
        Write-Warn2 'Skipped by request.'
    } else {
        & $venvPython -m alembic upgrade head
        if ($LASTEXITCODE -ne 0) { Fail-Hard 'alembic upgrade head failed.' }
        $head = & $venvPython -m alembic current 2>&1 | Select-String -Pattern '\(head\)'
        Write-Ok "Alembic at head: $($head -replace '.*\s([a-z0-9_]+)\s\(head\).*', '$1')"
    }
} finally { Pop-Location }

# --- 6. Seed data ----------------------------------------------------------
Write-Step '6/8  Seeding commercial defaults'

Push-Location $BackendDir
try {
    # 1. Price Book
    if (Test-Path 'scripts/seed_price_book.py') {
        & $venvPython scripts/seed_price_book.py
        Write-Ok "scripts/seed_price_book.py"
    }

    # 2. Quota Tiers
    if (Test-Path 'scripts/seed_quota_tiers.py') {
        & $venvPython scripts/seed_quota_tiers.py
        Write-Ok "scripts/seed_quota_tiers.py"
    }

    # 3. Admin User
    $adminSeedJson = & $venvPython scripts/seed_admin.py --json
    try {
        $seed = $adminSeedJson | ConvertFrom-Json
        $adminEmail = $seed.email
        $adminPassword = $seed.password
        Write-Ok "Admin account: $adminEmail ($($seed.status))"
    } catch {
        $adminEmail = 'admin@flowpilot.local'
        $adminPassword = 'FlowPilot!Dev123'
        Write-Ok "Admin default: $adminEmail"
    }
} finally { Pop-Location }

# --- 7. Application processes ---------------------------------------------
Write-Step '7/8  Starting application processes'

$tracked = @{}

# Unified console logging for API
$apiLog = Join-Path $LogDir 'api.log'
$apiProc = Start-Process -FilePath 'cmd.exe' `
    -ArgumentList @('/c', "$venvPython -m uvicorn app.main:app --host 127.0.0.1 --port $ApiPort --reload > ""$apiLog"" 2>&1") `
    -WorkingDirectory $BackendDir `
    -WindowStyle Hidden -PassThru
$tracked['api'] = $apiProc.Id
Write-Ok "API starting -> $apiLog"

# Unified console logging for Worker
$workerLog = Join-Path $LogDir 'worker.log'
$workerProc = Start-Process -FilePath 'cmd.exe' `
    -ArgumentList @('/c', "$venvPython -m app.worker --loop all --profile all --log-level INFO > ""$workerLog"" 2>&1") `
    -WorkingDirectory $BackendDir `
    -WindowStyle Hidden -PassThru
$tracked['worker'] = $workerProc.Id
Write-Ok "Worker supervisor starting -> $workerLog"

if (-not (Wait-ForPort 'API' $ApiPort 90)) {
    Write-Fail 'The API did not bind. Last 40 lines of api.log:'
    if (Test-Path $apiLog) {
        Get-Content -LiteralPath $apiLog -Tail 40 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    }
    $tracked | ConvertTo-Json | Set-Content -LiteralPath $PidFile
    exit 1
}

if (-not $NoFrontend) {
    $nodeModules = Join-Path $FrontendDir 'node_modules'
    if (-not (Test-Path $nodeModules)) {
        Write-Host '    installing frontend dependencies (first run) ...'
        Push-Location $FrontendDir
        try { npm install } finally { Pop-Location }
    }
    $viteLog = Join-Path $LogDir 'vite.log'
    $viteProc = Start-Process -FilePath 'cmd.exe' `
        -ArgumentList @('/c', "npm run dev -- --port $FrontendPort > ""$viteLog"" 2>&1") `
        -WorkingDirectory $FrontendDir `
        -WindowStyle Hidden -PassThru
    $tracked['vite'] = $viteProc.Id
    Write-Ok "Vite starting -> $viteLog"
    Wait-ForPort 'Frontend' $FrontendPort 120 | Out-Null
}

$tracked | ConvertTo-Json | Set-Content -LiteralPath $PidFile

# --- 8. Ready --------------------------------------------------------------
Write-Step '8/8  Ready'

Write-Host ''
Write-Host '  ---------------------------------------------------------------' -ForegroundColor DarkGray
Write-Host '   FlowPilot AI is running' -ForegroundColor Green
Write-Host '  ---------------------------------------------------------------' -ForegroundColor DarkGray
if (-not $NoFrontend) {
    Write-Host "   App          http://localhost:$FrontendPort" -ForegroundColor White
}
Write-Host "   API docs     http://localhost:$ApiPort/docs" -ForegroundColor White
Write-Host "   Health       http://localhost:$ApiPort/api/v1/health" -ForegroundColor White
Write-Host '   MinIO        http://localhost:9001   (minioadmin / minioadmin)' -ForegroundColor White
Write-Host ''
Write-Host '   Sign in with' -ForegroundColor White
Write-Host "     email      $adminEmail" -ForegroundColor Yellow
Write-Host "     password   $adminPassword" -ForegroundColor Yellow
Write-Host ''
Write-Host '   Live Logs (Unified)' -ForegroundColor White
Write-Host "     Get-Content .dev\logs\worker.log -Wait -Tail 40" -ForegroundColor DarkGray
Write-Host "     Get-Content .dev\logs\api.log -Wait -Tail 40" -ForegroundColor DarkGray
Write-Host ''
Write-Host '   Stop everything' -ForegroundColor White
Write-Host '     .\start_dev.ps1 -Stop' -ForegroundColor DarkGray
Write-Host '  ---------------------------------------------------------------' -ForegroundColor DarkGray
Write-Host ''

if (-not $NoFrontend) {
    Start-Process "http://localhost:$FrontendPort"
}