<#
.SYNOPSIS
    One driver to run any (or all) of the FinOps reports.

.DESCRIPTION
    Bootstraps a local Python virtual environment and runs the requested
    report(s) against the customer's billing CSV under .\data\.

    Reports
    -------
    -Dashboard  Single Excel workbook with a dashboard, charts, and every
                detail sheet. RECOMMENDED — most useful single output.
    -Disks      Unattached disks scan (Resource Graph + CSV pricing).
    -Vms        VM cost / benefit / RI-candidate report (CSV only).
    -Ahb        Azure Hybrid Benefit scan: who has it, who could (CSV only).
    -Sql        SQL Server license + AHB scan across SQL on VM, Azure SQL
                Database, and SQL Managed Instance (CSV only).
    -Extract    Decompress + concatenate a Cost Management export
                (manifest.json + .csv.gz parts) into a single CSV under .\data\.
    -All        Run Disks + Vms + Ahb + Sql in sequence (separate workbooks).

    Switches stack: '.\Run-FinOpsReport.ps1 -Vms -Ahb' runs both. Anything
    after the switches is forwarded verbatim to the underlying Python
    script(s).

.EXAMPLE
    # Recommended: build the consolidated dashboard
    .\Run-FinOpsReport.ps1 -Dashboard

.EXAMPLE
    # Build the dashboard against a specific CSV, skip the Azure call
    .\Run-FinOpsReport.ps1 -Dashboard --usage-csv .\data\march.csv --skip-disks

.EXAMPLE
    # Per-domain workbooks (one per script)
    .\Run-FinOpsReport.ps1 -All

.EXAMPLE
    # Re-extract a Cost Management export
    .\Run-FinOpsReport.ps1 -Extract
#>
[CmdletBinding(DefaultParameterSetName = 'Reports')]
param(
    [Parameter(ParameterSetName = 'Reports')]
    [switch]$Dashboard,

    [Parameter(ParameterSetName = 'Reports')]
    [switch]$Disks,

    [Parameter(ParameterSetName = 'Reports')]
    [switch]$Vms,

    [Parameter(ParameterSetName = 'Reports')]
    [switch]$Ahb,

    [Parameter(ParameterSetName = 'Reports')]
    [switch]$Sql,

    [Parameter(ParameterSetName = 'Reports')]
    [switch]$Extract,

    [Parameter(ParameterSetName = 'Reports')]
    [switch]$All,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# ----- Resolve which scripts to run
$scripts = New-Object System.Collections.Generic.List[string]
if ($All) {
    $scripts.Add('list_unattached_disks.py')
    $scripts.Add('list_vms.py')
    $scripts.Add('list_ahb.py')
    $scripts.Add('list_sql.py')
} else {
    if ($Extract)   { $scripts.Add('extract_focus_export.py') }
    if ($Dashboard) { $scripts.Add('build_dashboard.py') }
    if ($Disks)     { $scripts.Add('list_unattached_disks.py') }
    if ($Vms)       { $scripts.Add('list_vms.py') }
    if ($Ahb)       { $scripts.Add('list_ahb.py') }
    if ($Sql)       { $scripts.Add('list_sql.py') }
}

if ($scripts.Count -eq 0) {
    Write-Host "No report selected. Use one or more of: -Dashboard, -Disks, -Vms, -Ahb, -Sql, -Extract, -All" -ForegroundColor Yellow
    Write-Host "Recommended: .\Run-FinOpsReport.ps1 -Dashboard"
    Get-Help $PSCommandPath -Examples
    exit 1
}

# ----- Bootstrap venv + deps
$venv       = Join-Path $PSScriptRoot '.venv'
$venvPy     = Join-Path $venv 'Scripts\python.exe'
$reqs       = Join-Path $PSScriptRoot 'scripts\requirements.txt'

$pythonCmd = $null
foreach ($candidate in 'py', 'python', 'python3') {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($cmd) { $pythonCmd = $cmd.Source; break }
}
if (-not $pythonCmd) {
    Write-Error "Python 3.10+ was not found on PATH. Install from https://www.python.org/downloads/"
    exit 1
}

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

# Probe the deps each script needs. The disk script needs azure-* packages;
# the VM and AHB scripts only need openpyxl. We probe the union so a single
# install covers all three.
& $venvPy -c "import openpyxl, azure.identity, azure.mgmt.resourcegraph, azure.mgmt.subscription" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Python dependencies..." -ForegroundColor Cyan
    & $venvPy -m pip install --upgrade pip --quiet
    & $venvPy -m pip install -r $reqs --quiet
    if ($LASTEXITCODE -ne 0) {
        Write-Error "pip install failed. See output above."
        exit 1
    }
}

# ----- Run the requested scripts in order
$exitCode = 0
foreach ($script in $scripts) {
    $scriptPath = Join-Path $PSScriptRoot "scripts\$script"
    Write-Host ""
    Write-Host ("=" * 70) -ForegroundColor DarkCyan
    Write-Host "  Running: $script" -ForegroundColor Cyan
    Write-Host ("=" * 70) -ForegroundColor DarkCyan

    & $venvPy $scriptPath @ScriptArgs
    $rc = $LASTEXITCODE
    if ($rc -ne 0) {
        Write-Host "  $script exited with code $rc" -ForegroundColor Yellow
        $exitCode = $rc
        # Continue to next script — partial failure shouldn't block others.
    }
}

exit $exitCode
