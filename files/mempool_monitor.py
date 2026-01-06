#!/usr/bin/env python3
"""Monitor mempool size across all nodes during experiment.

Improvements:
- Parallel queries for fast collection across many nodes
- Cycle-based sampling with consistent timestamps
- Shorter default interval to catch block events
- Graceful shutdown on SIGTERM to prevent data corruption
"""
import argparse
import csv
import json
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# Global flag for graceful shutdown
shutdown_requested = False


def signal_handler(signum, frame):
    """Handle SIGTERM gracefully."""
    global shutdown_requested
    shutdown_requested = True


def get_mempool_info(node: str) -> tuple[str, dict | None]:
    """Get mempool info from a node. Returns (node, info) tuple."""
    try:
        cmd = ["docker", "exec", node, "bitcoin-cli", "-regtest", "getmempoolinfo"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        if proc.returncode == 0:
            return (node, json.loads(proc.stdout))
    except Exception:
        pass
    return (node, None)


def main():
    global shutdown_requested

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument("--node-count", type=int, required=True)
    parser.add_argument("--interval", type=float, default=2.0, help="Sampling interval in seconds (default: 2.0)")
    parser.add_argument("--output", required=True, help="Output CSV file")
    parser.add_argument("--workers", type=int, default=32, help="Parallel workers (default: 32)")
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = [f"node{i:02d}" for i in range(1, args.node_count + 1)]

    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "cycle_id", "cycle_start_utc", "cycle_end_utc", "node",
            "mempool_size", "mempool_bytes", "mempool_usage", "mempool_max", "mempool_minfee"
        ])
        writer.writeheader()
        f.flush()

        print(f"Monitoring mempool every {args.interval}s with {args.workers} parallel workers...", flush=True)
        cycle_id = 0

        while not shutdown_requested:
            cycle_start = datetime.now(timezone.utc)
            cycle_id += 1

            # Query all nodes in parallel
            results = []
            try:
                with ThreadPoolExecutor(max_workers=args.workers) as executor:
                    futures = {executor.submit(get_mempool_info, node): node for node in nodes}
                    for future in as_completed(futures):
                        if shutdown_requested:
                            break
                        node, info = future.result()
                        if info:
                            results.append((node, info))
            except Exception:
                if shutdown_requested:
                    break
                raise

            if shutdown_requested:
                break

            cycle_end = datetime.now(timezone.utc)

            # Build all rows first, then write atomically
            rows = []
            for node, info in results:
                rows.append({
                    "cycle_id": cycle_id,
                    "cycle_start_utc": cycle_start.isoformat(),
                    "cycle_end_utc": cycle_end.isoformat(),
                    "node": node,
                    "mempool_size": info.get("size", 0),
                    "mempool_bytes": info.get("bytes", 0),
                    "mempool_usage": info.get("usage", 0),
                    "mempool_max": info.get("maxmempool", 0),
                    "mempool_minfee": info.get("mempoolminfee", 0),
                })

            # Write all rows and flush immediately
            for row in rows:
                writer.writerow(row)
            f.flush()

            # Calculate sleep time to maintain consistent interval
            elapsed = (cycle_end - cycle_start).total_seconds()
            sleep_time = max(0, args.interval - elapsed)
            if sleep_time > 0:
                # Use shorter sleep intervals to check shutdown flag
                sleep_end = time.time() + sleep_time
                while time.time() < sleep_end and not shutdown_requested:
                    time.sleep(min(0.1, sleep_end - time.time()))

        # Final flush before exit
        f.flush()
        print(f"\nMonitoring stopped after {cycle_id} cycles.", flush=True)


if __name__ == "__main__":
    main()





