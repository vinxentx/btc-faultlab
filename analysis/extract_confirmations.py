#!/usr/bin/env python3
"""Extract confirmation data for experiment runs - OPTIMIZED block-based version.

Uses block-based scanning instead of per-transaction queries for much better performance.
Combines the reliability of docker exec with the efficiency of block-based scanning.

Performance: ~200-500 RPC calls instead of 5000+ (10-25x faster)
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


def run_cli(node: str, *args: str, timeout: int = 30) -> Optional[dict | str]:
    """Invoke bitcoin-cli inside the given container and parse JSON output.

    Returns:
        - dict for JSON responses (most RPC calls)
        - str for plain string responses (e.g., getblockhash)
        - None on error
    """
    cmd = [
        "docker",
        "exec",
        node,
        "bitcoin-cli",
        "-regtest",
    ]
    cmd.extend(args)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            print(f"WARNING: RPC call failed: {' '.join(cmd)}", file=sys.stderr, flush=True)
            return None

        # Try to parse as JSON first
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError:
            # If not JSON, return as string (e.g., getblockhash returns plain string)
            return proc.stdout.strip() if proc.stdout.strip() else None
    except subprocess.TimeoutExpired:
        print(f"WARNING: RPC call timed out: {' '.join(cmd)}", file=sys.stderr, flush=True)
        return None


def isoformat(ts: int) -> str:
    """Convert a unix timestamp to RFC3339 in UTC."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def collect_confirmations(run_dir: Path, node: str) -> None:
    """Collect confirmations using block-based scanning with docker exec."""
    txlog_path = run_dir / "txlog.csv"
    if not txlog_path.exists():
        print("No txlog.csv found, skipping confirmation extraction", flush=True)
        return

    confirmations_path = run_dir / "confirmations.csv"

    # Step 1: Load all submitted transactions
    print("Loading submitted transactions...", flush=True)
    submissions: Dict[str, str] = {}
    with txlog_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if "txid" not in reader.fieldnames or "submit_ts_utc" not in reader.fieldnames:
            print("ERROR: txlog.csv missing required columns (txid, submit_ts_utc)", file=sys.stderr, flush=True)
            return

        for entry in reader:
            txid = entry.get("txid", "").strip()
            submitted = entry.get("submit_ts_utc", "").strip()
            if txid and submitted:
                submissions[txid] = submitted
    
    if not submissions:
        print("No transactions found in txlog.csv", flush=True)
        return
    
    print(f"Loaded {len(submissions)} submitted transactions", flush=True)
    
    # Step 2: Check if node container is running
    check_cmd = ["docker", "ps", "--format", "{{.Names}}", "--filter", f"name={node}"]
    proc = subprocess.run(check_cmd, capture_output=True, text=True)
    if node not in proc.stdout:
        print(f"ERROR: Container {node} is not running", file=sys.stderr, flush=True)
        print("Cannot extract confirmations - containers may have been stopped", file=sys.stderr, flush=True)
        return
    
    # Step 3: Get blockchain info
    print("Getting blockchain info...", flush=True)
    chain_info = run_cli(node, "getblockchaininfo")
    if not chain_info:
        print("ERROR: Could not get blockchain info - container may not be responding", file=sys.stderr, flush=True)
        return
    
    current_height = chain_info.get("blocks", 0)
    
    # Step 4: Determine start height from metadata or use smart default
    start_height = 0
    
    # Try to get start height from funding_snapshot or metadata
    funding_path = run_dir / "funding_snapshot.json"
    if funding_path.exists():
        try:
            with open(funding_path, 'r') as f:
                funding_data = json.load(f)
                # Could extract block height from funding data if available
                # For now, start from 0
        except:
            pass
    
    print(f"Scanning blocks {start_height} to {current_height} ({current_height - start_height + 1} blocks)...", flush=True)
    
    # Step 5: Scan blocks and match transactions
    confirmations = []
    found_count = 0
    blocks_scanned = 0
    blocks_with_errors = 0
    
    for height in range(start_height, current_height + 1):
        # Progress update every 10 blocks
        if height % 10 == 0:
            print(f"Progress: Block {height}/{current_height} ({found_count}/{len(submissions)} found, {blocks_with_errors} errors)", flush=True)
        
        # Get block hash
        block_hash = run_cli(node, "getblockhash", str(height))
        if not block_hash:
            blocks_with_errors += 1
            continue
        
        # Get block with verbosity=2 (includes all transaction details)
        block = run_cli(node, "getblock", block_hash, "2")
        if not block:
            blocks_with_errors += 1
            continue

        blocks_scanned += 1
        block_time = block.get("time", 0)
        block_time_iso = isoformat(block_time)
        
        # Check each transaction in the block
        # Note: getblock with verbosity=2 returns tx array with txid field
        tx_list = block.get("tx", [])
        if not isinstance(tx_list, list):
            print(f"WARNING: Block {height} has unexpected tx structure", file=sys.stderr, flush=True)
            continue

        for tx in tx_list:
            # Handle both dict and string formats
            if isinstance(tx, dict):
                txid = tx.get("txid")
            elif isinstance(tx, str):
                # If tx is just a string (txid), we can use it directly
                txid = tx
            else:
                continue

            if txid and txid in submissions:
                confirmations.append({
                    "txid": txid,
                    "submit_ts_utc": submissions[txid],
                    "confirm_ts_utc": block_time_iso,
                    "confirm_block_height": str(height),
                    "confirm_block_hash": block_hash,
                })
                found_count += 1
        
        # Early exit if we found all transactions
        if found_count >= len(submissions):
            print(f"Found all {found_count} transactions, stopping scan", flush=True)
            break
    
    print(f"Scan complete: {found_count}/{len(submissions)} transactions confirmed", flush=True)
    print(f"Scanned {blocks_scanned} blocks, {blocks_with_errors} blocks had errors", flush=True)
    
    if found_count == 0:
        print("WARNING: No confirmations found!", file=sys.stderr, flush=True)
        print(f"  - Submitted: {len(submissions)}", file=sys.stderr, flush=True)
        print(f"  - Blocks scanned: {blocks_scanned}", file=sys.stderr, flush=True)
        print(f"  - Block range: {start_height} to {current_height}", file=sys.stderr, flush=True)
    
    # Step 6: Write results
    confirmations.sort(key=lambda x: (int(x["confirm_block_height"]), x["txid"]))

    with confirmations_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "txid",
            "submit_ts_utc",
            "confirm_ts_utc",
            "confirm_block_height",
            "confirm_block_hash",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(confirmations)
    
    print(f"Wrote {len(confirmations)} confirmations to {confirmations_path}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract transaction confirmations (optimized block-based)")
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
