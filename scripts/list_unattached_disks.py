#!/usr/bin/env python3
"""Report unattached Azure managed disks with the customer's negotiated price.

Inventory comes from Azure Resource Graph (a single KQL query across every
subscription the signed-in account can read). Pricing comes from the customer's
detailed billing CSV (Cost Management → 'Detail_BillingProfile_*.csv'),
where `effectivePrice` is the negotiated unit rate and `payGPrice` is public
list. The two are joined by (meterSubCategory, meterName, resourceLocation).

Default output: only disks currently in the Unattached state, with monthly
cost, list price, and savings. The headline number is the total monthly
spend the customer would eliminate by deleting all unattached disks.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ----- Tier ladders: (max_provisioned_gb, tier_code) ascending -----
STANDARD_HDD = [
    (32, "S4"), (64, "S6"), (128, "S10"), (256, "S15"), (512, "S20"),
    (1024, "S30"), (2048, "S40"), (4096, "S50"), (8192, "S60"),
    (16384, "S70"), (32767, "S80"),
]
STANDARD_SSD = [
    (4, "E1"), (8, "E2"), (16, "E3"), (32, "E4"), (64, "E6"), (128, "E10"),
    (256, "E15"), (512, "E20"), (1024, "E30"), (2048, "E40"), (4096, "E50"),
    (8192, "E60"), (16384, "E70"), (32767, "E80"),
]
PREMIUM_SSD = [
    (4, "P1"), (8, "P2"), (16, "P3"), (32, "P4"), (64, "P6"), (128, "P10"),
    (256, "P15"), (512, "P20"), (1024, "P30"), (2048, "P40"), (4096, "P50"),
    (8192, "P60"), (16384, "P70"), (32767, "P80"),
]

SKU_PREFIX_TO_PRODUCT = {
    "Standard":    ("Standard HDD Managed Disks", STANDARD_HDD),
    "StandardSSD": ("Standard SSD Managed Disks", STANDARD_SSD),
    "Premium":     ("Premium SSD Managed Disks",  PREMIUM_SSD),
}


@dataclass
class TierLookup:
    tier: Optional[str]
    product: Optional[str]   # CSV meterSubCategory
    meter:   Optional[str]   # CSV meterName, e.g. "P10 LRS Disk"
    note:    Optional[str]


def derive_tier(sku_name: str, size_gb: int) -> TierLookup:
    """Map ('Premium_LRS', 4) → ('P1', 'Premium SSD Managed Disks', 'P1 LRS Disk')."""
    if not sku_name or "_" not in sku_name:
        return TierLookup(None, None, None, f"Unrecognized SKU '{sku_name}'")
    prefix, redundancy = sku_name.split("_", 1)
    info = SKU_PREFIX_TO_PRODUCT.get(prefix)
    if info is None:
        return TierLookup(None, None, None,
                          f"Unsupported SKU '{sku_name}' (Premium SSD v2 / "
                          "Ultra Disks not yet mapped)")
    product, ladder = info
    for cap, tier in ladder:
        if size_gb <= cap:
            return TierLookup(tier, product, f"{tier} {redundancy} Disk", None)
    return TierLookup(None, product, None, f"Size {size_gb} GB exceeds known tiers")


# ---------- CSV → price index ----------

@dataclass
class PriceEntry:
    effective_price: Optional[float]
    payg_price:      Optional[float]
    currency:        str


def _to_float(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def build_price_index(path: Path) -> dict[tuple[str, str, str], PriceEntry]:
    """Two-stage parse of a (potentially multi-GB) detailed billing CSV.

    1. Stream the file line-by-line and keep only lines that contain both
       'Managed Disks' and ' Disk,' (cheap substring tests reject the 99%+
       of rows that are VMs, networking, etc. without parsing fields).
    2. Run csv.DictReader over the small surviving subset to correctly
       handle quoted fields (the `tags` JSON blob, etc.).

    Returns a dict keyed by (meterSubCategory, meterName, resourceLocation)
    all lower-cased.
    """
    size_mb = path.stat().st_size / (1024 * 1024)
    print(f"Loading customer pricing from '{path}' ({size_mb:,.1f} MB)...")
    t0 = time.perf_counter()

    candidate_lines: list[str] = []
    rows_scanned = 0
    progress_every = 250_000

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        header_line = f.readline()
        if not header_line:
            raise RuntimeError("Usage CSV is empty.")
        candidate_lines.append(header_line)
        for line in f:
            rows_scanned += 1
            if "Managed Disks" in line and " Disk," in line:
                candidate_lines.append(line)
            if rows_scanned % progress_every == 0:
                elapsed = time.perf_counter() - t0
                print(f"  scanned {rows_scanned:,} rows, "
                      f"{len(candidate_lines) - 1:,} disk-candidates so far "
                      f"({elapsed:.1f}s)...")

    elapsed = time.perf_counter() - t0
    print(f"  pre-filter complete: {rows_scanned:,} rows scanned, "
          f"{len(candidate_lines) - 1:,} candidates kept in {elapsed:.1f}s.")

    reader = csv.DictReader(candidate_lines)
    required = [
        "meterCategory", "meterSubCategory", "meterName", "resourceLocation",
        "unitOfMeasure", "effectivePrice", "payGPrice", "pricingCurrency",
    ]
    missing = [c for c in required if c not in (reader.fieldnames or [])]
    if missing:
        raise RuntimeError(f"Usage CSV missing required columns: {missing}")

    uom_re = re.compile(r"^\s*1\s*/\s*Month\s*$")
    index: dict[tuple[str, str, str], PriceEntry] = {}
    matched = 0

    for row in reader:
        if row["meterCategory"] != "Storage":
            continue
        sub = row["meterSubCategory"]
        if not sub.endswith("Managed Disks"):
            continue
        meter = row["meterName"]
        if not meter.endswith(" Disk"):
            continue
        if not uom_re.match(row["unitOfMeasure"]):
            continue

        matched += 1
        key = (sub.lower(), meter.lower(), row["resourceLocation"].lower())
        existing = index.get(key)
        if existing is not None and existing.effective_price is not None:
            continue

        index[key] = PriceEntry(
            effective_price=_to_float(row["effectivePrice"]),
            payg_price=_to_float(row["payGPrice"]),
            currency=row["pricingCurrency"],
        )

    elapsed = time.perf_counter() - t0
    print(f"Indexed {len(index)} unique disk price points from {matched} matching "
          f"rows ({rows_scanned:,} total rows scanned in {elapsed:.1f}s).")
    return index


# ---------- Resource Graph → disk inventory ----------

def query_disks_via_resource_graph(
    *, all_states: bool = False, tenant_id: Optional[str] = None,
) -> list[dict]:
    """Run a KQL query through Azure Resource Graph for managed disks.

    Returns a list of dicts with keys: id, name, resourceGroup, subscriptionId,
    location, skuName, sizeGB, diskState, timeCreated. By default filters to
    disks currently in the Unattached state.
    """
    try:
        from azure.identity import (
            ChainedTokenCredential,
            DefaultAzureCredential,
            InteractiveBrowserCredential,
        )
        from azure.mgmt.resourcegraph import ResourceGraphClient
        from azure.mgmt.resourcegraph.models import (
            QueryRequest, QueryRequestOptions, ResultFormat,
        )
        from azure.mgmt.subscription import SubscriptionClient
    except ImportError as exc:
        raise RuntimeError(
            "Azure SDK packages not installed. Install with:\n"
            "  pip install -r scripts/requirements.txt"
        ) from exc

    # Try every silent credential source the SDK supports (env vars, managed
    # identity, VS Code / Visual Studio sign-in, az / azd CLI), then fall
    # back to launching a browser window for interactive sign-in. The customer
    # does NOT need the Azure CLI installed.
    silent_kwargs = {"additionally_allowed_tenants": ["*"]}
    interactive_kwargs: dict[str, object] = {}
    if tenant_id:
        silent_kwargs["tenant_id"] = tenant_id
        interactive_kwargs["tenant_id"] = tenant_id

    credential = ChainedTokenCredential(
        DefaultAzureCredential(**silent_kwargs),
        InteractiveBrowserCredential(**interactive_kwargs),
    )

    # Enumerate every subscription this credential can see.
    print("Enumerating accessible Azure subscriptions...")
    sub_client = SubscriptionClient(credential)
    sub_ids = [s.subscription_id for s in sub_client.subscriptions.list()
               if s.subscription_id]
    if not sub_ids:
        raise RuntimeError(
            "No subscriptions are visible to this account. "
            "Pass --tenant-id <tenant-id> to sign in to the right directory."
        )
    print(f"  found {len(sub_ids)} subscription(s).")

    state_filter = "" if all_states else \
        "| where properties.diskState == 'Unattached'\n    "
    kql = f"""
    resources
    | where type =~ 'microsoft.compute/disks'
    {state_filter}| project id, name, resourceGroup, subscriptionId, location,
              skuName=tostring(sku.name),
              sizeGB=toint(properties.diskSizeGB),
              diskState=tostring(properties.diskState),
              timeCreated=tostring(properties.timeCreated)
    """

    arg_client = ResourceGraphClient(credential)
    all_rows: list[dict] = []
    skip_token: Optional[str] = None
    page = 0

    label = "all" if all_states else "unattached"
    print(f"Querying Resource Graph for {label} managed disks...")
    while True:
        page += 1
        opts = QueryRequestOptions(
            top=1000,
            skip_token=skip_token,
            result_format=ResultFormat.OBJECT_ARRAY,
            allow_partial_scopes=True,
        )
        req = QueryRequest(subscriptions=sub_ids, query=kql, options=opts)
        resp = arg_client.resources(req)
        rows = list(resp.data) if resp.data else []
        all_rows.extend(rows)
        print(f"  page {page}: +{len(rows)} (running total {len(all_rows)})")
        skip_token = resp.skip_token
        if not skip_token:
            break

    print(f"Resource Graph returned {len(all_rows)} disk(s).")
    return all_rows


# ---------- CSV utilities ----------

def find_default_usage_csv(script_path: Path) -> Optional[Path]:
    """Most-recent Detail_BillingProfile_*.csv in <workspace>/data/, then root."""
    workspace = script_path.parent.parent
    for search_dir in (workspace / "data", workspace):
        if not search_dir.is_dir():
            continue
        candidates = sorted(
            search_dir.glob("Detail_BillingProfile_*.csv"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            return candidates[0]
    return None


# ---------- main ----------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--usage-csv",
        help="Detailed billing profile CSV. Defaults to the most recent "
             "Detail_BillingProfile_*.csv in <workspace>/data/, then in "
             "the workspace root.",
    )
    parser.add_argument(
        "--export-csv",
        default="unattached-disks-report.csv",
        help="Output CSV path (default: %(default)s).",
    )
    parser.add_argument(
        "--export-xlsx",
        default="unattached-disks-report.xlsx",
        help="Output Excel path (default: %(default)s). "
             "Pass an empty string to skip.",
    )
    parser.add_argument(
        "--all-disks",
        action="store_true",
        help="Include disks in any state, not just Unattached.",
    )
    parser.add_argument(
        "--tenant-id",
        help="Azure AD tenant ID to authenticate against. If omitted, the "
             "default credential's home tenant is used.",
    )
    args = parser.parse_args(argv)

    script_path = Path(__file__).resolve()

    # ----- 1. Disk inventory from Resource Graph
    try:
        disks = query_disks_via_resource_graph(
            all_states=args.all_disks, tenant_id=args.tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface SDK / auth errors plainly
        print(f"\nERROR: Resource Graph query failed: {exc}", file=sys.stderr)
        print("If sign-in failed, retry with --tenant-id <tenant-id>.",
              file=sys.stderr)
        return 1

    if not disks:
        label = "disks" if args.all_disks else "unattached disks"
        print(f"\nNo {label} found.")
        return 0

    # ----- 2. Price index from billing CSV
    if args.usage_csv:
        usage_csv = Path(args.usage_csv).resolve()
    else:
        found = find_default_usage_csv(script_path)
        if not found:
            print("\nERROR: No usage CSV provided and no Detail_BillingProfile_*.csv "
                  "found in <workspace>/data/ or the workspace root. "
                  "Pass --usage-csv.", file=sys.stderr)
            return 2
        usage_csv = found.resolve()
        print(f"\nAuto-detected usage CSV: {usage_csv}")

    if not usage_csv.exists():
        print(f"\nERROR: Usage CSV not found: {usage_csv}", file=sys.stderr)
        return 2

    price_index = build_price_index(usage_csv)

    # ----- 3. Marry inventory to pricing
    results: list[dict] = []
    no_price = 0
    for d in disks:
        sku  = d.get("skuName") or ""
        size = int(d.get("sizeGB") or 0)
        loc  = (d.get("location") or "").lower()
        tier_info = derive_tier(sku, size)

        customer_monthly = list_monthly = monthly_savings = None
        currency = ""
        if tier_info.product and tier_info.meter:
            entry = price_index.get(
                (tier_info.product.lower(), tier_info.meter.lower(), loc)
            )
            if entry and entry.effective_price is not None:
                customer_monthly = round(entry.effective_price, 2)
                if entry.payg_price is not None:
                    list_monthly = round(entry.payg_price, 2)
                    monthly_savings = round(entry.payg_price - entry.effective_price, 2)
                currency = entry.currency
            else:
                no_price += 1
        else:
            no_price += 1

        results.append({
            "Name":               d.get("name"),
            "ResourceGroup":      d.get("resourceGroup"),
            "SubscriptionId":     d.get("subscriptionId"),
            "Location":           d.get("location"),
            "DiskState":          d.get("diskState"),
            "SizeGB":             size or None,
            "Sku":                sku or None,
            "Tier":               tier_info.tier,
            "Currency":           currency or None,
            "CustomerMonthlyCost": customer_monthly,
            "ListMonthlyCost":     list_monthly,
            "MonthlySavings":      monthly_savings,
        })

    if no_price:
        print(f"\nNote: {no_price} disk(s) had no matching price in the CSV "
              "(likely Premium SSD v2 / Ultra, or a region not present in the "
              "CSV's billing period).")

    results.sort(key=lambda r: -(r["CustomerMonthlyCost"] or 0))

    total_customer = sum(r["CustomerMonthlyCost"] or 0 for r in results)
    total_list     = sum(r["ListMonthlyCost"]     or 0 for r in results)
    total_savings  = sum(r["MonthlySavings"]      or 0 for r in results)
    cur = next((r["Currency"] for r in results if r["Currency"]), "")

    label = "managed disks" if args.all_disks else "unattached disks"
    print()
    print(f"Total {label}: {len(results)}")
    print(f"Customer monthly cost (savings if all deleted)  : {total_customer:,.2f} {cur}")
    print(f"List monthly cost                               : {total_list:,.2f} {cur}")
    print(f"Additional savings vs list (negotiated discount): {total_savings:,.2f} {cur}")

    out_path = Path(args.export_csv).resolve()
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows(results)
    print(f"\nReport exported to {out_path}")

    if args.export_xlsx:
        xlsx_path = Path(args.export_xlsx).resolve()
        try:
            write_xlsx(results, xlsx_path, currency=cur,
                       total_customer=total_customer,
                       total_list=total_list,
                       total_savings=total_savings,
                       label=label)
            print(f"Excel report exported to {xlsx_path}")
        except ImportError:
            print("WARNING: openpyxl not installed; skipping Excel export.",
                  file=sys.stderr)
        except PermissionError:
            print(f"WARNING: cannot write {xlsx_path} — is it open in Excel? "
                  f"Close it or pass --export-xlsx <other-path>.",
                  file=sys.stderr)

    return 0


def write_xlsx(
    results: list[dict],
    out_path: Path,
    *,
    currency: str,
    total_customer: float,
    total_list: float,
    total_savings: float,
    label: str,
) -> None:
    """Single-sheet workbook: title, totals, then a disk table."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Disks"

    bold        = Font(bold=True)
    bold_white  = Font(bold=True, color="FFFFFF")
    big_bold    = Font(bold=True, size=14)
    accent      = Font(bold=True, color="0070C0")
    header_fill = PatternFill("solid", fgColor="305496")
    money_fmt   = '#,##0.00'

    ws["A1"] = f"Unattached Disks — {len(results)} disks ({label})"
    ws["A1"].font = big_bold
    ws.merge_cells("A1:D1")

    ws["A3"] = "Total customer monthly cost"
    ws["B3"] = round(total_customer, 2)
    ws["C3"] = currency
    ws["A4"] = "Total list monthly cost"
    ws["B4"] = round(total_list, 2)
    ws["C4"] = currency
    ws["A5"] = "Total monthly savings if all deleted"
    ws["B5"] = round(total_customer, 2)
    ws["C5"] = currency
    ws["A6"] = "Additional savings vs list (negotiated discount)"
    ws["B6"] = round(total_savings, 2)
    ws["C6"] = currency

    for r in (3, 4, 5, 6):
        ws.cell(row=r, column=1).font = bold
        ws.cell(row=r, column=2).number_format = money_fmt
    ws.cell(row=5, column=1).font = accent
    ws.cell(row=5, column=2).font = accent

    table_start = 8
    headers = list(results[0].keys())
    for c_idx, h in enumerate(headers, start=1):
        cell = ws.cell(row=table_start, column=c_idx, value=h)
        cell.font = bold_white
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    money_cols = {"CustomerMonthlyCost", "ListMonthlyCost", "MonthlySavings"}
    for r_idx, row in enumerate(results, start=table_start + 1):
        for c_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=r_idx, column=c_idx, value=row[h])
            if h in money_cols and row[h] is not None:
                cell.number_format = money_fmt

    for c_idx, h in enumerate(headers, start=1):
        letter = get_column_letter(c_idx)
        max_len = len(h)
        for row in results:
            v = row[h]
            if v is None:
                continue
            max_len = max(max_len, min(len(str(v)), 50))
        ws.column_dimensions[letter].width = max_len + 2

    ws.freeze_panes = ws.cell(row=table_start + 1, column=1)
    ws.auto_filter.ref = (
        f"A{table_start}:{get_column_letter(len(headers))}"
        f"{table_start + len(results)}"
    )

    wb.save(out_path)


if __name__ == "__main__":
    sys.exit(main())
