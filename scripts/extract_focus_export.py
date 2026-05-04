#!/usr/bin/env python3
"""Extract a Cost Management export (manifest.json + .csv.gz parts) to one CSV.

Usage:
    python scripts/extract_focus_export.py
    python scripts/extract_focus_export.py --data-dir data --output data/combined.csv

Reads the manifest, decompresses each part listed there, and concatenates
them into a single CSV (header deduplicated). Verifies that every part
declared in the manifest is present locally and that the total row count
matches the manifest's `dataRowCount`. Missing parts are reported but do
not stop extraction of what's available.
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path
from typing import Optional


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default="data",
        help="Folder containing manifest.json and .csv.gz parts (default: %(default)s).",
    )
    parser.add_argument(
        "--manifest",
        help="Path to manifest.json (defaults to <data-dir>/manifest.json).",
    )
    parser.add_argument(
        "--output",
        help="Output CSV path. Defaults to <data-dir>/<exportName>_<startDate>_<endDate>.csv.",
    )
    parser.add_argument(
        "--per-part",
        action="store_true",
        help="Also write each .csv.gz to a sibling .csv next to it.",
    )
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.is_dir():
        print(f"ERROR: data folder not found: {data_dir}", file=sys.stderr)
        return 2

    manifest_path = Path(args.manifest).resolve() if args.manifest \
        else data_dir / "manifest.json"
    if not manifest_path.is_file():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 2

    with manifest_path.open("r", encoding="utf-8-sig") as f:
        manifest = json.load(f)

    blobs = manifest.get("blobs") or []
    if not blobs:
        print("ERROR: manifest has no 'blobs' array.", file=sys.stderr)
        return 2

    export_cfg = manifest.get("exportConfig") or {}
    run_info   = manifest.get("runInfo")     or {}
    export_name = export_cfg.get("exportName") or "export"
    export_type = export_cfg.get("type")      or "Unknown"
    start_date  = (run_info.get("startDate") or "").split("T")[0] or "start"
    end_date    = (run_info.get("endDate")   or "").split("T")[0] or "end"
    expected_rows = manifest.get("dataRowCount")

    print(f"Manifest: {manifest_path}")
    print(f"  export   : {export_name} ({export_type})")
    print(f"  range    : {start_date} -> {end_date}")
    print(f"  parts    : {len(blobs)} declared, "
          f"{manifest.get('dataRowCount', '?')} rows total, "
          f"{manifest.get('byteCount', '?')} bytes compressed.")

    # Resolve each blob to a local file by basename.
    plan: list[tuple[dict, Optional[Path]]] = []
    missing = 0
    for blob in blobs:
        blob_name = blob.get("blobName") or ""
        leaf = Path(blob_name).name  # e.g. part_3_0001.csv.gz
        local = data_dir / leaf
        if local.is_file():
            plan.append((blob, local))
        else:
            plan.append((blob, None))
            missing += 1

    if missing:
        print(f"\nWARNING: {missing} declared part(s) are missing locally:")
        for blob, local in plan:
            if local is None:
                print(f"  - {Path(blob['blobName']).name} "
                      f"(expected {blob.get('dataRowCount', '?')} rows)")
        print()

    # Default output name
    if args.output:
        out_path = Path(args.output).resolve()
    else:
        safe_name = export_name.replace("/", "_").replace("\\", "_")
        out_path = data_dir / f"{safe_name}_{start_date}_{end_date}.csv"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Writing combined CSV: {out_path}")
    t0 = time.perf_counter()
    total_rows = 0
    total_bytes_out = 0
    canonical_header: Optional[bytes] = None

    with out_path.open("wb") as out_f:
        for idx, (blob, local) in enumerate(plan, start=1):
            if local is None:
                continue

            blob_name = Path(blob["blobName"]).name
            expected_blob_rows = blob.get("dataRowCount")

            with gzip.open(local, "rb") as gz:
                header = gz.readline()
                if not header:
                    print(f"  WARN: {blob_name} is empty; skipping.")
                    continue
                if canonical_header is None:
                    canonical_header = header
                    out_f.write(header)
                    total_bytes_out += len(header)
                elif header != canonical_header:
                    print(f"  WARN: {blob_name} header differs from first part. "
                          "Schema may have changed mid-export. Skipping its header "
                          "but appending data anyway.")

                rows_in_part = 0
                for line in gz:
                    out_f.write(line)
                    total_bytes_out += len(line)
                    rows_in_part += 1

                # If --per-part, emit a sibling .csv next to the .gz too.
                if args.per_part:
                    sibling = local.with_suffix("")  # strips .gz; leaves .csv
                    if sibling.suffix.lower() != ".csv":
                        sibling = sibling.with_suffix(".csv")
                    print(f"    also writing {sibling.name}")
                    with gzip.open(local, "rb") as gz2, sibling.open("wb") as sf:
                        for chunk in iter(lambda: gz2.read(1 << 20), b""):
                            sf.write(chunk)

            total_rows += rows_in_part
            elapsed = time.perf_counter() - t0
            note = ""
            if expected_blob_rows is not None and rows_in_part != expected_blob_rows:
                note = (f"  ROW COUNT MISMATCH (manifest says "
                        f"{expected_blob_rows})")
            print(f"  [{idx}/{len(plan)}] {blob_name}: "
                  f"{rows_in_part:,} rows ({elapsed:.1f}s){note}")

    elapsed = time.perf_counter() - t0
    print(f"\nDone in {elapsed:.1f}s.")
    print(f"  Combined CSV : {out_path} "
          f"({total_bytes_out / (1024 * 1024):,.1f} MB)")
    print(f"  Total rows   : {total_rows:,}")
    if expected_rows is not None:
        if missing or total_rows != expected_rows:
            delta = expected_rows - total_rows
            print(f"  Manifest expected {expected_rows:,} rows "
                  f"(diff: {delta:+,}). {missing} part(s) missing locally.")
        else:
            print("  Row count matches manifest. ✔")

    return 0


if __name__ == "__main__":
    sys.exit(main())
