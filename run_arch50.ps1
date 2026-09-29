[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipDb,
    [switch]$SkipBuild,
    [switch]$SkipMutate,
    [switch]$SkipRegression,
    [switch]$AllowUnpriced,
    [switch]$SeedPriceBook,
    [switch]$Rollback,
    [switch]$WithDatabase,
    [switch]$GA2,
    [string]$GA2Database = "flowpilot_ga2",
    [switch]$PitrDrill
)
# ARCH-50 - Sovereign Edition, RevOps, DR Certification & GA-2.
# ARCH50-S1:runner
#
# Place apply_arch50.py and verify_arch50.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch50.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch50.ps1                   # apply, migrate, seed, build and certify
#   .\run_arch50.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch50.ps1 -SeedPriceBook    # a FRESH database: publish the price book before the tiers
#   .\run_arch50.ps1 -GA2              # GA-2: a CLEAN database (drop + create "flowpilot_ga2"), every
#                                      #   verifier 31-50 + hardening + the scripts, one after another
#   .\run_arch50.ps1 -PitrDrill        # the measured point-in-time-recovery drill on its own
#   .\run_arch50.ps1 -Rollback         # restore the code
#   .\run_arch50.ps1 -Rollback -WithDatabase
#                                      # downgrade the database to ARCH-49 FIRST (alembic needs this package's
#                                      #   migration file to find its way back), then restore the code
#
# Accepted starting point: b76347a "ARCH-49 DONE". Every file is brought to this
# package's result; any other local change is refused.
#
# No new dependency and no new recurring cost: Ed25519 is `cryptography` (pinned),
# the operator's local model speaks the OpenAI API through the pinned `openai` SDK,
# the SBOM is JSON written by scripts\release_manifest.py, and point-in-time
# recovery is PostgreSQL's own archive_command / pg_basebackup / restore_command.
# The daily RevOps sweep and the DR heartbeat / base backup / weekly drill are more
# lines in the existing cron file (deploy/cron.d/flowpilot-sweepers).
#
# WINDOWS NOTES. The PITR drill needs PostgreSQL's server binaries (initdb, pg_ctl,
# pg_basebackup) on PATH or under PG_BIN, and runs PostgreSQL as the CURRENT user
# (never as an administrator: PostgreSQL refuses to start elevated). The -GA2 run
# needs the same server the application uses (POSTGRES_HOST/PORT/USER/PASSWORD in
# backend\.env) and permission to CREATE DATABASE.
#
# DEVELOPMENT DATABASE ONLY for the -Db gates. The HTTP gates run inside one
# rolled-back transaction; the race, clock and chaos gates COMMIT dedicated
# organizations (audit writers captured) and delete them afterwards; the DR gate
# RECORDS its measured drills in dr_drills (that is what the console reads).
# Never point any of this at a database you back up with verify_arch10_step3.py:
# -GA2 runs that script, and only against a database whose name contains "ga2".
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch50_step1_sovereign_revops"
$BeforeHead = "arch49_step1_process_intelligence"
$ContractHead = "arch40_step3_contract_ai_settings"
$env:PYTHONUTF8 = "1"
$Python = "python"
# The same script runs under PowerShell 7 on Linux/macOS (the sandbox certified it there): script paths below use
# forward slashes, which Windows Python accepts too.
$OnWindows = ($env:OS -eq "Windows_NT")
$Npm = if ($OnWindows) { "npm.cmd" } else { "npm" }
foreach ($candidate in @((Join-Path $Backend ".venv\Scripts\python.exe"), (Join-Path $Backend "venv\Scripts\python.exe"),
                         (Join-Path $Backend ".venv/bin/python"), (Join-Path $Backend "venv/bin/python"))) {
    if (Test-Path $candidate) { $Python = $candidate; break }
}
function Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host "`nFAILED: $Text" -ForegroundColor Red; exit 1 }
function RunCommand([string]$Exe, [string[]]$CmdArgs, [string]$What) {
    & $Exe $CmdArgs
    if ($LASTEXITCODE -ne 0) { Fail "$What (exit $LASTEXITCODE)" }
}
# Python's output as text lines, stderr included. Windows PowerShell 5.1 turns a native command's stderr lines into
# error records, which "Stop" would make fatal (alembic logs to stderr), so the preference is relaxed here only.
function Capture([string[]]$CmdArgs) {
    $saved = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { return @(& $Python $CmdArgs 2>&1 | ForEach-Object { "$_" }) } finally { $ErrorActionPreference = $saved }
}
foreach ($required in @("apply_arch50.py", "verify_arch50.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend
if ($Rollback) {
    if ($WithDatabase) {
        Step "ROLLBACK 1/2 - DATABASE to $BeforeHead (drops the fourteen ARCH-50 tables and their three trigger functions)"
        $current = (Capture @("-m", "alembic", "current")) -join "`n"
        Write-Host $current
        if ($current -match $ContractHead) {
            # The contract step already ran and ARCH-50 was applied beneath it (step 5 stamps around it): undo
            # exactly ARCH-50 the same way -- stamp under, downgrade one revision, stamp back.
            Write-Host "Contract step applied; removing ARCH-50 underneath it (stamp, downgrade, stamp back)." -ForegroundColor Yellow
            RunCommand $Python @("-m", "alembic", "stamp", $ReleaseHead) "alembic stamp $ReleaseHead"
            RunCommand $Python @("-m", "alembic", "downgrade", $BeforeHead) "alembic downgrade $BeforeHead"
            RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
        } elseif ($current -match $ReleaseHead) {
            RunCommand $Python @("-m", "alembic", "downgrade", $BeforeHead) "alembic downgrade $BeforeHead"
        } else { Write-Host "The database is not at $ReleaseHead; nothing to downgrade." }
        Step "ROLLBACK 2/2 - CODE"
    } else {
        Step "ROLLBACK - CODE"
        Write-Host "If the database was migrated to $ReleaseHead, stop and run -Rollback -WithDatabase instead:" -ForegroundColor Yellow
        Write-Host "alembic can only downgrade it while this package's migration file is still here." -ForegroundColor Yellow
    }
    RunCommand $Python @("apply_arch50.py", "--rollback") "code rollback"
    exit 0
}
if ($PitrDrill) {
    Step "PITR DRILL - scratch primary, WAL archive, pg_basebackup, a crash, restore to a timestamp and to the end (timed)"
    RunCommand $Python @("scripts/dr_pitr.py", "drill", "--scratch", "--record", "--json", "evidence/arch50/pitr_drill.json") "PITR drill"
    exit 0
}
if ($GA2) {
    Step "GA-2 - the whole verification chain on a CLEAN database ($GA2Database)"
    RunCommand $Python @("scripts/ga2.py", "--database", $GA2Database, "--create") "GA-2 (see backend\evidence\ga2\summary.txt)"
    Write-Host "`nGA-2 passed. Report: backend\evidence\ga2\summary.txt and ga2.json." -ForegroundColor Green
    Write-Host "Restore the committed per-milestone evidence files the verifiers rewrote:  git checkout -- backend/evidence"
    exit 0
}
Step "1/8 CHECK - every file must be the b76347a file or the ARCH-50 result"
RunCommand $Python @("apply_arch50.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }
Step "2/8 APPLY"
RunCommand $Python @("apply_arch50.py") "apply"
Step "3/8 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch50.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }
Step "4/8 DEPENDENCIES - cryptography (Ed25519) and openai (the local model), both already pinned"
RunCommand $Python @("-c", "import cryptography, openai; from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey; Ed25519PrivateKey.generate(); print('cryptography', cryptography.__version__, '- openai', openai.__version__)") "cryptography / openai missing: pip install -r requirements.txt"
Step "5/8 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (Capture @("-m", "alembic", "current")) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran. ARCH-50 now sits beneath it, so alembic
    # believes ARCH-50 ran too; check for the tables.
    # No double quote inside the Python code: Windows PowerShell 5.1 strips embedded quotes from native arguments.
    $probe = Capture @("-c", "from sqlalchemy import inspect; from app.db.session import engine; print(int(inspect(engine).has_table('egress_policies')))")
    if ((($probe | Select-Object -Last 1) -as [string]).Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-50 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-50 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (Capture @("-m", "alembic", "current")) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after
Step "6/8 SEED TIERS - publish an Enterprise version carrying capability.egress_lockdown, carry live subscriptions forward"
if ($SeedPriceBook) {
    RunCommand $Python @("scripts/seed_price_book.py") "seed_price_book (a fresh database needs the price book before the tiers)"
}
$seedArgs = @("scripts/seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database; on a fresh database pass -SeedPriceBook)"
& $Python scripts/seed_quota_tiers.py --matrix
Step "7/8 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand $Npm @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }
Step "8/8 VERIFY - offline, database (incl. a MEASURED PITR drill), mutation, build, regression"
$verifyArgs = @("verify_arch50.py")
if (-not $SkipDb) { $verifyArgs += "--db" }
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch50.py"
Write-Host "`nARCH-50 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch50\verify_arch50.json" -ForegroundColor Green
Write-Host @"
Where to find it:
  Organization > Egress lockdown (Enterprise): switch the lockdown on, allow destinations
  (host, *.domain, IP or CIDR; per channel and port), test one, and see what was refused.
  Billing: annual plans, INR prices and promo codes at checkout; an invoiced contract and
  its invoices when the organization is on one.
  Platform admin > Sovereign edition: the deployment's egress mode and declared hosts, every
  refusal, the operator's local model (health), the licence, the release manifest, and
  disaster recovery (the latest measured drills against RPO 300 s / RTO 3600 s).
  Platform admin > Revenue operations: MRR / ARR per currency with movements, receivables,
  price books, promo codes and invoiced enterprise contracts.
Deploy (all optional, all operator settings in backend\.env):
  EGRESS_MODE=deny with EGRESS_OPERATOR_HOSTS for the hosts this deployment may reach;
  LOCAL_LLM_MODE=fallback|exclusive, LOCAL_LLM_BASE_URL, LOCAL_LLM_MODEL (llama.cpp, vLLM
  or Ollama behind an OpenAI-compatible URL); FLOWPILOT_EDITION=sovereign with a licence
  (scripts\licence_tool.py); PITR: archive_command = dr_pitr.py archive-wal (see its header).
  Cron (deploy/cron.d/flowpilot-sweepers): revops daily, dr-heartbeat every minute,
  base-backup nightly, pitr-drill weekly; the weekly restore drill now records itself.
"@
