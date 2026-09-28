[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipDb,
    [switch]$SkipBuild,
    [switch]$SkipMutate,
    [switch]$SkipRegression,
    [switch]$AllowUnpriced,
    [switch]$SeedPriceBook,
    [switch]$Rollback
)
# ARCH-49 - Process Intelligence & the Governed Exception Agent.
# ARCH49-S1:runner
#
# Place apply_arch49.py and verify_arch49.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch49.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch49.ps1                   # apply, migrate, seed, build and certify
#   .\run_arch49.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch49.ps1 -SeedPriceBook    # a FRESH database: publish the price book before the tiers
#   .\run_arch49.ps1 -Rollback         # restore the code
#
# Accepted starting point: 5ac6496 "ARCH-48 DONE". Every file is brought to this
# package's result; any other local change is refused.
#
# No new dependency and no new recurring cost: the SLA model is scikit-learn
# 1.9.0 (already pinned), the exception agent is model-free (no LLM call, no
# outbound host), and the sweep is one more line in the existing cron file
# (deploy/cron.d/flowpilot-sweepers) dispatched by deploy/bin/flowpilot-sweep.
#
# DEVELOPMENT DATABASE ONLY for the -Db gates. The HTTP and agent gates run
# inside one rolled-back transaction. The race and sweep gates COMMIT dedicated
# organizations on the real Enterprise tier (separate connections cannot see a
# rolled-back transaction) with the audit writers silenced, and delete them
# afterwards. The regression run (verify_arch48 --db) commits and deletes its
# own organizations too.
# Never point any of this at a database you back up with verify_arch10_step3.py.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch49_step1_process_intelligence"
$BeforeHead = "arch48_step1_collaborative_review"
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
foreach ($required in @("apply_arch49.py", "verify_arch49.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend
if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch49.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (drops the nine ARCH-49 tables and restores ARCH-47's outbox vocabulary):" -ForegroundColor Yellow
    Write-Host "    python -m alembic downgrade $BeforeHead"
    exit 0
}
Step "1/8 CHECK - every file must be the 5ac6496 file or the ARCH-49 result"
RunCommand $Python @("apply_arch49.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }
Step "2/8 APPLY"
RunCommand $Python @("apply_arch49.py") "apply"
Step "3/8 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch49.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }
Step "4/8 DEPENDENCIES - scikit-learn 1.9.0 and numpy (already pinned)"
RunCommand $Python @("-c", "import sklearn, numpy; assert sklearn.__version__ == '1.9.0', sklearn.__version__; print('scikit-learn', sklearn.__version__, '- numpy', numpy.__version__)") "scikit-learn 1.9.0 is missing: pip install -r requirements.txt"
Step "5/8 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran. ARCH-49 now sits beneath it, so alembic
    # believes ARCH-49 ran too; check for the tables.
    $probe = (& $Python -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'].replace('+psycopg2','')); print(e.connect().execute(text(""select count(*) from information_schema.tables where table_name='agent_proposals'"")).scalar())" 2>&1) -join ""
    if ($probe.Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-49 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-49 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (& $Python -m alembic current 2>&1) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after
Step "6/8 SEED TIERS - publish an Enterprise version carrying capability.process_intelligence, carry live subscriptions forward"
if ($SeedPriceBook) {
    RunCommand $Python @("scripts\seed_price_book.py") "seed_price_book (a fresh database needs the price book before the tiers)"
}
$seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database; on a fresh database pass -SeedPriceBook)"
& $Python scripts\seed_quota_tiers.py --matrix
Step "7/8 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }
Step "8/8 VERIFY - offline, database, mutation, build, regression"
$verifyArgs = @("verify_arch49.py")
if (-not $SkipDb) { $verifyArgs += "--db" }
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch49.py"
Write-Host "`nARCH-49 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch49\verify_arch49.json" -ForegroundColor Green
Write-Host @"
Where to find it: Enterprise processing > Process intelligence (Enterprise plans). Overview
(the event log and what is at risk), Discovery (the directly-follows graph and the variants
of documents, cases, postings, review items, findings and flow runs, each with its cost to
serve), Conformance (flows and case templates replayed on what happened), Service levels
(targets, the breach models and their held-out Brier check, open items at risk), Cost to
serve, and the Exception agent (its proposals and its policy). The Review hub shows the
agent's proposal on each item for contributors: approve, reject (a reason), or undo a
scheduled automatic resolution. Automatic resolution is OFF until an admin switches it on,
and only covers decisions your calibrated autonomy bounds.
Deploy: nothing new to run. The sweep is already in deploy/cron.d/flowpilot-sweepers
(every 15 minutes: flowpilot-sweep process --apply); a LIGHT worker runs the jobs.
A new Flow Builder trigger: "SLA breach predicted".
"@
