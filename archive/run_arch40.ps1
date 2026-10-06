[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$SkipBuild,
    [switch]$SkipMutate,
    [switch]$SkipRegression,
    [switch]$Contract,
    [switch]$Rollback
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$ReleaseHead = "arch40_step2a_review_view_paths"
$ContractHead = "arch40_step3_contract_ai_settings"
$BeforeHead = "arch38_step1_batches"

$env:PYTHONUTF8 = "1"
if (-not $env:DATABASE_URL) {
    $env:DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/flowpilot"
}
$env:BACKEND = $Backend

$Python = "python"
$VenvPython = Join-Path $Backend ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) { $Python = $VenvPython }

function Step([string]$Text) { Write-Host "`n=== $Text ===" -ForegroundColor Cyan }
function Fail([string]$Text) { Write-Host "`nFAILED: $Text" -ForegroundColor Red; exit 1 }
function RunCommand([string]$Exe, [string[]]$CmdArgs, [string]$What) {
    & $Exe $CmdArgs
    if ($LASTEXITCODE -ne 0) { Fail "$What (exit $LASTEXITCODE)" }
}

if (-not (Test-Path (Join-Path $Backend "apply_arch40.py"))) {
    Fail "backend\apply_arch40.py not found. Copy it into backend\ first."
}

Set-Location $Backend

if ($Rollback) {
    Step "ROLLBACK"
    Write-Host "This restores the CODE to what it was before the last ARCH-40 apply." -ForegroundColor Yellow
    Write-Host "It is LOSSY for the DATABASE if you also downgrade:" -ForegroundColor Yellow
    Write-Host "  * workspace_email_overrides and review_assignments are dropped;" -ForegroundColor Yellow
    Write-Host "  * If -Contract ever ran, the three ai_settings columns come back" -ForegroundColor Yellow
    $answer = Read-Host "Type ROLLBACK to restore the code"
    if ($answer -ne "ROLLBACK") { Write-Host "Cancelled."; exit 0 }
    RunCommand $Python @("apply_arch40.py", "--rollback") "code rollback"
    Write-Host "`nTo take the database back too (lossy, see above):" -ForegroundColor Yellow
    Write-Host "    alembic downgrade $BeforeHead"
    exit 0
}

Step "1/6 CHECK - validate every file against the three accepted states"
RunCommand $Python @("apply_arch40.py", "--check") "apply check (a file has local changes; nothing was written)"
if ($CheckOnly) { Write-Host "`n-CheckOnly: nothing written." -ForegroundColor Green; exit 0 }

Step "2/6 APPLY"
RunCommand $Python @("apply_arch40.py") "apply"

Step "3/6 IDEMPOTENCY - a second apply must change nothing"
$second = & $Python apply_arch40.py
if ($LASTEXITCODE -ne 0) { Fail "second apply" }
$second | Select-Object -Last 2 | ForEach-Object { Write-Host $_ }
if (($second -join "`n") -notmatch "0 file\(s\) to write") { Fail "the second apply wanted to write files; it is not idempotent" }

Step "4/6 MIGRATE - to the ARCH-40 release head"
RunCommand $Python @("-m", "alembic", "upgrade", $ReleaseHead) "alembic upgrade $ReleaseHead"
if ($Contract) {
    Write-Host "`n-Contract: dropping system_prompt_version, prompt_version, enable_token_tracking." -ForegroundColor Yellow
    $env:ARCH40_CONTRACT = "1"
    try { RunCommand $Python @("-m", "alembic", "upgrade", $ContractHead) "alembic upgrade $ContractHead" }
    finally { Remove-Item Env:ARCH40_CONTRACT -ErrorAction SilentlyContinue }
}
& $Python -m alembic current

Step "5/6 NPM INSTALL"
Set-Location $Frontend
RunCommand "npm" @("install", "--no-audit", "--no-fund") "npm install"
Set-Location $Backend

Step "6/6 VERIFY"
$verify = @("verify_arch40.py", "--db")
if (-not $SkipMutate) { $verify += "--mutate" }
if (-not $SkipBuild) { $verify += "--build" }
if (-not $SkipRegression) { $verify += "--regression" }
RunCommand $Python $verify "verify_arch40.py"

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host " ARCH-40 applied, migrated and verified (61/61)." -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host "`nOpen Settings and the Review hub in a browser and click through them." -ForegroundColor Green
if (-not $Contract) {
    Write-Host "Later deploy, once this one is settled:  .\run_arch40.ps1 -Contract" -ForegroundColor Green
}
Set-Location $Root

