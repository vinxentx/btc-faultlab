#!/usr/bin/env python3
"""
Simple RPC-based confirmation collector.
Replaces the ZMQ collector with a more reliable approach.
"""

import argparse
import json
import base64
import time
import csv
from datetime import datetime, timezone
from http.client import HTTPConnection


def rpc_call(url, method, params=None, auth=None, timeout=5):
    """Make an RPC call to Bitcoin Core."""
    proto, rest = url.split("://")
    host = rest
    conn = HTTPConnection(host, timeout=timeout)
    payload = json.dumps({"jsonrpc": "1.0", "id": "confirmation_collector", "method": method, "params": params or []})
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    conn.request("POST", "/", payload, headers)
    resp = conn.getresponse()
    out = json.loads(resp.read())
    conn.close()
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out["result"]


def load_txlog(txlog_path):
    """Load transaction submission times from CSV."""
    submissions = {}
    with open(txlog_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            txid = row['txid']
            submit_ts = row['submit_ts_utc']
            submissions[txid] = submit_ts
    return submissions


def find_confirmations(rpc_url, auth, submissions, start_height=0):
    """Find confirmations by scanning blocks."""
    print(f"Scanning blocks from height {start_height}...")
    
    # Get current block height
    current_height = rpc_call(rpc_url, "getblockcount", auth=auth)
    print(f"Current block height: {current_height}")
    
    confirmations = []
    found_count = 0
    
    for height in range(start_height, current_height + 1):
        try:
            # Get block hash
            block_hash = rpc_call(rpc_url, "getblockhash", [height], auth=auth)
            
            # Get block details
            block = rpc_call(rpc_url, "getblock", [block_hash, 2], auth=auth)
            block_ts = datetime.fromtimestamp(block["time"], tz=timezone.utc).isoformat()
            
            print(f"Scanning block {height} ({block_hash[:16]}...) with {len(block['tx'])} transactions")
            
            # Check each transaction in the block
            for tx in block["tx"]:
                txid = tx["txid"]
                if txid in submissions:
                    submit_ts = submissions[txid]
                    submit_dt = datetime.fromisoformat(submit_ts.replace("Z", "+00:00"))
                    latency = (datetime.fromtimestamp(block["time"], tz=timezone.utc) - submit_dt).total_seconds()
                    
                    confirmations.append({
                        'txid': txid,
                        'submit_ts_utc': submit_ts,
                        'confirm_ts_utc': block_ts,
                        'block_hash': block_hash,
                        'block_height': height,
                        'latency_seconds': f"{latency:.3f}"
                    })
                    found_count += 1
                    print(f"  ✓ Confirmed {txid[:16]}... (latency: {latency:.3f}s)")
            
        except Exception as e:
            print(f"Error processing block {height}: {e}")
            continue
    
    print(f"Found {found_count} confirmations out of {len(submissions)} submitted transactions")
    return confirmations


def main():
    parser = argparse.ArgumentParser(description="Collect transaction confirmations via RPC")
    parser.add_argument("--rpc", required=True, help="RPC URL (e.g., http://user:pass@127.0.0.1:18443)")
    parser.add_argument("--txlog", required=True, help="Path to txlog.csv")
    parser.add_argument("--out", required=True, help="Output CSV file")
    parser.add_argument("--start-height", type=int, default=0, help="Starting block height to scan")
    args = parser.parse_args()
    
    print("Confirmation Collector starting...")
    print(f"RPC: {args.rpc}")
    print(f"TX Log: {args.txlog}")
    print(f"Output: {args.out}")
    
    # Parse RPC URL
    proto, rest = args.rpc.split("://")
    cred, host = rest.split("@")
    auth = cred
    
    # Load transaction submissions
    print("Loading transaction submissions...")
    submissions = load_txlog(args.txlog)
    print(f"Loaded {len(submissions)} transaction submissions")
    
    if not submissions:
        print("No transactions to process")
        return 0
    
    # Find confirmations
    confirmations = find_confirmations(proto + "://" + host, auth, submissions, args.start_height)
    
    # Write results
    if confirmations:
        with open(args.out, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['txid', 'submit_ts_utc', 'confirm_ts_utc', 'block_hash', 'block_height', 'latency_seconds']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(confirmations)
        print(f"Wrote {len(confirmations)} confirmations to {args.out}")
    else:
        print("No confirmations found")
    
    return 0


if __name__ == "__main__":
    exit(main())

