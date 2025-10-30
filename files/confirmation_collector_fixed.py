#!/usr/bin/env python3
"""
Robust RPC-based confirmation collector.
Fixed version that handles large blockchains efficiently.
"""

import argparse
import json
import base64
import time
import csv
from datetime import datetime, timezone
from http.client import HTTPConnection
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def create_session():
    """Create a requests session with retry strategy"""
    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def rpc_call_robust(session, url, method, params=None, auth=None, timeout=30):
    """Make a robust RPC call with retries and proper error handling"""
    payload = {
        "jsonrpc": "1.0", 
        "id": "confirmation_collector", 
        "method": method, 
        "params": params or []
    }
    
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    
    try:
        response = session.post(url, json=payload, headers=headers, timeout=timeout)
        response.raise_for_status()
        result = response.json()
        
        if result.get("error"):
            raise RuntimeError(f"RPC Error: {result['error']}")
        
        return result["result"]
    except Exception as e:
        print(f"RPC call failed: {e}")
        raise


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


def find_confirmations_robust(session, rpc_url, auth, submissions, start_height=None):
    """Find confirmations by scanning recent blocks efficiently."""
    print(f"Loading transaction submissions...")
    print(f"Loaded {len(submissions)} transaction submissions")
    
    if not submissions:
        print("No transactions to process")
        return []
    
    # Get current block height
    try:
        current_height = rpc_call_robust(session, rpc_url, "getblockcount", auth=auth)
        print(f"Current block height: {current_height}")
    except Exception as e:
        print(f"Failed to get block height: {e}")
        return []
    
    # Determine scan range - only scan recent blocks
    if start_height is None:
        # Scan last 200 blocks or from height 0, whichever is smaller
        start_height = max(0, current_height - 200)
    
    print(f"Scanning blocks {start_height} to {current_height} ({current_height - start_height + 1} blocks)")
    
    confirmations = []
    found_count = 0
    batch_size = 10  # Process blocks in batches
    
    for batch_start in range(start_height, current_height + 1, batch_size):
        batch_end = min(batch_start + batch_size - 1, current_height)
        
        print(f"Processing blocks {batch_start}-{batch_end}...")
        
        for height in range(batch_start, batch_end + 1):
            try:
                # Get block hash
                block_hash = rpc_call_robust(session, rpc_url, "getblockhash", [height], auth=auth)
                
                # Get block details (verbosity 2 to get transaction details)
                block = rpc_call_robust(session, rpc_url, "getblock", [block_hash, 2], auth=auth)
                block_ts = datetime.fromtimestamp(block["time"], tz=timezone.utc).isoformat()
                
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
                
                # Small delay to avoid overwhelming the RPC
                time.sleep(0.1)
                
            except Exception as e:
                print(f"Error processing block {height}: {e}")
                continue
        
        # Longer delay between batches
        time.sleep(0.5)
    
    print(f"Found {found_count} confirmations out of {len(submissions)} submitted transactions")
    return confirmations


def main():
    parser = argparse.ArgumentParser(description="Robust transaction confirmation collector")
    parser.add_argument("--rpc", required=True, help="RPC URL (e.g., http://user:pass@127.0.0.1:18443)")
    parser.add_argument("--txlog", required=True, help="Path to txlog.csv")
    parser.add_argument("--out", required=True, help="Output CSV file")
    parser.add_argument("--start-height", type=int, help="Starting block height (default: last 200 blocks)")
    parser.add_argument("--max-blocks", type=int, default=200, help="Maximum blocks to scan (default: 200)")
    
    args = parser.parse_args()
    
    print("Robust Confirmation Collector starting...")
    print(f"RPC: {args.rpc}")
    print(f"TX Log: {args.txlog}")
    print(f"Output: {args.out}")
    
    # Parse RPC URL
    proto, rest = args.rpc.split("://")
    if "@" in rest:
        cred, host = rest.split("@")
        auth = cred
    else:
        host = rest
        auth = None
    
    rpc_url = f"{proto}://{host}"
    
    # Create robust session
    session = create_session()
    
    # Load transaction submissions
    try:
        submissions = load_txlog(args.txlog)
    except Exception as e:
        print(f"Failed to load transaction log: {e}")
        return 1
    
    # Find confirmations
    try:
        confirmations = find_confirmations_robust(session, rpc_url, auth, submissions, args.start_height)
    except Exception as e:
        print(f"Failed to find confirmations: {e}")
        return 1
    
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
        # Create empty file to avoid analysis errors
        with open(args.out, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['txid', 'submit_ts_utc', 'confirm_ts_utc', 'block_hash', 'block_height', 'latency_seconds']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
    
    return 0


if __name__ == "__main__":
    exit(main())


















