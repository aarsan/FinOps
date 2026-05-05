# Azure FinOps Analyzer (FOCUS-driven)

A small, fast toolkit of Python scripts — driven from a single PowerShell
entry point and designed to be run inside an **AI coding agent** (GitHub
Copilot in agent mode, Claude, Cursor, etc.) — that ingests your
**FOCUS 1.0 cost export** (or a legacy Microsoft Cost Management amortized
detail CSV) and produces a single Excel dashboard plus targeted CSV reports
covering:

- **Idle / unattached managed disks** (deletion savings at *your*
  contract rate, not list price).
- **Every billed VM**, with effective vs. list rate, the benefit applied
  (Reservation / Savings Plan / Spot / AHB / negotiated MCA), Spot &
  Reserved Instance candidates, and Azure Hybrid Benefit (Windows)
  candidates.
- **Azure Hybrid Benefit (AHB) coverage and gaps** across Windows on VM,
  SQL Server on VM, Azure SQL DB, SQL Managed Instance, RHEL and SUSE.
- **SQL Server license cost** broken out from compute and storage,
  including DTU databases that need to migrate to vCore before AHB can
  apply.
- **Realized savings attribution** — how much of your spend is already
  covered by Reservations, Savings Plans, Spot, or your MCA negotiated
  rate.

The output is *intentionally* a tabular, deterministic dataset that an AI
agent can read end-to-end, reason over, and turn into a prioritized
remediation plan with you in the chat window.

---

## Why this is better than Azure Cost Management

Cost Management is a great pivot-table over your bill. It is not a
recommendation engine — and the recommendations it does surface
(Advisor) are conservative, generic, and don't see your contract. This
toolkit is built around three things Cost Management can't do:

| You want… | Azure Cost Management | This toolkit |
|---|---|---|
| **Pricing at *your* effective rate** for every "what-if" question | Shows aggregated cost; recommendations use list-price assumptions | Every line uses `EffectiveCost` / `ListCost` from your own FOCUS export, so a savings number is *yours*, not a generic estimate |
| **Cross-domain joins** ("show me all SQL spend across IaaS + PaaS, with AHB state") | Each domain is a separate blade | One pass over the export joins SQL on VM, Azure SQL DB, MI, license meters, and AHB rows into a single sheet |
| **Per-resource AHB gap detection** | Advisor flags some Windows VMs; SQL coverage is patchy; DTU vs. vCore is silent | Every Windows VM, every SQL workload classified: `Applied` / `Not applied` / `N/A (free edition)` / `Not eligible (DTU — migrate first)` with the dollars attached |
| **Per-row realized savings** | Aggregate "savings vs. PAYG" mixes RI true-up rows and skews the number | We sum `(ListCost − EffectiveCost)` *only* on rows where ListCost is populated, and bucket the rest by mechanism (Reservation / Savings Plan / Spot / negotiated). The number is auditable line-by-line |
| **RI candidates priced at *your* RI discount** | Advisor uses a model discount | We derive the customer's actual cost-weighted RI discount from VMs that already have RI coverage, then apply it to candidates. No made-up percentages |
| **An AI agent can ingest the whole thing in one shot** | UI-only; no machine-readable export of *recommendations* | Every output is a CSV/XLSX sheet with explicit columns, period labels, and the same row schema across runs — perfect grounding for an LLM |
| **Air-gapped / sensitive subs** | Requires Cost Management RBAC and portal access | Runs locally against a CSV you already exported. No outbound calls except the optional Resource Graph query for unattached disks |
| **Reproducibility** | Charts change as you click around | A single workbook (`reports/finops-dashboard.xlsx`) with a fixed period banner, headline KPIs, and embedded charts you can attach to a deck or share |

The "AI angle" is concrete: because the scripts emit clean, narrow-purpose
CSVs with your real rates and explicit columns (`AhbState`,
`PotentialSavingsPeriod`, `BenefitApplied`, etc.), an AI agent reading the
workbook can produce a prioritized action list — *"turn on AHB on these
12 SQL VMs to save \$X/month, buy a 1-year RI on this VM family for \$Y,
delete these 4 unattached premium disks for \$Z"* — grounded in your
actual numbers, not generic guidance.

---

## Repository layout

```
.
├── Run-FinOpsReport.ps1            # Single PowerShell driver. Bootstraps a venv,
│                                   # runs whichever reports you ask for.
├── scripts/
│   ├── build_dashboard.py          # Orchestrator → reports/finops-dashboard.xlsx
│   ├── list_unattached_disks.py    # Resource Graph + CSV pricing
│   ├── list_vms.py                 # VM cost / benefits / RI candidates (CSV only)
│   ├── list_ahb.py                 # AHB scan: Windows, SQL, RHEL/SUSE (CSV only)
│   ├── list_sql.py                 # Deep SQL license + AHB scan (CSV only)
│   ├── extract_focus_export.py     # Decompress manifest.json + .csv.gz parts
│   └── requirements.txt
├── data/                           # Drop your FOCUS export here (.gitignored)
└── reports/                        # All outputs land here
```

---

## Prerequisites

1. **Python 3.10+** on PATH (Windows: install from
   <https://www.python.org/downloads/>, tick *Add to PATH*).
2. **PowerShell 5.1 or 7+**.
3. **A FOCUS export** (recommended) **or** a legacy
   `Detail_BillingProfile_<id>_<yyyymm>_en.csv` from
   *Cost Management → Billing scopes → Invoices → Download usage details
   (Amortized usage)*.
4. *(Optional, only for the unattached-disks scan)* an Azure account with
   **Reader** on the subscriptions you want included. The script signs
   you in via `azure-identity` — **no Azure CLI required**.

### Getting a FOCUS export

In the Azure portal: *Cost Management → Exports → Add → FOCUS cost*.
Schedule it to a storage account, daily or monthly, then download the
folder for the period you care about. It will contain
`manifest.json` plus one or more `*.csv.gz` parts.

Drop the whole folder (or just the `manifest.json` + parts, or a single
already-extracted `.csv`) into [data/](data/) — that folder is gitignored,
nothing leaves the machine.

---

## Quick start

From the repo root:

```powershell
# Recommended: build the consolidated dashboard.
.\Run-FinOpsReport.ps1 -Dashboard
```

First run creates `.venv\` and installs `azure-identity`,
`azure-mgmt-resourcegraph`, `azure-mgmt-subscription`, and `openpyxl`.
Subsequent runs reuse the venv.

If you only have a multi-part FOCUS export and haven't extracted it yet:

```powershell
.\Run-FinOpsReport.ps1 -Extract       # produces data/<export>_<start>_<end>.csv
.\Run-FinOpsReport.ps1 -Dashboard
```

To skip the live Azure call (no unattached-disks sheet, but everything
else works from the CSV):

```powershell
.\Run-FinOpsReport.ps1 -Dashboard --skip-disks
```

---

## All driver switches

```text
-Dashboard   Build reports/finops-dashboard.xlsx (one workbook, all charts).
-Disks       Unattached disks only → reports/unattached-disks-report.{csv,xlsx}
-Vms         VM cost & RI/Spot/AHB candidates → reports/vm-report.{csv,xlsx}
-Ahb         AHB scan → reports/ahb-report.{csv,xlsx}
-Sql         SQL deep scan → reports/sql-license-report.{csv,xlsx}
-Extract     Decompress manifest + .csv.gz parts under .\data\
-All         Run Disks + Vms + Ahb + Sql in sequence (separate workbooks)
```

Anything after the switches is forwarded verbatim to the underlying
Python script. Useful overrides:

```powershell
# Use a specific CSV instead of auto-detecting the newest under .\data\
.\Run-FinOpsReport.ps1 -Dashboard --usage-csv .\data\nov.csv

# Pin the tenant for the disks Resource Graph call
.\Run-FinOpsReport.ps1 -Disks --tenant-id <tenant-guid>

# Custom output paths
.\Run-FinOpsReport.ps1 -Vms --export-csv .\reports\vm-mar.csv `
                            --export-xlsx .\reports\vm-mar.xlsx
```

---

## What the dashboard contains

`reports/finops-dashboard.xlsx`:

- **Dashboard** — period banner (e.g. *Nov 2025*), total spend at
  effective vs. list, monthly and annualized run-rate, headline savings
  opportunities, mix-of-spend by service (pie), commitment coverage (RI /
  SP / Spot / negotiated), and top-10 savings actions.
- **Recommendations** — a flat list of every actionable item (AHB toggles,
  RI candidates, SP candidates, disk deletions) sorted by dollars saved,
  with the resource id and the explicit rationale on each row.
- **Detail sheets** — one per domain: Unattached disks, VMs, AHB,
  SQL license. The columns are stable across runs so they diff cleanly.

Three time grains are labelled explicitly throughout, so the numbers are
never ambiguous:

- **Period** — exactly what's in the CSV (e.g. 30 days).
- **Monthly** — hourly rate × 730h run-rate.
- **Annualized** — period × 365 / days.

---

## Implementation notes (for the curious)

- **Built for very large CSVs.** A FOCUS export for a mid-sized estate
  can be 8+ GB / 3.5M+ rows. A naive `Import-Csv` or single-pass
  `DictReader` is unusable. Each script does a two-stage parse: a
  line-level substring pre-filter, then `csv.DictReader` only on
  surviving lines. A full dashboard build over an 8 GB export runs in
  about a minute on a laptop.
- **Schema auto-detection.** Every script calls `_detect_schema()` on
  the header row and transparently handles either FOCUS 1.0
  (`x_SkuMeterCategory`, `EffectiveCost`, `ListCost`,
  `CommitmentDiscountType`, …) or the legacy amortized export
  (`meterCategory`, `costInBillingCurrency`, `paygCostInBillingCurrency`,
  `pricingModel`, …).
- **Customer's RI discount is *derived*, not assumed.** From VMs that
  already carry RI coverage we compute a cost-weighted average of
  `1 − effective/list` and apply that to candidates. No one's RI
  discount is exactly 30%.
- **Resource Graph for disks (only).** The billing CSV alone cannot
  distinguish a disk attached to a powered-off VM from a genuinely
  unattached disk — both produce identical billing rows. So that one
  question is answered by a single KQL query
  (`type =~ 'microsoft.compute/disks' and properties.diskState == 'Unattached'`)
  spanning every readable subscription.
- **Auth chain.** `ChainedTokenCredential(DefaultAzureCredential,
  InteractiveBrowserCredential)` — env vars, managed identity, VS Code,
  cached CLI creds, then a browser fallback. Works on a laptop without
  the Azure CLI installed.
- **Per-row savings, not aggregate ratios.** Aggregate
  `(list − effective) / list` is misleading because reservation
  consumption rows record `EffectiveCost` with `ListCost = 0`. We sum
  per-row savings only on rows where `ListCost > 0` and bucket the rest
  by mechanism for separate reporting.

---

## Driving this from an AI agent

The whole project is designed to be useful inside an AI coding agent.
A typical loop:

1. *"Run the dashboard against `data/`"* → agent runs
   `.\Run-FinOpsReport.ps1 -Dashboard`.
2. *"Open `reports/finops-dashboard.xlsx`, summarise the top 10 actions
   with dollar amounts, and tell me which to do first based on
   effort vs. saving."*
3. *"For the AHB-eligible SQL VMs, draft the Azure portal steps and the
   `az` CLI command to flip the license type."*

Because the workbook is plain text under the hood (CSV sheets + XML
charts) and every column is named, the agent can ground every claim in a
specific row. That's what *Cost Management* plus *Advisor* plus a
spreadsheet plus tribal knowledge usually looks like — collapsed into one
artefact that an LLM can read.

---

## Troubleshooting

- **"No usage CSV found"** — drop the file in `data\` or pass
  `--usage-csv <path>`. For a multi-part export, run
  `.\Run-FinOpsReport.ps1 -Extract` first.
- **"Cannot write …xlsx — is it open in Excel?"** — close the workbook
  and re-run. The CSV side is already written.
- **"could not enumerate disks in any subscription"** — pass
  `--tenant-id <tenant-guid>`. The signed-in identity needs **Reader**
  on the subscriptions you want included. Or skip the Azure call with
  `--skip-disks`.
- **`pip install` fails behind a corporate proxy** — set
  `HTTPS_PROXY` before the first run; the venv bootstrap will pick it
  up.

---

## Privacy

Nothing in `data/` or `reports/` is committed (both are gitignored).
The only network call the toolkit makes is the optional Azure Resource
Graph query for unattached disks; everything else runs locally against
the CSV you already exported.
