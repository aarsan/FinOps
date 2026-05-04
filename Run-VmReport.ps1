<#
.SYNOPSIS
    One-command bootstrap + runner for the VM cost report.

.DESCRIPTION
    - Creates the local Python virtual environment in .\.venv (first run only).
    - Installs the script's Python dependencies into it.
    - Runs scripts\list_vms.py, forwarding any extra arguments.

    The Python script reads the customer's billing CSV from .\data\ and
    aggregates per-VM cost, rates, and applied benefits. No Azure access
    required; everything is in the CSV.

.EXAMPLE
    .\Run-VmReport.ps1

.EXAMPLE
    .\Run-VmReport.ps1 --usage-csv .\data\march.csv --export-csv .\reports\march-vm.csv
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$venv       = Join-Path $PSScriptRoot '.venv'
$venvPy     = Join-Path $venv 'Scripts\python.exe'
$reqs       = Join-Path $PSScriptRoot 'scripts\requirements.txt'
$mainScript = Join-Path $PSScriptRoot 'scripts\list_vms.py'

# ----- Ensure Python is available
$pythonCmd = $null
foreach ($candidate in 'py', 'python', 'python3') {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $pythonCmd = $cmd.Source; break }
}
if (-not $pythonCmd) {
    Write-Error "Python 3.10+ was not found on PATH. Install from https://www.python.org/downloads/ and try again."
    exit 1
}

# ----- Ensure venv exists
if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host "Creating Python virtual environment in $venv ..." -ForegroundColor Cyan
    if ((Split-Path -Leaf $pythonCmd) -eq 'py.exe') {
        & $pythonCmd -3 -m venv $venv
    } else {
        & $pythonCmd -m venv $venv
    }
    if (-not (Test-Path -LiteralPath $venvPy)) {
        Write-Error "Failed to create virtual environment at $venv."
        exit 1
    }
}

# ----- Ensure deps are installed (cheap import check; reinstall on miss)
$probe = & $venvPy -c "import openpyxl" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -r $reqs --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed. See output above."
        exit 1
    }
}

# ----- Run the report
& $venvPy $mainScript @ScriptArgs
exit $LASTEXITCODE
