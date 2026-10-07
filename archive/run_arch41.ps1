[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipDb,
    [switch]$SkipBuild,
    [switch]$SkipMutate,
    [switch]$SkipRegression,
    [switch]$SkipExecBits,
    [switch]$AllowUnpriced,
    [switch]$Rollback
)

# ARCH-41 - Operational Seams, Automation Conformance & Extraction Memory (Tranches 1-3).
#
# Place apply_arch41.py and verify_arch41.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch41.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch41.ps1                   # apply, migrate, seed, build and certify
#   .\run_arch41.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch41.ps1 -Rollback         # restore the code
#
# Accepted starting points: 2f81cdf (hardening) or 6f7dc4c (Tranche 1).
#
# DEVELOPMENT DATABASE ONLY for -Db gates: the backup gate restores into a
# throwaway flowpilot_restore_drill_* database and drops it; the extraction-
# memory gates run inside one rolled-back transaction; the automation
# conformance matrix COMMITS rows under an organization named
# "arch41-conformance-<stamp>" (it must, because the real job handler opens its
# own session). pg_dump / pg_restore 16 must be on PATH or in PG_BIN.

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch41_step1_extraction_memory"
$BeforeHead = "hm1_tier_price_per_key"
$ContractHead = "arch40_step3_contract_ai_settings"

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
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend

if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch41.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (drops the seven extraction-memory tables):" -ForegroundColor Yellow
    Write-Host "    python -m alembic downgrade $BeforeHead"
    exit 0
}

Step "1/8 CHECK - every file must be the 2f81cdf file, the Tranche 1 file or the ARCH-41 result"
RunCommand $Python @("apply_arch41.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }

Step "2/8 APPLY"
RunCommand $Python @("apply_arch41.py") "apply"

Step "3/8 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch41.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }

Step "4/8 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran on this database. ARCH-41 now sits beneath
    # it in the chain, so alembic believes ARCH-41 ran too; check the tables.
    $probe = (& $Python -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'].replace('+psycopg2','')); print(e.connect().execute(text(""select count(*) from information_schema.tables where table_name='extraction_memory_applications'"")).scalar())" 2>&1) -join ""
    if ($probe.Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-41 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-41 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (& $Python -m alembic current 2>&1) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after

Step "5/8 SEED TIERS - publish versions carrying capability.extraction_memory, carry live subscriptions forward"
$seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database)"
& $Python scripts\seed_quota_tiers.py --matrix

Step "6/8 EXECUTABLE BITS - deploy wrappers"
if (-not $SkipExecBits) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Push-Location $Root
        try { & git update-index --chmod=+x backend/deploy/bin/flowpilot-sweep backend/deploy/bin/flowpilot-sweep-watchdog } finally { Pop-Location }
    } else { Write-Host "git not found; run 'chmod +x backend/deploy/bin/*' on the server." -ForegroundColor Yellow }
}

Step "7/8 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }

Step "8/8 VERIFY - offline, database, mutation, build, regression chain"
$verifyArgs = @("verify_arch41.py")
if (-not $SkipDb) {
    if (-not $env:PG_BIN -and -not (Get-Command pg_dump -ErrorAction SilentlyContinue)) {
        $defaultBin = "C:\Program Files\PostgreSQL\16\bin"
        if (Test-Path (Join-Path $defaultBin "pg_dump.exe")) { $env:PG_BIN = $defaultBin }
        else { Fail "pg_dump not found. Install the PostgreSQL 16 client tools or set PG_BIN, or pass -SkipDb." }
    }
    $verifyArgs += "--db"
}
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch41.py"

Write-Host "`nARCH-41 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch41\  (verify_arch41.json, automation_conformance_live.json)" -ForegroundColor Green
Write-Host @"

Turn it on for a workspace: Document intelligence > Extraction memory > Mode.
  SHADOW (default) learns and measures, never changes an extraction.
  AUTO starts a trial per layout after five reviewed documents and applies
  memory only where the trial proves fewer corrections.
Schedule the nightly sweep on the server: deploy/cron.d/flowpilot-sweepers
already carries 'flowpilot-sweep extraction_memory --apply'.
"@
