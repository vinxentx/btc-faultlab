#!/usr/bin/env python3
"""Monitor mempool size across all nodes during experiment."""
import argparse
import csv
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def get_mempool_info(node: str) -> dict | None:
    """Get mempool info from a node."""
    try:
        cmd = ["docker", "exec", node, "bitcoin-cli", "-regtest", "getmempoolinfo"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-count", type=int, required=True)
    parser.add_argument("--interval", type=int, default=5, help="Sampling interval in seconds")
    parser.add_argument("--output", required=True, help="Output CSV file")
    args = parser.parse_args()
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "timestamp_utc", "node", "mempool_size", "mempool_bytes", 
            "mempool_usage", "mempool_max", "mempool_minfee"
        ])
        writer.writeheader()
        
        print(f"Monitoring mempool every {args.interval}s...", flush=True)
        try:
            while True:
                timestamp = datetime.now(timezone.utc).isoformat()
                
                for i in range(1, args.node_count + 1):
                    node = f"node{i:02d}"
                    info = get_mempool_info(node)
                    
                    if info:
                        writer.writerow({
                            "timestamp_utc": timestamp,
                            "node": node,
                            "mempool_size": info.get("size", 0),
                            "mempool_bytes": info.get("bytes", 0),
                            "mempool_usage": info.get("usage", 0),
                            "mempool_max": info.get("maxmempool", 0),
                            "mempool_minfee": info.get("mempoolminfee", 0),
                        })
                        f.flush()
                
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nMonitoring stopped.", flush=True)


if __name__ == "__main__":
    main()




