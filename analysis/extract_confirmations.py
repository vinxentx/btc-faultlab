#!/usr/bin/env python3
"""Extract confirmation data for experiment runs.

Reads the submitted transactions from txlog.csv and queries a designated
regtest node via docker exec to determine confirmation timestamp and block
height. The resulting confirmations.csv is written alongside the run data and
consumed by analysis/metrics.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
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

    block_cache: Dict[str, Dict[str, str]] = {}
    rows = []

    with txlog_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "txid" not in reader.fieldnames or "submit_ts_utc" not in reader.fieldnames:
            return

        for entry in reader:
            txid = entry.get("txid", "").strip()
            submitted = entry.get("submit_ts_utc", "").strip()
            if not txid or not submitted:
                continue

            tx_info = run_cli(node, "getrawtransaction", txid, "true")
            if not tx_info:
                continue

            blockhash = tx_info.get("blockhash")
            if not blockhash:
                # Unconfirmed transaction.
                continue

            if blockhash not in block_cache:
                block_data = run_cli(node, "getblock", blockhash)
                if not block_data:
                    continue
                block_cache[blockhash] = {
                    "height": str(block_data.get("height", "")),
                    "time_iso": isoformat(block_data.get("time", 0)),
                }

            block_meta = block_cache[blockhash]
            rows.append(
                {
                    "txid": txid,
                    "submit_ts_utc": submitted,
                    "confirm_ts_utc": block_meta["time_iso"],
                    "confirm_block_height": block_meta["height"],
                }
            )

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
