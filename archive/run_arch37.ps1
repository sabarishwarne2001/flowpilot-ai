<#
.SYNOPSIS
    ARCH-37 — check, apply, migrate, build and verify, in order. Stops at the
    first failure.

.DESCRIPTION
    Enterprise Flow Builder & Commercial Action Catalog (Tranches 1 + 2).

    Place apply_arch37.py and verify_arch37.py in backend\ and run this from
    the repository root:

        .\run_arch37.ps1                 # full sequence
        .\run_arch37.ps1 -CheckOnly      # report what would change, write nothing
        .\run_arch37.ps1 -SkipBuild      # skip npm install / tsc / eslint / vite
        .\run_arch37.ps1 -SkipMutate     # skip the mutation run (faster)
        .\run_arch37.ps1 -Rollback       # downgrade both migrations and restore files from git

    Uses backend\.venv (created by start_dev.ps1) and the Docker Compose `db`
    service, which must be running (.\start_dev.ps1 starts it). The ARCH-31
    database gates in the regression chain need the development seed (one
    organization, one workspace, two documents), as they did for ARCH-39.
#>

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipBuild,
    [switch]$SkipMutate,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PYTHONUNBUFFERED = '1'
$env:PYTHONIOENCODING = 'utf-8'

$RepoRoot    = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BackendDir  = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'
$Python      = Join-Path $BackendDir '.venv\Scripts\python.exe'
$HeadBefore  = 'arch39_step1_conversations'
$HeadAfter   = 'arch37_step1_flow_builder'

function Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }

function Invoke-Checked {
    param([string]$Label, [scriptblock]$Command)
    & $Command
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    [FAILED] $Label (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
    Write-Host "    [ok] $Label" -ForegroundColor Green
}

if (-not (Test-Path $Python)) {
    Write-Host "backend\.venv not found. Run .\start_dev.ps1 once to create it." -ForegroundColor Red
    exit 2
}
foreach ($script in 'apply_arch37.py', 'verify_arch37.py') {
    if (-not (Test-Path (Join-Path $BackendDir $script))) {
        Write-Host "backend\$script is missing. Copy it into backend\ first." -ForegroundColor Red
        exit 2
    }
}

Push-Location $BackendDir
try {
    if ($Rollback) {
        Step 'Rolling back ARCH-37'
        # Rules the migration paused stay paused on downgrade, by design.
        Invoke-Checked "alembic downgrade $HeadBefore" { & $Python -m alembic downgrade $HeadBefore }
        Push-Location $RepoRoot
        try {
            # Restores modified files AND the four retired frontend files.
            Invoke-Checked 'git checkout -- backend frontend' { git checkout -- backend frontend }
            Write-Host '    New ARCH-37 files are still on disk. Review with:  git clean -n backend frontend' -ForegroundColor Yellow
        } finally { Pop-Location }
        exit 0
    }

    Step '1/7  What ARCH-37 would change'
    Invoke-Checked 'apply_arch37.py --check' { & $Python apply_arch37.py --check }
    if ($CheckOnly) { exit 0 }

    Step '2/7  Apply'
    Invoke-Checked 'apply_arch37.py' { & $Python apply_arch37.py }

    Step '3/7  Idempotency (ARCH-37 and ARCH-39)'
    foreach ($script in 'apply_arch37.py', 'apply_arch39.py') {
        $again = & $Python $script --check
        $again | Select-Object -Last 1 | Write-Host
        if (-not ($again -match '0 file\(s\) would change')) {
            Write-Host "    [FAILED] a second $script would still change files" -ForegroundColor Red
            exit 1
        }
    }
    Write-Host '    [ok] a second run changes nothing' -ForegroundColor Green

    Step "4/7  Migrate (-> $HeadAfter)"
    Invoke-Checked 'alembic upgrade head' { & $Python -m alembic upgrade head }
    $current = (& $Python -m alembic current 2>$null | Out-String)
    Write-Host $current.Trim()
    if ($current -notmatch $HeadAfter) {
        Write-Host "    [FAILED] alembic head is not $HeadAfter" -ForegroundColor Red
        exit 1
    }

    $verifyArgs = @('verify_arch37.py', '--db')
    if (-not $SkipMutate) { $verifyArgs += '--mutate' }
    if (-not $SkipBuild) {
        Step '5/7  Frontend dependencies'
        Push-Location $FrontendDir
        try {
            Invoke-Checked 'npm install' { npm install --no-audit --no-fund }
        } finally { Pop-Location }
        $verifyArgs += '--build'
    } else {
        Step '5/7  Frontend dependencies (skipped)'
    }

    Step '6/7  Paused rules (review these in the flow builder)'
    & $Python -c @"
import sys
sys.path.insert(0, '.')
from sqlalchemy import text
from app.db.session import engine
with engine.connect() as c:
    rows = c.execute(text("SELECT details->>'rule_name', details->>'legacy_event' FROM audit_logs WHERE details->>'reason' = 'arch37_dead_trigger_paused' ORDER BY created_at")).all()
print(f'    {len(rows)} rule(s) were paused because their trigger never fired before ARCH-37')
for name, event in rows[:20]:
    print(f'      - {name}  ({event})')
"@

    Step '7/7  Verify (offline, build, database, mutation, regression chain)'
    Invoke-Checked 'verify_arch37.py' { & $Python @verifyArgs }

    Write-Host "`nARCH-37 applied and verified." -ForegroundColor Green
    Write-Host 'Restart the API and worker (.\start_dev.ps1) to load the new code.'
}
finally {
    Pop-Location
}
