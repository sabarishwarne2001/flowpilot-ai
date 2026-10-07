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
# ARCH-42 - Entity Resolution & Document Knowledge Graph.
#
# Place apply_arch42.py and verify_arch42.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch42.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch42.ps1                   # apply, migrate, seed, build and certify
#   .\run_arch42.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch42.ps1 -Rollback         # restore the code
#
# Accepted starting point: d59baed "ARCH-41 DONE".
#
# DEVELOPMENT DATABASE ONLY for the -Db gates: the entity-graph gates run
# inside one rolled-back transaction; the regression run (verify_arch41 --db)
# commits its automation conformance rows under "arch41-conformance-<stamp>".
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch42_step1_entity_graph"
$BeforeHead = "arch41_step1_extraction_memory"
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
foreach ($required in @("apply_arch42.py", "verify_arch42.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend
if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch42.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (drops the six entity tables, restores the ARCH-40 review view):" -ForegroundColor Yellow
    Write-Host "    python -m alembic downgrade $BeforeHead"
    exit 0
}
Step "1/8 CHECK - every file must be the d59baed file or the ARCH-42 result"
RunCommand $Python @("apply_arch42.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }
Step "2/8 APPLY"
RunCommand $Python @("apply_arch42.py") "apply"
Step "3/8 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch42.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }
Step "4/8 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran. ARCH-42 now sits beneath it, so alembic
    # believes ARCH-42 ran too; check for the tables.
    $probe = (& $Python -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'].replace('+psycopg2','')); print(e.connect().execute(text(""select count(*) from information_schema.tables where table_name='entity_merge_candidates'"")).scalar())" 2>&1) -join ""
    if ($probe.Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-42 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-42 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (& $Python -m alembic current 2>&1) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after
Step "5/8 SEED TIERS - publish versions carrying capability.entity_graph, carry live subscriptions forward"
$seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database)"
& $Python scripts\seed_quota_tiers.py --matrix
Step "6/8 EXECUTABLE BITS - deploy wrappers and the new sweep script"
if (-not $SkipExecBits) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Push-Location $Root
        try { & git update-index --add --chmod=+x backend/deploy/bin/flowpilot-sweep backend/deploy/bin/flowpilot-sweep-watchdog backend/scripts/sweep_entities.py } finally { Pop-Location }
    } else { Write-Host "git not found; run 'chmod +x backend/deploy/bin/* backend/scripts/sweep_entities.py' on the server." -ForegroundColor Yellow }
}
Step "7/8 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }
Step "8/8 VERIFY - offline, database, mutation, build, regression"
$verifyArgs = @("verify_arch42.py")
if (-not $SkipDb) { $verifyArgs += "--db" }
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch42.py"
Write-Host "`nARCH-42 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch42\verify_arch42.json" -ForegroundColor Green
Write-Host @"
Where to find it: Document intelligence > Entity graph (Business and Enterprise).
New documents resolve on enrichment; to resolve documents processed before the
upgrade, run the sweep once:  python scripts\sweep_entities.py --apply
The nightly sweep is already scheduled on the server
('flowpilot-sweep entities --apply' in deploy/cron.d/flowpilot-sweepers).
Possible duplicates land in the review hub under "Entity merges".
"@


