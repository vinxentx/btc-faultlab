#!/usr/bin/env python3
"""
Incremental Snapshot Chain Builder

Builds a blockchain snapshot by mining blocks incrementally as transactions
are created. This avoids the descriptor wallet 'map::at' bug that occurs
when spending long chains of unconfirmed change outputs.

Approach:
  For each block:
    1. Each shard wallet sends N transactions (using confirmed UTXOs only)
    2. Mine 1 block immediately to confirm the change
    3. Repeat

This ensures we never spend unconfirmed change, avoiding the bug entirely.
"""
import argparse
import base64
import json
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.client import HTTPConnection
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple


JSONRPC_HEADERS = {"Content-Type": "application/json"}


class RPCClient:
    """Thread-safe RPC client with connection pooling."""

    def __init__(self, host: str, auth: str, timeout: int = 30):
        self.host = host
        self.auth_header = "Basic " + base64.b64encode(auth.encode()).decode()
        self.timeout = timeout
        self._local = threading.local()

    def _get_conn(self) -> HTTPConnection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = HTTPConnection(self.host, timeout=self.timeout)
            self._local.conn = conn
        return conn

    def _reset_conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        self._local.conn = None

    def call(self, method: str, params: list = None, wallet: str = None) -> Any:
        payload = json.dumps({
            "jsonrpc": "1.0",
            "id": "snapshot",
            "method": method,
            "params": params or []
        })
        headers = {
            "Content-Type": "application/json",
            "Authorization": self.auth_header
        }
        path = f"/wallet/{wallet}" if wallet else "/"

        for attempt in range(3):
            try:
                conn = self._get_conn()
                conn.request("POST", path, payload, headers)
                resp = conn.getresponse()
                body = resp.read()
                data = json.loads(body)
                if data.get("error"):
                    raise RPCError(data["error"])
                return data["result"]
            except (ConnectionError, BrokenPipeError, OSError) as e:
                self._reset_conn()
                if attempt == 2:
                    raise
                time.sleep(0.1 * (attempt + 1))
        raise RuntimeError("RPC call failed after retries")


class RPCError(Exception):
    def __init__(self, error_dict):
        self.code = error_dict.get("code", -1)
        self.message = error_dict.get("message", str(error_dict))
        super().__init__(self.message)


def load_config(state_dir: Path, config_file: str = "builder_targets.json") -> Dict[str, Any]:
    """Load wallet configuration from JSON file."""
    path = state_dir / config_file
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    return json.loads(path.read_text())


def ensure_wallet_loaded(client: RPCClient, wallet: str) -> None:
    """Ensure wallet is loaded, ignore if already loaded."""
    try:
        client.call("loadwallet", [wallet])
    except RPCError as e:
        if "already loaded" not in e.message.lower():
            raise


def get_confirmed_utxo_count(client: RPCClient, wallet: str) -> int:
    """Count confirmed UTXOs (1+ confirmations)."""
    utxos = client.call("listunspent", [1, 9999999], wallet=wallet)
    return len(utxos)


def send_transactions(
    client: RPCClient,
    wallet: str,
    dest_address: str,
    amount: float,
    count: int
) -> Tuple[int, int]:
    """
    Send multiple transactions from a wallet.
    Returns (success_count, error_count).
    Only uses confirmed UTXOs by letting the wallet choose.
    """
    success = 0
    errors = 0

    for _ in range(count):
        try:
            client.call("sendtoaddress", [dest_address, amount], wallet=wallet)
            success += 1
        except RPCError as e:
            errors += 1
            # If we hit the map::at bug or similar, stop this wallet for this round
            if "map::at" in e.message or errors > 3:
                break
        except Exception:
            errors += 1
            if errors > 3:
                break

    return success, errors


def mine_block(client: RPCClient, address: str) -> str:
    """Mine a single block and return its hash."""
    result = client.call("generatetoaddress", [1, address])
    return result[0] if result else None


def wait_for_sync(
    clients: List[Tuple[RPCClient, str]],
    target_height: int,
    timeout_s: int = 60
) -> bool:
    """Wait for all nodes to reach target height."""
    deadline = time.time() + timeout_s

    while time.time() < deadline:
        all_synced = True
        for client, name in clients:
            try:
                height = client.call("getblockcount")
                if height < target_height:
                    all_synced = False
                    break
            except Exception:
                all_synced = False
                break

        if all_synced:
            return True
        time.sleep(0.5)

    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build snapshot chain incrementally (avoids map::at bug)"
    )
    parser.add_argument("--rpc-user", required=True)
    parser.add_argument("--rpc-pass", required=True)
    parser.add_argument("--state-dir", default="/Users/vincenttietze/btc-faultlab/state")
    parser.add_argument("--config-file", default="builder_targets.json",
                        help="Config file name in state-dir (default: builder_targets.json)")
    parser.add_argument("--miner-host", default="127.0.0.1")
    parser.add_argument("--miner-port", type=int, default=20443)
    parser.add_argument("--blocks", type=int, default=600,
                        help="Number of blocks to mine")
    parser.add_argument("--txs-per-block", type=int, default=120,
                        help="Target transactions per block")
    parser.add_argument("--amount-btc", type=float, default=0.00001,
                        help="Amount per transaction")
    parser.add_argument("--parallel-wallets", type=int, default=4,
                        help="Number of wallets to send from in parallel")
    parser.add_argument("--sync-timeout", type=int, default=30,
                        help="Seconds to wait for block propagation")
    parser.add_argument("--progress-interval", type=int, default=25,
                        help="Report progress every N blocks")
    parser.add_argument("--initial-blocks", type=int, default=0,
                        help="Mine this many empty blocks first (for maturity)")
    args = parser.parse_args()

    auth = f"{args.rpc_user}:{args.rpc_pass}"
    state_dir = Path(args.state_dir)

    # Load configuration
    print(f"Loading configuration from {args.config_file}...")
    config = load_config(state_dir, args.config_file)
    shards = config.get("shards", [])
    mining_address = config.get("mining_address")

    if not shards:
        print("ERROR: No shard wallets found in config")
        return 1

    if not mining_address:
        print("ERROR: No mining address in config")
        return 1

    print(f"Found {len(shards)} shard wallets")
    print(f"Mining address: {mining_address}")
    print(f"Target: {args.blocks} blocks with ~{args.txs_per_block} txs each")

    # Create miner client
    miner_client = RPCClient(f"{args.miner_host}:{args.miner_port}", auth)

    # Get starting height
    start_height = miner_client.call("getblockcount")
    print(f"Starting height: {start_height}")

    # Mine initial empty blocks if requested (for coinbase maturity)
    if args.initial_blocks > 0:
        print(f"Mining {args.initial_blocks} initial blocks...")
        miner_client.call("generatetoaddress", [args.initial_blocks, mining_address])
        start_height = miner_client.call("getblockcount")
        print(f"New starting height: {start_height}")

    # Setup wallet clients
    wallet_clients: List[Tuple[RPCClient, str, str]] = []  # (client, wallet_name, dest_addr)

    for shard in shards:
        wallet = shard.get("wallet")
        host = shard.get("host")
        if not wallet or not host:
            continue

        client = RPCClient(host, auth)
        ensure_wallet_loaded(client, wallet)

        # Get a destination address for this wallet
        dest_addr = client.call("getnewaddress", [], wallet=wallet)

        # Check UTXO count
        utxo_count = get_confirmed_utxo_count(client, wallet)
        print(f"  {wallet}: {utxo_count} confirmed UTXOs")

        wallet_clients.append((client, wallet, dest_addr))

    if not wallet_clients:
        print("ERROR: No wallet clients configured")
        return 1

    num_wallets = len(wallet_clients)
    txs_per_wallet = max(1, args.txs_per_block // num_wallets)

    print(f"\nWill send ~{txs_per_wallet} txs per wallet per block ({num_wallets} wallets)")
    print(f"Starting incremental mining...\n")

    # Stats tracking
    total_txs = 0
    total_errors = 0
    blocks_mined = 0
    start_time = time.time()

    # Main loop: for each block
    for block_num in range(1, args.blocks + 1):
        block_txs = 0
        block_errors = 0

        # Send transactions from all wallets (can parallelize if needed)
        if args.parallel_wallets >= num_wallets:
            # All wallets in parallel
            with ThreadPoolExecutor(max_workers=num_wallets) as executor:
                futures = []
                for client, wallet, dest_addr in wallet_clients:
                    future = executor.submit(
                        send_transactions,
                        client, wallet, dest_addr, args.amount_btc, txs_per_wallet
                    )
                    futures.append(future)

                for future in as_completed(futures):
                    success, errors = future.result()
                    block_txs += success
                    block_errors += errors
        else:
            # Sequential
            for client, wallet, dest_addr in wallet_clients:
                success, errors = send_transactions(
                    client, wallet, dest_addr, args.amount_btc, txs_per_wallet
                )
                block_txs += success
                block_errors += errors

        # Mine a block to confirm all transactions
        try:
            block_hash = mine_block(miner_client, mining_address)
            blocks_mined += 1
        except Exception as e:
            print(f"ERROR mining block {block_num}: {e}")
            continue

        total_txs += block_txs
        total_errors += block_errors

        # Progress report
        if block_num % args.progress_interval == 0 or block_num == args.blocks:
            elapsed = time.time() - start_time
            rate = total_txs / elapsed if elapsed > 0 else 0
            current_height = start_height + blocks_mined
            print(f"Block {block_num}/{args.blocks}: "
                  f"height={current_height}, "
                  f"txs_this_block={block_txs}, "
                  f"total_txs={total_txs}, "
                  f"rate={rate:.1f} tx/s, "
                  f"errors={total_errors}")

    # Final stats
    elapsed = time.time() - start_time
    final_height = miner_client.call("getblockcount")

    print(f"\n{'='*60}")
    print(f"Snapshot generation complete!")
    print(f"  Blocks mined: {blocks_mined}")
    print(f"  Final height: {final_height}")
    print(f"  Total transactions: {total_txs}")
    print(f"  Total errors: {total_errors}")
    print(f"  Time elapsed: {elapsed:.1f}s")
    print(f"  Average rate: {total_txs/elapsed:.1f} tx/s")
    print(f"  Average txs/block: {total_txs/blocks_mined:.1f}" if blocks_mined > 0 else "")
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
