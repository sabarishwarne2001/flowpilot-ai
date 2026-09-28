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
# ARCH-48 - Real-Time Collaborative Review & Live Presence.
# ARCH48-S1:runner
#
# Place apply_arch48.py and verify_arch48.py in backend\ and this file in the
# repository root, then:
#
#   .\run_arch48.ps1 -CheckOnly        # validate the tree; write nothing
#   .\run_arch48.ps1                   # apply, migrate, seed, build and certify
#   .\run_arch48.ps1 -AllowUnpriced    # seed tier versions without gateway price ids (dev)
#   .\run_arch48.ps1 -SeedPriceBook    # a FRESH database: publish the price book before the tiers
#   .\run_arch48.ps1 -Rollback         # restore the code
#
# Accepted starting point: 7680b0c "ARCH-47 DONE" (with or without the two
# handoff prompts ARCH-47's apply writes). Every file is brought to this
# package's result; any other local change is refused.
#
# No new dependency: the WebSocket server and client (websockets 15.0), Redis
# (redis 8.1.0) and uvicorn are already pinned in requirements.txt. REDIS_URL
# must point at the Redis the API already uses: the live channel fans out
# through it across API workers (no new service).
#
# DEVELOPMENT DATABASE ONLY for the -Db gates. The HTTP gates run inside one
# rolled-back transaction. The WebSocket, race, lock-expiry, fan-out and Caddy
# gates COMMIT dedicated organizations (separate connections, threads and
# processes cannot see a rolled-back transaction) with the audit writers
# silenced, and delete them afterwards. The fan-out gate starts two uvicorn
# processes on free localhost ports; the Caddy gate starts the real Caddy
# ($env:CADDY or caddy on PATH) on another. The regression run
# (verify_arch47 --db) rolls back too.
# Never point any of this at a database you back up with verify_arch10_step3.py.
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch48_step1_collaborative_review"
$BeforeHead = "arch47_step1_erp_posting"
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
foreach ($required in @("apply_arch48.py", "verify_arch48.py")) {
    if (-not (Test-Path (Join-Path $Backend $required))) { Fail "backend\$required not found. Copy it into backend\ first." }
}
Write-Host "Python: $Python"
Set-Location $Backend
if ($Rollback) {
    Step "ROLLBACK"
    RunCommand $Python @("apply_arch48.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (drops the four ARCH-48 tables; the hub view and the outbox vocabulary were never changed):" -ForegroundColor Yellow
    Write-Host "    python -m alembic downgrade $BeforeHead"
    exit 0
}
Step "1/9 CHECK - every file must be the 7680b0c file or the ARCH-48 result"
RunCommand $Python @("apply_arch48.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }
Step "2/9 APPLY"
RunCommand $Python @("apply_arch48.py") "apply"
Step "3/9 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch48.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }
Step "4/9 DEPENDENCIES - websockets, redis, uvicorn (already pinned) and a reachable Redis"
RunCommand $Python @("-c", "import websockets, redis, uvicorn; print('websockets', websockets.__version__, '- redis', redis.__version__, '- uvicorn', uvicorn.__version__)") "a dependency is missing: pip install -r requirements.txt"
RunCommand $Python @("-c", "from app.core.config import settings; import redis; u = settings.REDIS_URL.get_secret_value() if settings.REDIS_URL else ''; assert u, 'REDIS_URL is not set'; redis.Redis.from_url(u, socket_connect_timeout=3).ping(); print('redis reachable')") "Redis is not reachable at REDIS_URL (the live channel fans out through it)"
Step "5/9 MIGRATE - to $ReleaseHead (never 'head': the contract step stays held)"
$current = (& $Python -m alembic current 2>&1) -join "`n"
Write-Host $current
if ($current -match $ContractHead) {
    # The contract step already ran. ARCH-48 now sits beneath it, so alembic
    # believes ARCH-48 ran too; check for the tables.
    $probe = (& $Python -c "import os; from sqlalchemy import create_engine, text; e=create_engine(os.environ['DATABASE_URL'].replace('+psycopg2','')); print(e.connect().execute(text(""select count(*) from information_schema.tables where table_name='review_locks'"")).scalar())" 2>&1) -join ""
    if ($probe.Trim() -ne "1") {
        Write-Host "Contract step already applied; applying ARCH-48 underneath it (stamp, upgrade, stamp back)." -ForegroundColor Yellow
        RunCommand $Python @("-m", "alembic", "stamp", $BeforeHead) "alembic stamp $BeforeHead"
        RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
        RunCommand $Python @("-m", "alembic", "stamp", $ContractHead) "alembic stamp $ContractHead"
    } else { Write-Host "ARCH-48 tables already present." }
} else {
    RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
}
$after = (& $Python -m alembic current 2>&1) -join "`n"
if ($after -notmatch $ReleaseHead -and $after -notmatch $ContractHead) { Fail "database is not at $ReleaseHead after the upgrade: $after" }
Write-Host $after
Step "6/9 SEED TIERS - publish an Enterprise version carrying capability.collaborative_review, carry live subscriptions forward"
if ($SeedPriceBook) {
    RunCommand $Python @("scripts\seed_price_book.py") "seed_price_book (a fresh database needs the price book before the tiers)"
}
$seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
RunCommand $Python $seedArgs "seed_quota_tiers (set GATEWAY_PRICE_ID_* or pass -AllowUnpriced on a dev database; on a fresh database pass -SeedPriceBook)"
& $Python scripts\seed_quota_tiers.py --matrix
Step "7/9 INGRESS - the Caddyfile routes the live channel and drops Sec-WebSocket-Protocol from its access log"
$caddy = $env:CADDY
if (-not $caddy) { $found = Get-Command caddy -ErrorAction SilentlyContinue; if ($found) { $caddy = $found.Source } }
if ($caddy) {
    $env:APP_DOMAIN = "app.example.test"; $env:ACME_CONTACT_EMAIL = "ops@example.test"
    RunCommand $caddy @("validate", "--config", "deploy\Caddyfile", "--adapter", "caddyfile") "caddy validate"
    Remove-Item Env:\APP_DOMAIN; Remove-Item Env:\ACME_CONTACT_EMAIL
} else { Write-Host "No caddy binary (set `$env:CADDY): the Caddy gates L1 and C1 will be reported as not run." -ForegroundColor Yellow }
Step "8/9 NPM INSTALL"
if (-not $SkipBuild) {
    Set-Location $Frontend
    RunCommand "npm.cmd" @("install", "--no-audit", "--no-fund") "npm install"
    Set-Location $Backend
} else { Write-Host "skipped (-SkipBuild)" }
Step "9/9 VERIFY - offline, database, mutation, build, regression"
$verifyArgs = @("verify_arch48.py")
if (-not $SkipDb) { $verifyArgs += "--db" }
if (-not $SkipMutate) { $verifyArgs += "--mutate" }
if (-not $SkipBuild) { $verifyArgs += "--build" }
if (-not $SkipRegression) { $verifyArgs += "--regression" }
RunCommand $Python $verifyArgs "verify_arch48.py"
Write-Host "`nARCH-48 applied, migrated and certified." -ForegroundColor Green
Write-Host "Evidence: backend\evidence\arch48\verify_arch48.json" -ForegroundColor Green
Write-Host @"
Where to find it: the Review hub (every plan) now carries each item's version, so a
decision someone else made first is refused instead of overwritten. On Enterprise the hub
goes live: a Live pill in the header, avatars of whoever else has an item open, a lock
badge while someone decides (opening Resolve takes the lock; a workspace admin can break
it), and a discussion button per item (threads on the item, a field or a paragraph of
its document; @-mentions notify in the app). Keyboard: c opens the discussion.
Deploy: REDIS_URL must be set on every API worker (fan-out); Caddy routes
/api/v1/workspaces/*/review/collab/live (deploy/Caddyfile). Nothing new to schedule.
"@
