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
# ARCH-45 - Universal Document Corroborator & Discrepancy Matrix.
# ARCH45-S1:runner
#
# Place apply_arch45.py and verify_arch45.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch45.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch45.ps1                   # apply, migrate, seed, build and certify
#   .\run_arch45.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch45.ps1 -Rollback         # restore the code
#
# Accepted starting point: 79f6277 "ARCH-44 DONE".
#
# DEVELOPMENT DATABASE ONLY for the -Db gates: the request/cache/job, HTTP,
# invalidation, entity, rules, hub, sweep and erasure gates run inside one
# rolled-back transaction; the regression run (verify_arch44 --db) rolls back too.
# Never point any of this at a database you back up with verify_arch10_step3.py.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch45_step1_corroboration"
$BeforeHead = "arch44_step1_table_intelligence"
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
foreach ($required in @("apply_arch45.py", "verify_arch45.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend
if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch45.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (drops the four ARCH-45 tables, restores the ARCH-44 review view and outbox vocabulary):" -ForegroundColor Yellow
    Write-Host "    python -m alembic downgrade $BeforeHead"
    exit 0
}
Step "1/8 CHECK - every file must be the 79f6277 file or the ARCH-45 result"
RunCommand $Python @("apply_arch45.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }
Step "2/8 APPLY"
RunCommand $Python @("apply_arch45.py") "apply"
Step "3/8 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch45.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }
Step "4/8 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran. ARCH-45 now sits beneath it, so alembic
    # believes ARCH-45 ran too; check for the tables.
    $probe = (& $Python -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'].replace('+psycopg2','')); print(e.connect().execute(text(""select count(*) from information_schema.tables where table_name='corroboration_runs'"")).scalar())" 2>&1) -join ""
    if ($probe.Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-45 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-45 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (& $Python -m alembic current 2>&1) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after
Step "5/8 SEED TIERS - publish an Enterprise version carrying capability.universal_corroborator, carry live subscriptions forward"
$seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database)"
& $Python scripts\seed_quota_tiers.py --matrix
Step "6/8 EXECUTABLE BITS - deploy wrappers and the new sweep script"
if (-not $SkipExecBits) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Push-Location $Root
        try { & git update-index --add --chmod=+x backend/deploy/bin/flowpilot-sweep backend/deploy/bin/flowpilot-sweep-watchdog backend/scripts/sweep_corroboration.py } finally { Pop-Location }
    } else { Write-Host "git not found; run 'chmod +x backend/deploy/bin/* backend/scripts/sweep_corroboration.py' on the server." -ForegroundColor Yellow }
}
Step "7/8 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }
Step "8/8 VERIFY - offline, database, mutation, build, regression"
$verifyArgs = @("verify_arch45.py")
if (-not $SkipDb) { $verifyArgs += "--db" }
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch45.py"
Write-Host "`nARCH-45 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch45\verify_arch45.json" -ForegroundColor Green
Write-Host @"
Where to find it: Enterprise processing > Document corroborator (Enterprise plan),
and "Comparisons" (with "Compare with...") on every document's Extracted Entities tab.
Comparisons run on the ENRICH worker profile (corroboration.run). Material
differences land in the review hub under "Comparisons", and the Flow Builder
trigger "Documents disagree" (corroboration.discrepancies) is live.
The nightly sweep is scheduled in deploy/cron.d/flowpilot-sweepers:
    deploy/bin/flowpilot-sweep corroboration --apply
"@
