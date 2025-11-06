import argparse, time, json, base64, os, random
from datetime import datetime, timezone
from http.client import HTTPConnection


def rpc_call(url, method, params=None, auth=None, wallet=None, timeout=10):
    """
    Make RPC call with adaptive timeout for large networks.
    Default timeout is 10s (increased from 5s for large networks).
    """
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


def wait_for_rpc(rpc_url: str, auth: str, timeout_s: int = 120, node_count: int = 32) -> None:
    """
    Wait for RPC to be ready, with adaptive timeout for large networks.
    """
    # Increase timeout for large networks (more nodes = more sync time)
    if node_count >= 64:
        timeout_s = max(timeout_s, 300)  # At least 5 minutes for 64+ nodes
    if node_count >= 128:
        timeout_s = max(timeout_s, 600)  # At least 10 minutes for 128 nodes
    
    deadline = time.time() + timeout_s
    last_err = None
    print(f"Waiting for RPC at {rpc_url}... (timeout: {timeout_s}s for {node_count} nodes)")
    while time.time() < deadline:
        try:
            result = rpc_call(rpc_url, "getblockcount", auth=auth, timeout=10)
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
            # Quick health check: can we reach the node? (longer timeout for large networks)
            rpc_call(f"{proto}://{miner_host}", "getblockcount", [], auth=auth, timeout=5)
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

    # Wait for initial RPC to be reachable (adaptive timeout for large networks)
    wait_for_rpc(proto + "://" + host, auth=auth, timeout_s=600, node_count=args.node_count)

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
    # We need 201 blocks initially, then 100 more to ensure coins are mature (100 confirmations required)
    initial_blocks = 201
    maturity_blocks = 100
    total_blocks = initial_blocks + maturity_blocks
    
    print(f"🔨 Generating {initial_blocks} initial blocks for funding using {len(mining_nodes)} miners...")
    blocks_per_miner = initial_blocks // len(mining_nodes)
    remaining_blocks = initial_blocks % len(mining_nodes)
    
    for i, miner in enumerate(mining_nodes):
        blocks_to_mine = blocks_per_miner + (1 if i < remaining_blocks else 0)
        try:
            print(f"   {miner}: generating {blocks_to_mine} blocks...")
            # Use longer timeout for initial mining in large networks
            initial_timeout = 30 if args.node_count < 64 else 60 if args.node_count < 128 else 90
            rpc_call(f"{proto}://{miner}", "generatetoaddress", [blocks_to_mine, addr], auth=auth, timeout=initial_timeout)
        except Exception as e:
            print(f"   ⚠️  {miner} failed, using fallback: {e}")
            # Fallback to any healthy miner
            fallback = get_healthy_miner(mining_nodes, auth)
            initial_timeout = 30 if args.node_count < 64 else 60 if args.node_count < 128 else 90
            rpc_call(f"{proto}://{fallback}", "generatetoaddress", [blocks_to_mine, addr], auth=auth, timeout=initial_timeout)
    
    print(f"🔨 Generating {maturity_blocks} additional blocks to mature coins (100 confirmations required)...")
    maturity_blocks_per_miner = maturity_blocks // len(mining_nodes)
    remaining_maturity = maturity_blocks % len(mining_nodes)
    
    for i, miner in enumerate(mining_nodes):
        blocks_to_mine = maturity_blocks_per_miner + (1 if i < remaining_maturity else 0)
        try:
            print(f"   {miner}: generating {blocks_to_mine} maturity blocks...")
            maturity_timeout = 30 if args.node_count < 64 else 60 if args.node_count < 128 else 90
            rpc_call(f"{proto}://{miner}", "generatetoaddress", [blocks_to_mine, addr], auth=auth, timeout=maturity_timeout)
        except Exception as e:
            print(f"   ⚠️  {miner} failed, using fallback: {e}")
            fallback = get_healthy_miner(mining_nodes, auth)
            maturity_timeout = 30 if args.node_count < 64 else 60 if args.node_count < 128 else 90
            rpc_call(f"{proto}://{fallback}", "generatetoaddress", [blocks_to_mine, addr], auth=auth, timeout=maturity_timeout)
    
    # Wait for wallet to synchronize with network (critical for large networks)
    print(f"⏳ Waiting for wallet to synchronize with network ({args.node_count} nodes)...")
    rpc_url = proto + "://" + host
    sync_timeout = 60 if args.node_count < 64 else 120 if args.node_count < 128 else 180  # More time for larger networks
    sync_start = time.time()
    
    # Get expected block height from a mining node
    try:
        sample_miner = get_healthy_miner(mining_nodes, auth)
        expected_height = rpc_call(f"{proto}://{sample_miner}", "getblockcount", auth=auth, timeout=10)
        print(f"   Expected block height: {expected_height}")
    except Exception as e:
        print(f"   ⚠️  Could not get expected height from miner: {e}, using wallet height")
        expected_height = None
    
    while time.time() - sync_start < sync_timeout:
        try:
            wallet_height = rpc_call(rpc_url, "getblockcount", auth=auth, timeout=10)
            if expected_height is None:
                expected_height = wallet_height
            
            # Check if wallet is synced (within 1 block tolerance)
            if wallet_height >= expected_height - 1:
                print(f"✅ Wallet synchronized: height {wallet_height} (expected: {expected_height})")
                break
            else:
                print(f"⏳ Wallet syncing... height {wallet_height}/{expected_height}")
                time.sleep(2)
        except Exception as e:
            print(f"⚠️  Sync check failed: {e}")
            time.sleep(2)
    
    # Verify wallet has spendable balance before starting
    print(f"🔍 Verifying wallet has spendable coins...")
    max_wait = 60 if args.node_count < 64 else 120  # More time for large networks
    wait_start = time.time()
    while time.time() - wait_start < max_wait:
        try:
            balance_info = rpc_call(rpc_url, "getbalances", auth=auth, wallet=wallet, timeout=10)
            confirmed_balance = balance_info.get("mine", {}).get("trusted", 0.0)
            if confirmed_balance >= 0.001:  # At least 0.001 BTC spendable
                print(f"✅ Wallet verified: {confirmed_balance:.6f} BTC spendable")
                break
            else:
                print(f"⏳ Waiting for coins to mature... (current: {confirmed_balance:.6f} BTC)")
                time.sleep(3)
        except Exception as e:
            print(f"⚠️  Balance check failed: {e}, continuing anyway...")
            break
    
    print(f"✅ Initial funding complete, wallet has mature coins and is synchronized")

    interval = 1.0 / args.rate if args.rate > 0 else 0.1
    # Mine every 3s for standard rates (<=10 tx/s), more frequently for higher rates
    if args.rate <= 10:
        mine_interval = 3.0  # Fixed 3s for standard experiments
    else:
        mine_interval = max(1.0, 10.0 / args.rate)  # Dynamic for high rates
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
                # Use longer timeout for large networks
                rpc_timeout = 10 if args.node_count < 64 else 15 if args.node_count < 128 else 20
                dst = rpc_call(proto + "://" + host, "getnewaddress", auth=auth, wallet=wallet, timeout=rpc_timeout)
                txid = rpc_call(proto + "://" + host, "sendtoaddress", [dst, 0.0001], auth=auth, wallet=wallet, timeout=rpc_timeout)
                submit_ts = datetime.now(timezone.utc).isoformat()
                txlog.write(f"{submit_ts},{txid}\n")
                txlog.flush()
                print(f"📤 Submitted transaction {txid[:16]}...")
            except RuntimeError as e:
                error_str = str(e)
                # Handle insufficient funds and UTXO issues
                is_insufficient_funds = (
                    "insufficient funds" in error_str.lower() or 
                    "-6" in error_str or 
                    "Unconfirmed UTXOs" in error_str
                )
                
                if is_insufficient_funds:
                    print(f"⚠️  Funds/UTXO issue detected: {error_str}")
                    print(f"🚨 Triggering emergency mining to confirm transactions...")
                    try:
                        emergency_miner = get_healthy_miner(mining_nodes, auth)
                        # Use longer timeout for emergency mining in large networks
                        emergency_timeout = 20 if args.node_count < 64 else 30 if args.node_count < 128 else 40
                        block_hashes = rpc_call(f"{proto}://{emergency_miner}", "generatetoaddress", [1, addr], auth=auth, timeout=emergency_timeout)
                        mining_stats[emergency_miner] = mining_stats.get(emergency_miner, 0) + 1
                        blocks_mined += 1
                        
                        # Log emergency mining event
                        mine_ts = datetime.now(timezone.utc).isoformat()
                        block_hash = block_hashes[0] if block_hashes else "unknown"
                        minelog.write(f"{mine_ts},{blocks_mined},{emergency_miner},{block_hash}\n")
                        minelog.flush()
                        print(f"✅ Emergency block mined: {block_hash[:16]}...")
                        # Wait a bit after emergency mining to let transactions confirm
                        time.sleep(2.0)
                        next_mine = time.time() + mine_interval  # Reset regular mining timer
                    except Exception as mine_err:
                        print(f"❌ Emergency mining failed: {mine_err}")
                        time.sleep(5.0)  # Wait longer if emergency mining failed
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
                    # Use longer timeout for regular mining in large networks
                    mining_timeout = 20 if args.node_count < 64 else 30 if args.node_count < 128 else 40
                    block_hashes = rpc_call(f"{proto}://{selected_miner}", "generatetoaddress", [1, addr], auth=auth, timeout=mining_timeout)
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
