<#
.SYNOPSIS
  FlowPilot AI - HARDENING-MASTER: check, apply, migrate, seed, build and verify.

.DESCRIPTION
  Place this file and apply_hardening_master.py in the repository root (the
  folder that holds backend\ and frontend\), then run:

    .\run_hardening_master.ps1 -CheckOnly        # verify the tree; write nothing
    .\run_hardening_master.ps1                   # full run (the sign-off run)
    .\run_hardening_master.ps1 -SkipChain        # skip the ARCH-31..40 regression chain
    .\run_hardening_master.ps1 -AllowUnpriced    # Enterprise product not created at Dodo yet
                                                 # (NOT a sign-off run: prints a warning)

  Steps: apply --check -> apply -> idempotency re-apply
  -> alembic upgrade hm1_tier_price_per_key (never 'head': the lossy ARCH-40
     contract step stays behind its own flag)
  -> seed_quota_tiers.py --carry-forward (publishes the four-tier matrix,
     moves live subscribers onto a no-worse version of their own plan)
  -> npm install
  -> verify_hardening_master.py --db --mutation --frontend --previous --chain

  Before a sign-off run, create the Enterprise product at Dodo ($799 per seat
  per month, monthly) and put its id in backend\.env:

      GATEWAY_PRICE_ID_ENTERPRISE=pdt_...

  Developer and Business keep their existing product ids (a gateway price now
  identifies a plan, not one packaging of it), so they need nothing new.

  The --db gates create organizations named "hm-gate ..." and "hardening-..."
  in the configured database. Run against a DEVELOPMENT database only.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipChain,
    [switch]$SkipFrontend,
    [switch]$NoDb,
    [switch]$AllowUnpriced,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ExpectedHead = "hm1_tier_price_per_key"
$ReleaseHead = "arch40_step2a_review_view_paths"
$ContractHead = "arch40_step3_contract_ai_settings"

function Step([string]$text) { Write-Host ""; Write-Host "=== $text ===" -ForegroundColor Cyan }
function Fail([string]$text) { Write-Host "FAILED: $text" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $Backend "app")) -or -not (Test-Path (Join-Path $Frontend "src"))) {
    Fail "Run this script from the FlowPilot repository root (backend\ and frontend\ must be next to it)."
}
if (-not (Test-Path (Join-Path $Root "apply_hardening_master.py"))) {
    Fail "apply_hardening_master.py must sit next to this script in the repository root."
}

if (-not $Python) {
    $venvPython = Join-Path $Backend "venv\Scripts\python.exe"
    $dotVenvPython = Join-Path $Backend ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { $Python = $venvPython }
    elseif (Test-Path $dotVenvPython) { $Python = $dotVenvPython }
    else { $Python = "python" }
}
Write-Host "Python: $Python"

Step "1/9 Apply engine: pre-flight check"
& $Python (Join-Path $Root "apply_hardening_master.py") --root $Root --check
if ($LASTEXITCODE -ne 0) { Fail "the tree is not at the TIER 4 baseline (fd7f062) or the master state. Nothing was written." }
if ($CheckOnly) { Write-Host "Check only: no files were written." -ForegroundColor Green; exit 0 }

Step "2/9 Apply"
& $Python (Join-Path $Root "apply_hardening_master.py") --root $Root
if ($LASTEXITCODE -ne 0) { Fail "apply did not complete (the backup was restored if a write failed)." }

Step "3/9 Idempotency: a second apply must change nothing"
$second = & $Python (Join-Path $Root "apply_hardening_master.py") --root $Root 2>&1 | Out-String
Write-Host $second.Trim()
if ($second -notmatch "No changes") { Fail "second apply was not a no-op." }

# backend\.env -> DATABASE_URL (read by the milestone gates before settings)
# and GATEWAY_PRICE_ID_* (read by the seed from the process environment).
$envFile = Join-Path $Backend ".env"
$vals = @{}
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*([A-Z_0-9]+)\s*=\s*([^#]*?)\s*(#.*)?$') { $vals[$Matches[1]] = $Matches[2].Trim().Trim('"') }
    }
}
if (-not $env:DATABASE_URL) {
    if ($vals["DATABASE_URL"]) { $env:DATABASE_URL = $vals["DATABASE_URL"] }
    elseif ($vals["POSTGRES_USER"]) {
        $h = if ($vals["POSTGRES_HOST"]) { $vals["POSTGRES_HOST"] } else { "localhost" }
        $p = if ($vals["POSTGRES_PORT"]) { $vals["POSTGRES_PORT"] } else { "5432" }
        $env:DATABASE_URL = "postgresql://$($vals['POSTGRES_USER']):$($vals['POSTGRES_PASSWORD'])@${h}:$p/$($vals['POSTGRES_DB'])"
    }
}
foreach ($name in @("GATEWAY_PRICE_ID_DEVELOPER", "GATEWAY_PRICE_ID_BUSINESS", "GATEWAY_PRICE_ID_ENTERPRISE")) {
    if (-not [Environment]::GetEnvironmentVariable($name) -and $vals[$name]) {
        [Environment]::SetEnvironmentVariable($name, $vals[$name])
    }
}

Push-Location $Backend
try {
    Step "4/9 Database: upgrade to $ExpectedHead (not 'head')"
    if (-not $NoDb) {
        $current = & $Python -m alembic current 2>&1 | Out-String
        Write-Host $current.Trim()
        if ($current -match $ContractHead) {
            # The contract step was already applied. hm1 now sits before it in
            # the chain, so alembic believes hm1 ran; check the database.
            $probe = & $Python -c "from sqlalchemy import create_engine, text; import os; e=create_engine(os.environ['DATABASE_URL']); print(e.connect().execute(text(""select count(*) from pg_trigger where tgname='trg_quota_tiers_price_id_single_key'"")).scalar())" 2>&1 | Out-String
            if ($probe.Trim() -ne "1") {
                Write-Host "Contract step already applied; applying hm1 underneath it (stamp only, no ARCH-40 DDL re-runs)." -ForegroundColor Yellow
                & $Python -m alembic stamp $ReleaseHead; if ($LASTEXITCODE -ne 0) { Fail "alembic stamp $ReleaseHead failed." }
                & $Python -m alembic upgrade $ExpectedHead; if ($LASTEXITCODE -ne 0) { Fail "alembic upgrade $ExpectedHead failed." }
                & $Python -m alembic stamp $ContractHead; if ($LASTEXITCODE -ne 0) { Fail "alembic stamp $ContractHead failed." }
            } else { Write-Host "hm1 already in place." }
        } else {
            & $Python -m alembic upgrade $ExpectedHead
            if ($LASTEXITCODE -ne 0) { Fail "alembic upgrade $ExpectedHead failed." }
            $after = & $Python -m alembic current 2>&1 | Out-String
            if ($after -notmatch $ExpectedHead) { Fail "database is not at $ExpectedHead after the upgrade: $after" }
        }
    } else { Write-Host "skipped (-NoDb)" }

    Step "5/9 Quota tiers: publish the four-tier matrix and carry live subscribers forward"
    if (-not $NoDb) {
        if (-not $env:GATEWAY_PRICE_ID_ENTERPRISE -and -not $AllowUnpriced) {
            Fail "GATEWAY_PRICE_ID_ENTERPRISE is not set. Create the Enterprise product at Dodo (799.00 USD per seat per month) and add its id to backend\.env, or re-run with -AllowUnpriced (not a sign-off run)."
        }
        $seedArgs = @("scripts\seed_quota_tiers.py", "--carry-forward")
        if ($AllowUnpriced) { $seedArgs += "--allow-unpriced" }
        & $Python @seedArgs
        if ($LASTEXITCODE -ne 0) { Fail "seed_quota_tiers.py failed (see the message above)." }
        Write-Host "Second seed run (must report every tier current):"
        $again = & $Python @seedArgs 2>&1 | Out-String
        Write-Host $again.Trim()
        if ($again -match "publish v") { Fail "the seed is not idempotent: a second run published again." }
    } else { Write-Host "skipped (-NoDb)" }
}
finally { Pop-Location }

Step "6/9 Frontend dependencies"
if (-not $SkipFrontend) {
    Push-Location $Frontend
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { Fail "npm install failed." }
    } finally { Pop-Location }
} else { Write-Host "skipped (-SkipFrontend)" }

Step "7/9 Verification harness (HARDENING-MASTER + tiers 1-3 + final + chain)"
Push-Location $Backend
try {
    $verifyArgs = @("verify_hardening_master.py", "--mutation", "--previous")
    if (-not $NoDb) { $verifyArgs += "--db" }
    if (-not $SkipFrontend) { $verifyArgs += "--frontend" }
    if (-not $SkipChain) { $verifyArgs += "--chain" }
    if ($AllowUnpriced) { $verifyArgs += "--allow-unpriced-enterprise" }
    & $Python @verifyArgs
    $verifyExit = $LASTEXITCODE
}
finally { Pop-Location }

Step "8/9 Rollback reminder"
Write-Host "Code:      $Python apply_hardening_master.py --rollback"
Write-Host "Database:  cd backend; $Python -m alembic downgrade $ReleaseHead  (restores the global price index)"

Step "9/9 Summary"
$signoff = (-not $NoDb) -and (-not $SkipChain) -and (-not $SkipFrontend) -and (-not $AllowUnpriced)
if ($verifyExit -eq 0 -and $signoff) {
    Write-Host "HARDENING-MASTER: ALL GATES PASSED - unconditional enterprise sign-off holds for this tree." -ForegroundColor Green
    exit 0
}
if ($verifyExit -eq 0) {
    Write-Host "HARDENING-MASTER: all gates that ran PASSED, but this was not a sign-off run (a skip switch or -AllowUnpriced was used)." -ForegroundColor Yellow
    exit 0
}
Write-Host "HARDENING-MASTER: GATES FAILED (see output above). Sign-off does NOT hold." -ForegroundColor Red
exit 1
