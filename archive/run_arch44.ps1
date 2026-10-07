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
# ARCH-44 - Complex Table & Hierarchical Grid Extractor.
# ARCH44-S1:runner
#
# Place apply_arch44.py and verify_arch44.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch44.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch44.ps1                   # apply, migrate, seed, build and certify
#   .\run_arch44.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch44.ps1 -Rollback         # restore the code
#
# Accepted starting point: e6fbbf1 "ARCH-43 DONE".
#
# DEVELOPMENT DATABASE ONLY for the -Db gates: the extraction, HTTP, hub,
# job and erasure gates run inside one rolled-back transaction; the regression
# run (verify_arch43 --db) rolls back too.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch44_step1_table_intelligence"
$BeforeHead = "arch43_step1_case_intelligence"
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
foreach ($required in @("apply_arch44.py", "verify_arch44.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend
if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch44.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (drops the four ARCH-44 tables, restores the ARCH-43 review view and outbox vocabulary):" -ForegroundColor Yellow
    Write-Host "    python -m alembic downgrade $BeforeHead"
    exit 0
}
Step "1/8 CHECK - every file must be the e6fbbf1 file or the ARCH-44 result"
RunCommand $Python @("apply_arch44.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }
Step "2/8 APPLY"
RunCommand $Python @("apply_arch44.py") "apply"
Step "3/8 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch44.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }
Step "4/8 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran. ARCH-44 now sits beneath it, so alembic
    # believes ARCH-44 ran too; check for the tables.
    $probe = (& $Python -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'].replace('+psycopg2','')); print(e.connect().execute(text(""select count(*) from information_schema.tables where table_name='extracted_tables'"")).scalar())" 2>&1) -join ""
    if ($probe.Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-44 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-44 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (& $Python -m alembic current 2>&1) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after
Step "5/8 SEED TIERS - publish versions carrying capability.table_intelligence, carry live subscriptions forward"
$seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database)"
& $Python scripts\seed_quota_tiers.py --matrix
Step "6/8 EXECUTABLE BITS - deploy wrappers and the new backfill script"
if (-not $SkipExecBits) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Push-Location $Root
        try { & git update-index --add --chmod=+x backend/deploy/bin/flowpilot-sweep backend/deploy/bin/flowpilot-sweep-watchdog backend/scripts/backfill_tables.py } finally { Pop-Location }
    } else { Write-Host "git not found; run 'chmod +x backend/deploy/bin/* backend/scripts/backfill_tables.py' on the server." -ForegroundColor Yellow }
}
Step "7/8 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }
Step "8/8 VERIFY - offline, database, mutation, build, regression"
$verifyArgs = @("verify_arch44.py")
if (-not $SkipDb) { $verifyArgs += "--db" }
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch44.py"
Write-Host "`nARCH-44 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch44\verify_arch44.json" -ForegroundColor Green
Write-Host @"
Where to find it: Document intelligence > Tables (Business and Enterprise), and
"Tables in this document" on every document's Extracted Entities tab.
New documents are scanned for tables after enrichment (OCR worker profile).
To extract tables from documents processed before the upgrade, once:
    python scripts\backfill_tables.py            (report)
    python scripts\backfill_tables.py --apply    (queue the jobs)
Tables whose figures do not reconcile land in the review hub under "Tables",
and the Flow Builder trigger "Table does not reconcile" (table.flagged) is live.
"@
