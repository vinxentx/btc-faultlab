import argparse, time, json, base64, os, random
from datetime import datetime, timezone
from http.client import HTTPConnection


def rpc_call(url, method, params=None, auth=None, wallet=None, timeout=5):
    proto, rest = url.split("://")
    host = rest
    conn = HTTPConnection(host, timeout=timeout)
    payload = json.dumps({"jsonrpc": "1.0", "id": "txgen", "method": method, "params": params or []})
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    path = f"/wallet/{wallet}" if wallet else "/"
    conn.request("POST", path, payload, headers)
    resp = conn.getresponse()
    out = json.loads(resp.read())
    conn.close()
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out["result"]


def wait_for_rpc(rpc_url: str, auth: str, timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    last_err = None
    print(f"Waiting for RPC at {rpc_url}...")
    while time.time() < deadline:
        try:
            result = rpc_call(rpc_url, "getblockcount", auth=auth, timeout=5)
            print(f"RPC ready! Block count: {result}")
            return
        except Exception as e:
            last_err = e
            print(f"RPC not ready yet: {e}")
            time.sleep(3)
    raise RuntimeError(f"RPC not ready after {timeout_s}s: {last_err}")


def get_mining_nodes(node_count, mining_percentage=0.08):
    """
    Determine mining nodes based on total node count.
    Uses first N core nodes as miners (8% of network, increased from 5% for better resilience).
    Minimum 2 miners for resilience.
    """
    num_miners = max(2, int(node_count * mining_percentage))
    miners = [f"node{i:02d}:18443" for i in range(1, num_miners + 1)]
    print(f"📊 Mining Configuration:")
    print(f"   Total nodes: {node_count}")
    print(f"   Mining percentage: {mining_percentage * 100}%")
    print(f"   Active miners: {num_miners}")
    print(f"   Mining nodes: {', '.join(miners)}")
    return miners


def get_healthy_miner(miners, auth):
    """
    Select a healthy mining node using round-robin with health checks.
    Returns: miner_host (e.g., "node03:18443")
    Raises: RuntimeError if no miners available
    """
    # Shuffle for randomness, but deterministic within mining cycle
    candidates = miners.copy()
    random.shuffle(candidates)
    
    for miner_host in candidates:
        try:
            proto = "http"
            # Quick health check: can we reach the node?
            rpc_call(f"{proto}://{miner_host}", "getblockcount", [], auth=auth, timeout=2)
            return miner_host
        except Exception as e:
            print(f"⚠️  Miner {miner_host} unavailable: {e}")
            continue
    
    # All miners failed
    raise RuntimeError("❌ No mining nodes available! Network cannot produce blocks.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, required=True, help="tx per second")
    ap.add_argument("--rpc", type=str, required=True, help="e.g. http://user:pass@wallet:18443")
    ap.add_argument("--log", type=str, required=True, help="path to txlog.csv")
    ap.add_argument("--node-count", type=int, required=True, help="total number of nodes in network")
    ap.add_argument("--mining-percentage", type=float, default=0.05, help="percentage of nodes that mine (default: 0.05 = 5%%)")
    args = ap.parse_args()

    proto, rest = args.rpc.split("://")
    cred, host = rest.split("@")
    auth = cred

    # Ensure results dir exists and is writable
    log_dir = os.path.dirname(args.log)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Determine mining nodes based on network size
    mining_nodes = get_mining_nodes(args.node_count, args.mining_percentage)

    # Wait for initial RPC to be reachable (longer timeout for large networks)
    wait_for_rpc(proto + "://" + host, auth=auth, timeout_s=600)

    # Wallet setup on wallet node (separate from mining nodes)
    try:
        rpc_call(proto + "://" + host, "createwallet", ["faultlab"], auth=auth)
    except RuntimeError as e:
        if "already exists" not in str(e):
            raise

    # Load the wallet
    try:
        rpc_call(proto + "://" + host, "loadwallet", ["faultlab"], auth=auth)
    except RuntimeError as e:
        if "already loaded" not in str(e):
            raise

    wallet = "faultlab"
    addr = rpc_call(proto + "://" + host, "getnewaddress", auth=auth, wallet=wallet)
    
    # Generate initial blocks for funding using distributed miners
    print(f"🔨 Generating initial 201 blocks for funding using {len(mining_nodes)} miners...")
    blocks_per_miner = 201 // len(mining_nodes)
    remaining_blocks = 201 % len(mining_nodes)
    
    for i, miner in enumerate(mining_nodes):
        blocks_to_mine = blocks_per_miner + (1 if i < remaining_blocks else 0)
        try:
            print(f"   {miner}: generating {blocks_to_mine} blocks...")
            rpc_call(f"{proto}://{miner}", "generatetoaddress", [blocks_to_mine, addr], auth=auth)
        except Exception as e:
            print(f"   ⚠️  {miner} failed, using fallback: {e}")
            # Fallback to any healthy miner
            fallback = get_healthy_miner(mining_nodes, auth)
            rpc_call(f"{proto}://{fallback}", "generatetoaddress", [blocks_to_mine, addr], auth=auth)
    
    print(f"✅ Initial funding complete, wallet has mature coins")

    interval = 1.0 / args.rate if args.rate > 0 else 0.1
    # Mine more frequently for high transaction rates
    mine_interval = max(1.0, 10.0 / args.rate) if args.rate > 10 else 5.0
    print(f"⚡ Starting transaction generation: rate={args.rate} tx/s, mining every {mine_interval:.1f}s")
    print(f"💎 Mining will rotate between {len(mining_nodes)} distributed miners")
    
    # Prepare mining stats file
    mining_stats_file = args.log.replace('.csv', '_mining.csv')
    
    with open(args.log, "w", encoding="utf-8") as txlog, \
         open(mining_stats_file, "w", encoding="utf-8") as minelog:
        
        txlog.write("submit_ts_utc,txid\n")  # Transactions don't have miner info
        minelog.write("timestamp_utc,block_number,miner,block_hash\n")  # Mining events
        
        next_mine = time.time() + mine_interval
        blocks_mined = 0
        mining_stats = {miner: 0 for miner in mining_nodes}
        current_miner = None
        
        while True:
            try:
                dst = rpc_call(proto + "://" + host, "getnewaddress", auth=auth, wallet=wallet)
                txid = rpc_call(proto + "://" + host, "sendtoaddress", [dst, 0.0001], auth=auth, wallet=wallet)
                submit_ts = datetime.now(timezone.utc).isoformat()
                txlog.write(f"{submit_ts},{txid}\n")
                txlog.flush()
                print(f"📤 Submitted transaction {txid[:16]}...")
            except RuntimeError as e:
                if "Unconfirmed UTXOs" in str(e):
                    print(f"⚠️  UTXO issue, mining emergency block to confirm transactions...")
                    try:
                        emergency_miner = get_healthy_miner(mining_nodes, auth)
                        block_hashes = rpc_call(f"{proto}://{emergency_miner}", "generatetoaddress", [1, addr], auth=auth)
                        mining_stats[emergency_miner] = mining_stats.get(emergency_miner, 0) + 1
                        blocks_mined += 1
                        
                        # Log emergency mining event
                        mine_ts = datetime.now(timezone.utc).isoformat()
                        block_hash = block_hashes[0] if block_hashes else "unknown"
                        minelog.write(f"{mine_ts},{blocks_mined},{emergency_miner},{block_hash}\n")
                        minelog.flush()
                    except Exception as mine_err:
                        print(f"❌ Emergency mining failed: {mine_err}")
                    continue
                else:
                    print(f"⚠️  Transaction failed: {e}")
                    time.sleep(1)
                    continue
            
            # Regular mining interval
            if time.time() >= next_mine:
                try:
                    # Select healthy miner for this block
                    selected_miner = get_healthy_miner(mining_nodes, auth)
                    print(f"⛏️  Mining block #{blocks_mined + 1} on {selected_miner}...")
                    block_hashes = rpc_call(f"{proto}://{selected_miner}", "generatetoaddress", [1, addr], auth=auth)
                    mining_stats[selected_miner] = mining_stats.get(selected_miner, 0) + 1
                    blocks_mined += 1
                    current_miner = selected_miner
                    
                    # Log mining event to CSV
                    mine_ts = datetime.now(timezone.utc).isoformat()
                    block_hash = block_hashes[0] if block_hashes else "unknown"
                    minelog.write(f"{mine_ts},{blocks_mined},{selected_miner},{block_hash}\n")
                    minelog.flush()
                    
                    # Log mining statistics every 10 blocks
                    if blocks_mined % 10 == 0:
                        print(f"📊 Mining stats after {blocks_mined} blocks:")
                        for miner, count in sorted(mining_stats.items()):
                            print(f"   {miner}: {count} blocks ({count/blocks_mined*100:.1f}%)")
                    
                except Exception as e:
                    print(f"❌ Mining failed: {e}")
                    print(f"   Will retry on next interval...")
                
                next_mine = time.time() + mine_interval
            
            time.sleep(max(0, interval))
