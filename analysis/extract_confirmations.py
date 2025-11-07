#!/usr/bin/env python3
"""Extract confirmation data for experiment runs.

Reads the submitted transactions from txlog.csv and queries a designated
regtest node via docker exec to determine confirmation timestamp and block
height. The resulting confirmations.csv is written alongside the run data and
consumed by analysis/metrics.py.

Optimized version: Scans blocks instead of querying each transaction individually,
resulting in ~15x speedup for large runs.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional


def run_cli(node: str, *args: str) -> Optional[dict]:
    """Invoke bitcoin-cli inside the given container and parse JSON output."""

    cmd = [
        "docker",
        "exec",
        node,
        "bitcoin-cli",
        "-regtest",
    ]
    cmd.extend(args)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return None

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None


def isoformat(ts: int) -> str:
    """Convert a unix timestamp to RFC3339 in UTC."""

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def collect_confirmations(run_dir: Path, node: str) -> None:
    txlog_path = run_dir / "txlog.csv"
    if not txlog_path.exists():
        # Nothing to do if the generator never produced transactions.
        return

    confirmations_path = run_dir / "confirmations.csv"

    # Step 1: Load all submitted transactions into a dictionary for fast lookup
    print("Loading transaction submissions...", file=sys.stderr)
    submissions: Dict[str, str] = {}
    with txlog_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "txid" not in reader.fieldnames or "submit_ts_utc" not in reader.fieldnames:
            return

        for entry in reader:
            txid = entry.get("txid", "").strip()
            submitted = entry.get("submit_ts_utc", "").strip()
            if not txid or not submitted:
                continue
            submissions[txid] = submitted

    total_submissions = len(submissions)
    print(f"Loaded {total_submissions} transaction submissions", file=sys.stderr)

    if not submissions:
        # Create empty file
        with confirmations_path.open("w", encoding="utf-8", newline="") as handle:
            fieldnames = [
                "txid",
                "submit_ts_utc",
                "confirm_ts_utc",
                "confirm_block_height",
            ]
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
        return

    # Step 2: Get current block height
    print("Getting current block height...", file=sys.stderr)
    current_height = run_cli(node, "getblockcount")
    if current_height is None:
        print("ERROR: Failed to get block count", file=sys.stderr)
        return

    print(f"Current block height: {current_height}", file=sys.stderr)

    # Step 3: Scan all blocks and match transactions
    print(f"Scanning blocks 0-{current_height} for confirmations...", file=sys.stderr)
    rows = []
    found_count = 0

    for height in range(0, current_height + 1):
        # Progress indicator every 50 blocks
        if height % 50 == 0 or height == current_height:
            progress_pct = (height / (current_height + 1)) * 100 if current_height > 0 else 0
            print(
                f"Progress: {height}/{current_height} blocks ({progress_pct:.1f}%) - "
                f"Found {found_count}/{total_submissions} confirmations",
                file=sys.stderr
            )

        # Get block hash
        block_hash = run_cli(node, "getblockhash", str(height))
        if not block_hash:
            continue

        # Get block with verbosity 2 to include all transaction IDs
        block_data = run_cli(node, "getblock", block_hash, "2")
        if not block_data:
            continue

        block_time = block_data.get("time", 0)
        block_time_iso = isoformat(block_time)

        # Check each transaction in the block
        transactions = block_data.get("tx", [])
        for tx in transactions:
            txid = tx.get("txid")
            if not txid:
                continue

            # If this transaction is in our submissions, record the confirmation
            if txid in submissions:
                rows.append(
                    {
                        "txid": txid,
                        "submit_ts_utc": submissions[txid],
                        "confirm_ts_utc": block_time_iso,
                        "confirm_block_height": str(height),
                    }
                )
                found_count += 1

    print(
        f"\nCompleted: Found {found_count}/{total_submissions} confirmations "
        f"({found_count/total_submissions*100:.1f}%)",
        file=sys.stderr
    )

    # Step 4: Write results (sorted by block height for consistency)
    rows.sort(key=lambda r: (int(r["confirm_block_height"]), r["txid"]))

    with confirmations_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "txid",
            "submit_ts_utc",
            "confirm_ts_utc",
            "confirm_block_height",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract transaction confirmations")
    parser.add_argument("--run-dir", required=True, help="Path to the experiment run directory")
    parser.add_argument(
        "--node",
        default="node01",
        help="Container name of the node used for RPC queries (default: node01)",
    )
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.exists():
        raise SystemExit(f"Run directory does not exist: {run_dir}")

    collect_confirmations(run_dir, args.node)


if __name__ == "__main__":
    main()
