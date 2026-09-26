[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipDb,
    [switch]$SkipBuild,
    [switch]$SkipMutate,
    [switch]$SkipRegression,
    [switch]$SkipExecBits,
    [switch]$AllowUnpriced,
    [switch]$SeedPriceBook,
    [switch]$Rollback
)
# ARCH-47 - ERP & System-of-Record Posting.
# ARCH47-S1:runner
#
# Place apply_arch47.py and verify_arch47.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch47.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch47.ps1                   # apply, install, migrate, seed, build and certify
#   .\run_arch47.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch47.ps1 -SeedPriceBook    # a FRESH database: publish the price book before the tiers
#   .\run_arch47.ps1 -Rollback         # restore the code
#
# Accepted starting point: ff82586 "ARCH-46 DONE".
#
# New dependency: paramiko (SFTP delivery; open source, no service, no cost)
# and PyNaCl, its signing backend - both pinned in requirements.txt.
#
# DEVELOPMENT DATABASE ONLY for the -Db gates: planning, HTTP, the mock ERP
# and SFTP servers (local, self-signed, nothing leaves the machine), the hub,
# Flow Builder, the sweep and erasure run inside one rolled-back transaction.
# The concurrency gate X1 COMMITS a dedicated organization (separate
# connections cannot see a rolled-back transaction) and deletes it afterwards
# (it writes no audit rows, since audit_logs is append-only). The regression
# run (verify_arch46 --db) rolls back too.
# Never point any of this at a database you back up with verify_arch10_step3.py.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch47_step1_erp_posting"
$BeforeHead = "arch46_step1_obligations"
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
foreach ($required in @("apply_arch47.py", "verify_arch47.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend
if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch47.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (drops the five ARCH-47 tables, restores the ARCH-46 review view and outbox vocabulary):" -ForegroundColor Yellow
    Write-Host "    python -m alembic downgrade $BeforeHead"
    exit 0
}
Step "1/10 CHECK - every file must be the ff82586 file or the ARCH-47 result"
RunCommand $Python @("apply_arch47.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }
Step "2/10 APPLY"
RunCommand $Python @("apply_arch47.py") "apply"
Step "3/10 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch47.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }
Step "4/10 DEPENDENCIES - paramiko and PyNaCl (SFTP), lxml and jsonschema (schema validation)"
RunCommand $Python @("-m", "pip", "install", "paramiko==3.5.1", "PyNaCl==1.6.2") "pip install paramiko"
RunCommand $Python @("-c", "import paramiko, lxml.etree, jsonschema; print('paramiko', paramiko.__version__, '- lxml and jsonschema ok')") "a dependency is missing: pip install -r requirements.txt"
RunCommand $Python @("-c", "from app.services.erp.formats import xsd; e = xsd.warm(); print(e or 'the vendored UBL 2.1 / ECMA-376 / Tally schemas compile'); raise SystemExit(1 if e else 0)") "the vendored schemas do not compile"
Step "5/10 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran. ARCH-47 now sits beneath it, so alembic
    # believes ARCH-47 ran too; check for the tables.
    $probe = (& $Python -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'].replace('+psycopg2','')); print(e.connect().execute(text(""select count(*) from information_schema.tables where table_name='erp_postings'"")).scalar())" 2>&1) -join ""
    if ($probe.Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-47 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-47 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (& $Python -m alembic current 2>&1) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after
Step "6/10 SEED TIERS - publish Business and Enterprise versions carrying capability.erp_posting, carry live subscriptions forward"
if ($SeedPriceBook) {
    RunCommand $Python @("scripts\seed_price_book.py") "seed_price_book (a fresh database needs the price book before the tiers)"
}
$seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database; on a fresh database pass -SeedPriceBook)"
& $Python scripts\seed_quota_tiers.py --matrix
Step "7/10 EXECUTABLE BITS - deploy wrappers and the new sweep script"
if (-not $SkipExecBits) {
    if (Get-Command git -ErrorAction SilentlyContinue) {
        Push-Location $Root
        try { & git update-index --add --chmod=+x backend/deploy/bin/flowpilot-sweep backend/deploy/bin/flowpilot-sweep-watchdog backend/scripts/sweep_erp_postings.py backend/scripts/erp_mock_targets.py } finally { Pop-Location }
    } else { Write-Host "git not found; run 'chmod +x backend/deploy/bin/* backend/scripts/sweep_erp_postings.py' on the server." -ForegroundColor Yellow }
}
Step "8/10 CONFORMANCE - the live automation matrix, erp.post included (COMMITS a dedicated organization: development database only)"
if (-not $SkipDb) {
    RunCommand $Python @("scripts\automation_conformance.py", "--json", "evidence\arch47\automation_conformance.json") "automation conformance (22 triggers, 8 actions, erp.post)"
} else { Write-Host "skipped (-SkipDb)" }
Step "9/10 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }
Step "10/10 VERIFY - offline, database, mutation, build, regression"
$verifyArgs = @("verify_arch47.py")
if (-not $SkipDb) { $verifyArgs += "--db" }
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch47.py"
Write-Host "`nARCH-47 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch47\verify_arch47.json (golden files in backend\evidence\arch47\golden\)" -ForegroundColor Green
Write-Host @"
Where to find it: ERP posting in the workspace navigation (Business and Enterprise plans):
Postings (the ledger), Ready to post (approved matches, accepted tables, completed cases),
Targets (format, delivery, credential, test connection, versioned mappings with preview)
and Lookup tables. An ERP postings tab is on every document's details; exceptions wait
in the review hub under "ERP postings". The Flow Builder action "Post to ERP" (erp.post)
runs on "Three-way match approved" and "Case completed"; the trigger "ERP posting failed"
is live. Deliveries run on the LIGHT worker profile (erp.deliver_posting). The sweep runs
every 10 minutes from deploy/cron.d/flowpilot-sweepers:
    deploy/bin/flowpilot-sweep erp_postings --apply
Fill each target's lookup tables (<prefix>_vendors, _accounts incl. TAX and AP, _items,
_settings) before posting: a missing code fails the posting with the line and field named.
"@
