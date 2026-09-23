[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipDb,
    [switch]$SkipBuild,
    [switch]$SkipMutate,
    [switch]$SkipRegression,
    [switch]$SkipExecBits,
    [switch]$Rollback
)

# ARCH-41 Tranche 1 - Redaction Studio seams, automation conformance, backup floor.
#
# Place apply_arch41.py and verify_arch41.py in backend\ and this file in the
# repository root (next to backend\ and frontend\), then:
#
#   .\run_arch41.ps1 -CheckOnly      # validate the tree; write nothing
#   .\run_arch41.ps1                 # full run and certification
#   .\run_arch41.ps1 -SkipRegression # skip the ARCH-40 / hardening chain
#   .\run_arch41.ps1 -Rollback       # restore the code (no database change to undo)
#
# The --db gate backs up the database in DATABASE_URL with a THROWAWAY key into
# a temporary folder, restores it into flowpilot_restore_drill_<stamp>, compares
# every table, and drops that database. It never touches your real backups or
# the source database. It needs pg_dump / pg_restore matching the server's major
# version: on PATH, or set PG_BIN (default tried: C:\Program Files\PostgreSQL\16\bin).

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "hm1_tier_price_per_key"   # Tranche 1 adds no migration

$env:PYTHONUTF8 = "1"
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/flowpilot"
}

$Python = "python"
foreach ($candidate in @((Join-Path $Backend ".venv\Scripts\python.exe"), (Join-Path $Backend "venv\Scripts\python.exe"))) {
    if (Test-Path $candidate) { $Python = $candidate; break }
}

function Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host "`nFAILED: $Text" -ForegroundColor Red; exit 1 }
function RunCommand([string]$Exe, [string[]]$CmdArgs, [string]$What) {
    & $Exe $CmdArgs
    if ($LASTEXITCODE -ne 0) { Fail "$What (exit $LASTEXITCODE)" }
}

foreach ($required in @("apply_arch41.py", "verify_arch41.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) {
        Fail "backend\$required not found. Copy it into backend\ first."
    }
}
Write-Host "Python: $Python"
Set-Location $Backend

if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch41.py", "--rollback") "code rollback"
    exit 0
}

Step "1/7 CHECK - every file must be the 2f81cdf baseline or the ARCH-41 result"
RunCommand $Python @("apply_arch41.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }

Step "2/7 APPLY"
RunCommand $Python @("apply_arch41.py") "apply"

Step "3/7 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch41.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }

Step "4/7 HEAD - Tranche 1 adds no migration"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -notmatch [regex]::Escape($ReleaseHead)) {
    Fail "alembic current is not $ReleaseHead. Finish ARCH-40 (run_arch40.ps1) before ARCH-41."
}

Step "5/7 EXECUTABLE BITS - deploy wrappers"
# Found during ARCH-41: git tracks deploy/bin/flowpilot-sweep and the watchdog
# as 100644, so a Linux checkout cannot execute them and every cron entry that
# calls them (the sweepers, and now the backups) fails with Permission denied.
# This marks them executable IN THE INDEX; commit the change.
if (-not $SkipExecBits) {
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        Push-Location $Root
        try {
            & git update-index --chmod=+x backend/deploy/bin/flowpilot-sweep backend/deploy/bin/flowpilot-sweep-watchdog
            if ($LASTEXITCODE -eq 0) { Write-Host "Marked executable in the index. Commit to keep it." -ForegroundColor Green }
            else { Write-Host "git update-index failed; set the bits on the server with chmod +x." -ForegroundColor Yellow }
        } finally { Pop-Location }
    } else {
        Write-Host "git not found; run 'chmod +x backend/deploy/bin/*' on the server." -ForegroundColor Yellow
    }
}

Step "6/7 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    # npm ci fails on this repository (lockfile drift, finding R-1); install instead.
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }

Step "7/7 VERIFY"
$verifyArgs = @("verify_arch41.py")
if (-not $SkipDb) {
    if (-not $env:PG_BIN) {
        $defaultBin = "C:\Program Files\PostgreSQL\16\bin"
        if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue) -and (Test-Path (Join-Path $defaultBin "pg_dump.exe"))) {
            $env:PG_BIN = $defaultBin
        }
    }
    if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue) -and -not $env:PG_BIN) {
        Fail "pg_dump not found. Install the PostgreSQL 16 client tools or set PG_BIN, or pass -SkipDb."
    }
    $verifyArgs += "--db"
}
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch41.py"

Write-Host "`nARCH-41 Tranche 1 applied and verified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch41\" -ForegroundColor Green
Write-Host @"

Before relying on the backup floor:
  1. python scripts\backup_floor.py --generate-key
     Store the key OFF this machine, then set FLOWPILOT_BACKUP_KEY (and, on the
     server, add it to /etc/flowpilot/sweepers.env).
  2. python scripts\backup_floor.py          # first real backup
  3. python scripts\restore_drill.py         # prove it restores
  4. Optional off-host copy: FLOWPILOT_BACKUP_S3_BUCKET, _ENDPOINT, _ACCESS_KEY, _SECRET_KEY
"@

Set-Location $Root
