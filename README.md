# Unattached Azure Disks Report

Lists managed disks that are currently **Unattached** in your Azure
subscriptions, prices each one using the customer-specific rates from your
detailed billing CSV (so you see what *you* actually pay, not list price),
and exports a CSV + Excel report showing the monthly savings if those disks
were deleted.

## Prerequisites

1. **Python 3.10+** (Windows: install from <https://www.python.org/downloads/>; tick "Add to PATH").
2. **Detailed billing CSV** — exported from the Azure portal:
   *Cost Management* → *Billing scopes* → your billing profile → *Invoices* →
   open the invoice → **Download usage details (CSV)** → choose
   *Amortized usage*. The file is typically named
   `Detail_BillingProfile_<id>_<yyyymm>_en.csv`.
3. An Azure account with **Reader** access to the subscriptions you want
   included in the report. The script signs you in via the Python SDK —
   no Azure CLI required.

## Setup

1. Drop your detailed billing CSV into the [data/](data/) folder.
   (It's gitignored; nothing leaves the machine.)
2. (Optional) Set environment variables to silently authenticate —
   `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` for a service
   principal, or rely on managed identity if running on Azure. If none of
   those are set, the script will pop a browser window for interactive
   sign-in on the first run.

## Run

From this folder:

```powershell
.\Run-Report.ps1
```

The first run creates a local Python virtual environment in `.venv\` and
installs dependencies, then queries Azure and writes the report. If you're
not signed in already, a browser window opens for interactive sign-in. If
your identity has access to multiple tenants, pass `--tenant-id` to pick
one:

```powershell
.\Run-Report.ps1 --tenant-id <tenant-guid>
```

Outputs in this folder:

- `unattached-disks-report.csv`
- `unattached-disks-report.xlsx`

Each row shows: disk name, resource group, subscription, location, state,
size, SKU, tier, your monthly cost, the public list price, and your savings
vs. list. The total at the top of the Excel sheet is **the monthly amount
you'd save by deleting all unattached disks**.

## Common options

```powershell
# Use a specific CSV
.\Run-Report.ps1 --usage-csv .\data\march.csv

# Include disks in any state, not just Unattached
.\Run-Report.ps1 --all-disks

# Custom output paths
.\Run-Report.ps1 --export-csv .\reports\march.csv --export-xlsx .\reports\march.xlsx

# See all options
.\Run-Report.ps1 --help
```

## How it works

- **Disk inventory** comes from a single Azure Resource Graph KQL query
  (`resources | where type =~ 'microsoft.compute/disks' | where properties.diskState == 'Unattached'`)
  spanning every subscription the signed-in identity can read. One round-trip,
  paginated server-side.
- **Sign-in** uses the Python `azure-identity` chain: environment variables,
  managed identity, VS Code / Visual Studio sign-in, and any cached CLI
  credentials — falling back to an interactive browser window. The Azure
  CLI is **not** a dependency.
- **Pricing** comes from the billing CSV. The CSV's `effectivePrice` is the
  customer's negotiated rate; `payGPrice` is the public PAYG list rate.
  The script joins each disk to its price by
  `(meterSubCategory, meterName, resourceLocation)`.

The billing CSV alone cannot tell us which disks are unattached: a disk
attached to a powered-off VM and an unattached disk produce identical billing
rows. That's why the Resource Graph query is required.

## Troubleshooting

- **"Cannot write …xlsx — is it open in Excel?"**
  Close the report in Excel and re-run. The CSV is already written.
- **"No usage CSV found"**
  Place the CSV in `data\` (or pass `--usage-csv <path>`).
- **"could not enumerate disks in any subscription"**
  Pass `--tenant-id <tenant-guid>` to sign in to the right Azure AD
  directory. The signed-in identity needs **Reader** on the subscriptions
  you want included.
