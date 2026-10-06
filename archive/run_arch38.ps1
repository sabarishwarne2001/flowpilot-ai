<#
.SYNOPSIS
    ARCH-38 â€” Batch Ingestion & Universal Document Intelligence.
    Check, apply, prove idempotent, migrate, install, verify.

.DESCRIPTION
    Run from the repository root:

        .\run_arch38.ps1                 # full sequence
        .\run_arch38.ps1 -CheckOnly      # report what would change, write nothing
        .\run_arch38.ps1 -SkipBuild      # skip tsc / ESLint / vite build
        .\run_arch38.ps1 -Rollback       # downgrade the migration and restore the tree

    The apply step is idempotent: it is run twice on purpose, and the second
    run must report "0 file(s) would change". If it does not, the script stops
    before touching the database, because a non-idempotent apply means the tree
    is not in the state the migration expects.

.NOTES
    Baseline commit : ARCH 37 DONE
    Alembic head    : arch37_step1_flow_builder -> arch38_step1_batches
#>

[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipBuild,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = 'postgresql://postgres:postgres@localhost:5432/flowpilot'
}
Set-StrictMode -Version Latest

$Root     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'

function Write-Step {
    param([string]$Text)
    Write-Host ''
    Write-Host "=== $Text ===" -ForegroundColor Cyan
}

function Invoke-Checked {
    param(
        [string]$Label,
        [scriptblock]$Action
    )
    Write-Host "  -> $Label" -ForegroundColor DarkGray
    & $Action
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAILED: $Label (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if (-not (Test-Path $Backend)) {
    Write-Host "backend/ not found. Run this from the repository root." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $Backend 'apply_arch38.py'))) {
    Write-Host "backend/apply_arch38.py not found." -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

if ($Rollback) {
    Write-Step 'ARCH-38 rollback'
    Push-Location $Backend
    try {
        Write-Host '  Downgrading to arch37_step1_flow_builder...' -ForegroundColor Yellow
        alembic downgrade arch37_step1_flow_builder
        if ($LASTEXITCODE -ne 0) {
            Write-Host '  Downgrade failed. The tree has NOT been touched.' -ForegroundColor Red
            exit $LASTEXITCODE
        }
    }
    finally { Pop-Location }

    Write-Host ''
    Write-Host '  The database is back at arch37_step1_flow_builder.' -ForegroundColor Green
    Write-Host '  To restore the working tree, review and then run:' -ForegroundColor Yellow
    Write-Host '      git checkout -- backend frontend'
    Write-Host '      git clean -n backend frontend    # review the list first'
    Write-Host '      git clean -f backend frontend'
    Write-Host ''
    Write-Host '  Deliberately not automated: git clean -f removes untracked files,' -ForegroundColor Yellow
    Write-Host '  and this script cannot tell yours from ARCH-38''s.' -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------------------
# 1. Check
# ---------------------------------------------------------------------------

Write-Step '1/6  Checking what ARCH-38 would change'
Push-Location $Backend
try {
    python apply_arch38.py --check
    if ($LASTEXITCODE -ne 0) {
        Write-Host ''
        Write-Host '  The check refused. Nothing has been written.' -ForegroundColor Red
        Write-Host '  A sha mismatch means a file has local changes this script' -ForegroundColor Yellow
        Write-Host '  would discard. Review them, then re-run.' -ForegroundColor Yellow
        exit $LASTEXITCODE
    }
}
finally { Pop-Location }

if ($CheckOnly) {
    Write-Host ''
    Write-Host '  -CheckOnly: stopping here. Nothing was written.' -ForegroundColor Green
    exit 0
}

# ---------------------------------------------------------------------------
# 2. Apply
# ---------------------------------------------------------------------------

Write-Step '2/6  Applying ARCH-38'
Push-Location $Backend
try {
    Invoke-Checked 'apply_arch38.py' { python apply_arch38.py }
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
# 3. Idempotency
# ---------------------------------------------------------------------------

Write-Step '3/6  Proving the apply is idempotent'
Push-Location $Backend
try {
    $second = python apply_arch38.py --check 2>&1 | Out-String
    Write-Host $second
    if ($second -notmatch '0 file\(s\) would change') {
        Write-Host '  A second apply still wants to change files.' -ForegroundColor Red
        Write-Host '  Stopping before the migration: the tree is not in the state' -ForegroundColor Yellow
        Write-Host '  arch38_step1_batches expects.' -ForegroundColor Yellow
        exit 1
    }
    Write-Host '  Idempotent.' -ForegroundColor Green
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
# 4. Migrate
# ---------------------------------------------------------------------------

Write-Step '4/6  Running migrations'
Push-Location $Backend
try {
    Invoke-Checked 'alembic upgrade head' { alembic upgrade head }
    $head = (alembic current 2>&1 | Out-String)
    if ($head -notmatch 'arch38_step1_batches') {
        Write-Host "  Alembic head is not arch38_step1_batches:" -ForegroundColor Red
        Write-Host $head
        exit 1
    }
    Write-Host '  Head is arch38_step1_batches.' -ForegroundColor Green
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
# 5. Frontend dependencies
# ---------------------------------------------------------------------------

Write-Step '5/6  Installing frontend dependencies'
Push-Location $Frontend
try {
    Invoke-Checked 'npm install' { npm install }
}
finally { Pop-Location }

# ---------------------------------------------------------------------------
# 6. Verify
# ---------------------------------------------------------------------------

Write-Step '6/6  Verifying'
Push-Location $Backend
try {
    if ($SkipBuild) {
        Invoke-Checked 'verify_arch38.py --db --mutate' {
            python verify_arch38.py --db --mutate
        }
    }
    else {
        Invoke-Checked 'verify_arch38.py --db --mutate --build' {
            python verify_arch38.py --db --mutate --build
        }
    }
}
finally { Pop-Location }

Write-Host ''
Write-Host '========================================================' -ForegroundColor Green
Write-Host ' ARCH-38 applied, migrated and verified.' -ForegroundColor Green
Write-Host '========================================================' -ForegroundColor Green
Write-Host ''
Write-Host ' Restart the API and the workers so the new job types are' -ForegroundColor Yellow
Write-Host ' registered: batch.expand_archive, work_items.bulk and' -ForegroundColor Yellow
Write-Host ' ingestion.sweep_sessions.' -ForegroundColor Yellow

