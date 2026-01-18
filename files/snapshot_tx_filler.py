#!/usr/bin/env python3
import argparse
import base64
import json
import math
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from typing import Optional, Dict, Any, List


JSONRPC_HEADERS = {"Content-Type": "application/json"}


class PersistentRPCClient:
    def __init__(self, rpc_url: str, auth: Optional[str] = None):
        proto, rest = rpc_url.split("://", 1)
        if proto != "http":
            raise ValueError("Only http RPC endpoints are supported")
        self.host = rest
        self.auth = auth
        self._local = threading.local()

    def _get_connection(self, timeout: int) -> HTTPConnection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = HTTPConnection(self.host, timeout=timeout)
            self._local.conn = conn
        return conn

    def _close_connection(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def call(self, method: str, params=None, *, wallet: Optional[str], timeout: int = 30):
        payload = json.dumps({
            "jsonrpc": "1.0",
            "id": "snapshot_filler",
            "method": method,
            "params": params or []
        })
        headers = dict(JSONRPC_HEADERS)
        if self.auth:
            auth_str = base64.b64encode(self.auth.encode("utf-8")).decode("ascii")
            headers["Authorization"] = "Basic " + auth_str

        path = f"/wallet/{wallet}" if wallet else "/"

        for attempt in range(2):
            try:
                conn = self._get_connection(timeout)
                conn.request("POST", path, payload, headers)
                resp = conn.getresponse()
                body = resp.read()
                data = json.loads(body)
                if data.get("error"):
                    raise RuntimeError(data["error"])
                return data["result"]
            except (ConnectionError, BrokenPipeError, EOFError):
                self._close_connection()
                if attempt == 1:
                    raise
            except Exception:
                self._close_connection()
                raise


def decode_rpc_error(err: RuntimeError) -> str:
    if not err.args:
        return ""
    payload = err.args[0]
    if isinstance(payload, dict):
        return payload.get("message", "")
    return str(payload)


def ensure_wallet_loaded(client: PersistentRPCClient, wallet: str) -> None:
    try:
        client.call("loadwallet", [wallet], wallet=None, timeout=30)
    except RuntimeError as err:
        msg = decode_rpc_error(err).lower()
        if "already loaded" in msg or "duplicate" in msg:
            return
        raise


def get_mining_targets(state_dir: Path) -> Dict[str, Any]:
    path = state_dir / "mining_targets.json"
    if not path.exists():
        raise RuntimeError(f"mining_targets.json not found at {path}")
    return json.loads(path.read_text())


def mempool_size(rpc_url: str, auth: str) -> int:
    client = PersistentRPCClient(rpc_url, auth)
    info = client.call("getmempoolinfo", wallet=None, timeout=10)
    return int(info.get("size", 0))


def confirmed_utxos(client: PersistentRPCClient, wallet: str) -> int:
    utxos = client.call("listunspent", [1, 9999999], wallet=wallet, timeout=60)
    return len(utxos)


def monitor_mempool(rpc_url: str, auth: str, target: int, poll_s: float,
                    stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        size = mempool_size(rpc_url, auth)
        if size >= target:
            stop_event.set()
            return
        time.sleep(poll_s)


def worker_loop(shard_id: str, rpc_url: str, auth: str, wallet: str, addresses: List[str],
                amount_btc: float, per_wallet_target: int, rate: float,
                stop_event: threading.Event, progress_interval: int,
                counters: Dict[str, int], lock: threading.Lock) -> None:
    client = PersistentRPCClient(rpc_url, auth)
    ensure_wallet_loaded(client, wallet)
    sent = 0
    errors = 0
    next_send = time.monotonic()
    interval = 1.0 / rate if rate > 0 else 0.0

    addr_index = 0
    addr_count = len(addresses)
    if addr_count == 0:
        raise RuntimeError(f"No destination addresses for shard {shard_id}")

    while sent < per_wallet_target and not stop_event.is_set():
        if interval > 0:
            now = time.monotonic()
            if now < next_send:
                time.sleep(next_send - now)
            next_send += interval

        try:
            dest_addr = addresses[addr_index % addr_count]
            addr_index += 1
            client.call("sendtoaddress", [dest_addr, amount_btc], wallet=wallet, timeout=30)
            sent += 1
            with lock:
                counters["sent"] += 1
        except Exception as exc:
            errors += 1
            if errors <= 10:
                print(f"⚠️  Shard {shard_id}: sendtoaddress error: {exc}")
            time.sleep(0.5)

        if sent % progress_interval == 0:
            print(f"Shard {shard_id}: sent {sent}/{per_wallet_target} (errors {errors})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill mempool for snapshot mining using sendtoaddress.")
    parser.add_argument("--rpc-user", required=True)
    parser.add_argument("--rpc-pass", required=True)
    parser.add_argument("--state-dir", default="/state")
    parser.add_argument("--mempool-host", default="127.0.0.1")
    parser.add_argument("--mempool-port", type=int, default=20443)
    parser.add_argument("--mempool-target", type=int, required=True)
    parser.add_argument("--amount-btc", type=float, default=0.00001)
    parser.add_argument("--rate", type=float, default=0.0,
                        help="Global target rate (tx/s); 0 = no throttle")
    parser.add_argument("--min-confirmed-utxos", type=int, default=0,
                        help="Minimum confirmed UTXOs required per wallet (0 = skip check)")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--wallets", default="",
                        help="Comma-separated wallet names to use (default: all shards)")
    parser.add_argument("--address-mode", choices=["single", "pool"], default="single")
    parser.add_argument("--address-pool-size", type=int, default=50)
    parser.add_argument("--progress-interval", type=int, default=250)
    args = parser.parse_args()

    auth = f"{args.rpc_user}:{args.rpc_pass}"
    state_dir = Path(args.state_dir)
    data = get_mining_targets(state_dir)
    shards = data.get("shards", [])

    if args.wallets:
        allowed = {w.strip() for w in args.wallets.split(",") if w.strip()}
        shards = [s for s in shards if s.get("wallet") in allowed]

    if not shards:
        raise RuntimeError("No shard wallets found to fill mempool")

    mempool_rpc = f"http://{args.mempool_host}:{args.mempool_port}"

    wallet_count = len(shards)
    per_wallet_target = math.ceil(args.mempool_target / wallet_count)
    per_wallet_rate = (args.rate / wallet_count) if args.rate > 0 else 0.0

    print(f"Using {wallet_count} wallets, per-wallet target {per_wallet_target}, rate {per_wallet_rate:.2f} tx/s")

    stop_event = threading.Event()
    monitor = threading.Thread(
        target=monitor_mempool,
        args=(mempool_rpc, auth, args.mempool_target, args.poll_interval, stop_event),
        daemon=True,
    )
    monitor.start()

    counters = {"sent": 0}
    lock = threading.Lock()
    threads: List[threading.Thread] = []

    for shard in shards:
        wallet = shard.get("wallet")
        host = shard.get("host")
        shard_id = shard.get("id", wallet)
        if not wallet or not host:
            raise RuntimeError(f"Invalid shard entry: {shard}")
        rpc_url = f"http://{host}"
        client = PersistentRPCClient(rpc_url, auth)
        ensure_wallet_loaded(client, wallet)
        min_utxos = args.min_confirmed_utxos
        if min_utxos > 0:
            utxo_count = confirmed_utxos(client, wallet)
            if utxo_count < min_utxos:
                raise RuntimeError(
                    f"Wallet {wallet} has {utxo_count} confirmed UTXOs, need {min_utxos}."
                )
        if args.address_mode == "pool":
            addresses = [
                client.call("getnewaddress", [], wallet=wallet, timeout=30)
                for _ in range(args.address_pool_size)
            ]
        else:
            addresses = [client.call("getnewaddress", [], wallet=wallet, timeout=30)]

        t = threading.Thread(
            target=worker_loop,
            args=(
                shard_id,
                rpc_url,
                auth,
                wallet,
                addresses,
                args.amount_btc,
                per_wallet_target,
                per_wallet_rate,
                stop_event,
                args.progress_interval,
                counters,
                lock,
            ),
            daemon=True,
        )
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    stop_event.set()
    final_size = mempool_size(mempool_rpc, auth)
    print(f"Mempool size: {final_size} (target {args.mempool_target})")
    if final_size < args.mempool_target:
        raise RuntimeError("Mempool target not reached")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
