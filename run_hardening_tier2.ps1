<#
.SYNOPSIS
  FlowPilot AI - Hardening Tier 2: check, apply, seed, build and verify.

.DESCRIPTION
  Place this file, apply_hardening_tier2.py and the certification document in
  the repository root (the folder that holds backend\ and frontend\), then run:

    .\run_hardening_tier1.ps1 -CheckOnly        # verify the tree; write nothing
    .\run_hardening_tier1.ps1                   # full run
    .\run_hardening_tier1.ps1 -SkipChain        # skip the ARCH-31..40 regression chain
    .\run_hardening_tier1.ps1 -AllowUnpriced    # seed tiers without gateway price ids

  Steps: apply --check -> apply -> idempotency re-apply -> alembic head check
  -> verify (tier 2 gates, plus the tier-1 harness re-run)
  -> npm ci -> verify_hardening_tier2.py (--db --mutation --frontend [--chain]).

  The --db gates create organizations named "hardening-t2-gate ..." in the
  configured database. Run against a DEVELOPMENT database only.
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipChain,
    [switch]$SkipFrontend,
    [switch]$NoDb,
    [switch]$AllowUnpriced,
    [int]$TierVersion = 2,
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ExpectedHead = "arch40_step2a_review_view_paths"

function Step([string]$text) { Write-Host ""; Write-Host "=== $text ===" -ForegroundColor Cyan }
function Fail([string]$text) { Write-Host "FAILED: $text" -ForegroundColor Red; exit 1 }

if (-not (Test-Path (Join-Path $Backend "app")) -or -not (Test-Path (Join-Path $Frontend "src"))) {
    Fail "Run this script from the FlowPilot repository root (backend\ and frontend\ must be next to it)."
}

# Prefer the backend virtual environment when one exists.
if (-not $Python) {
    $venvPython = Join-Path $Backend "venv\Scripts\python.exe"
    $dotVenvPython = Join-Path $Backend ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) { $Python = $venvPython }
    elseif (Test-Path $dotVenvPython) { $Python = $dotVenvPython }
    else { $Python = "python" }
}
Write-Host "Python: $Python"

Step "1/8 Apply engine: pre-flight check"
& $Python (Join-Path $Root "apply_hardening_tier2.py") --root $Root --check
if ($LASTEXITCODE -ne 0) { Fail "the tree is not at the tier-1 state (apply tier 1 first). Nothing was written." }
if ($CheckOnly) { Write-Host "Check only: no files were written." -ForegroundColor Green; exit 0 }

Step "2/8 Apply"
& $Python (Join-Path $Root "apply_hardening_tier2.py") --root $Root
if ($LASTEXITCODE -ne 0) { Fail "apply did not complete (the backup was restored if a write failed)." }

Step "3/8 Idempotency: a second apply must change nothing"
$second = & $Python (Join-Path $Root "apply_hardening_tier2.py") --root $Root 2>&1 | Out-String
Write-Host $second.Trim()
if ($second -notmatch "No changes") { Fail "second apply was not a no-op." }

# DATABASE_URL: the milestone gates read it before settings. Build it from
# backend\.env when it is not already set.
if (-not $env:DATABASE_URL) {
    $envFile = Join-Path $Backend ".env"
    if (Test-Path $envFile) {
        $vals = @{}
        foreach ($line in Get-Content $envFile) {
            if ($line -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$') { $vals[$Matches[1]] = $Matches[2].Trim('"') }
        }
        if ($vals["DATABASE_URL"]) { $env:DATABASE_URL = $vals["DATABASE_URL"] }
        elseif ($vals["POSTGRES_USER"]) {
            $h = if ($vals["POSTGRES_HOST"]) { $vals["POSTGRES_HOST"] } else { "localhost" }
            $p = if ($vals["POSTGRES_PORT"]) { $vals["POSTGRES_PORT"] } else { "5432" }
            $env:DATABASE_URL = "postgresql://$($vals['POSTGRES_USER']):$($vals['POSTGRES_PASSWORD'])@${h}:$p/$($vals['POSTGRES_DB'])"
        }
    }
}

Push-Location $Backend
try {
    Step "4/8 Database head"
    if (-not $NoDb) {
        $current = & $Python -m alembic current 2>&1 | Out-String
        Write-Host $current.Trim()
        if ($current -notmatch $ExpectedHead) {
            Write-Host "WARNING: expected head $ExpectedHead. Run 'alembic upgrade $ExpectedHead' first." -ForegroundColor Yellow
        }
    } else { Write-Host "skipped (-NoDb)" }

    Step "5/8 Quota tiers (already seeded by tier 1; skipped)"
    Write-Host "Tier 2 adds no plan changes. (Tier 1 seeded capability tiers.)"
}
finally { Pop-Location }

Step "6/8 Frontend dependencies"
if (-not $SkipFrontend) {
    Push-Location $Frontend
    try {
        npm install --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { Fail "npm ci failed." }
    } finally { Pop-Location }
} else { Write-Host "skipped (-SkipFrontend)" }

Step "7/8 Verification harness"
Push-Location $Backend
try {
    $verifyArgs = @("verify_hardening_tier2.py", "--mutation", "--tier1")
    if (-not $NoDb) { $verifyArgs += "--db" }
    if (-not $SkipFrontend) { $verifyArgs += "--frontend" }
    if (-not $SkipChain) { $verifyArgs += "--chain" }
    & $Python @verifyArgs
    $verifyExit = $LASTEXITCODE
}
finally { Pop-Location }

Step "8/8 Summary"
if ($verifyExit -eq 0) {
    Write-Host "HARDENING TIER 2: ALL GATES PASSED" -ForegroundColor Green
    Write-Host "Rollback, if ever needed: $Python apply_hardening_tier2.py --rollback"
    exit 0
}
Write-Host "HARDENING TIER 2: GATES FAILED (see output above)." -ForegroundColor Red
Write-Host "Rollback: $Python apply_hardening_tier2.py --rollback"
exit 1

