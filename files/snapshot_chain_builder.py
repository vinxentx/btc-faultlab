#!/usr/bin/env python3
import argparse
import base64
import json
import subprocess
import time
from http.client import HTTPConnection
from pathlib import Path
from typing import Optional, List, Dict, Any
import heapq


JSONRPC_HEADERS = {"Content-Type": "application/json"}


def encode_basic_auth(credential: str) -> str:
    return base64.b64encode(credential.encode("utf-8")).decode("ascii")


def rpc_call(rpc_url: str, method: str, params=None, *, auth: Optional[str], timeout: int = 30):
    proto, rest = rpc_url.split("://", 1)
    if proto != "http":
        raise ValueError("Only http RPC endpoints are supported")
    conn = HTTPConnection(rest, timeout=timeout)
    payload = json.dumps({
        "jsonrpc": "1.0",
        "id": "snapshot",
        "method": method,
        "params": params or []
    })
    headers = dict(JSONRPC_HEADERS)
    if auth:
        headers["Authorization"] = "Basic " + encode_basic_auth(auth)
    try:
        conn.request("POST", "/", payload, headers)
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    data = json.loads(body)
    if data.get("error"):
        raise RuntimeError(data["error"])
    return data["result"]


def load_mining_address(state_dir: Path) -> Optional[str]:
    path = state_dir / "mining_targets.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data.get("mining_address")


def wait_for_mempool_size(rpc_url: str, auth: str, target: int, timeout_s: int, poll_s: float) -> int:
    deadline = time.time() + timeout_s
    last_size = 0
    while time.time() < deadline:
        info = rpc_call(rpc_url, "getmempoolinfo", auth=auth, timeout=10)
        size = int(info.get("size", 0))
        last_size = size
        if size >= target:
            return size
        time.sleep(poll_s)
    raise RuntimeError(f"Timed out waiting for mempool size {target} (last {last_size})")


def topo_sort_mempool(entries: Dict[str, Any]) -> List[str]:
    indeg: Dict[str, int] = {}
    children: Dict[str, List[str]] = {}
    for txid, entry in entries.items():
        deps = [dep for dep in entry.get("depends", []) if dep in entries]
        indeg[txid] = len(deps)
        for dep in deps:
            children.setdefault(dep, []).append(txid)
    heap = [txid for txid, deg in indeg.items() if deg == 0]
    heapq.heapify(heap)
    ordered: List[str] = []
    while heap:
        txid = heapq.heappop(heap)
        ordered.append(txid)
        for child in children.get(txid, []):
            indeg[child] -= 1
            if indeg[child] == 0:
                heapq.heappush(heap, child)
    if len(ordered) != len(entries):
        raise RuntimeError("Mempool contains cycles or missing dependencies")
    return ordered


def fetch_mempool_txids(rpc_url: str, auth: str, *, exclude_deps: bool) -> List[str]:
    payload: Dict[str, Any] = rpc_call(rpc_url, "getrawmempool", [True], auth=auth, timeout=30)
    if exclude_deps:
        return [txid for txid, entry in payload.items() if not entry.get("depends")]
    return topo_sort_mempool(payload)


def stop_containers(containers: List[str]) -> None:
    if not containers:
        return
    cmd = ["docker", "stop"] + containers
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to stop containers: {result.stderr.strip()}")


def wait_for_sync(auth: str, node_count: int, rpc_base: int, rpc_step: int,
                  target_height: int, timeout_s: int, poll_s: float) -> None:
    deadline = time.time() + timeout_s
    pending = set(range(1, node_count + 1))
    while pending:
        for idx in list(pending):
            port = rpc_base + (idx - 1) * rpc_step
            rpc_url = f"http://127.0.0.1:{port}"
            try:
                height = int(rpc_call(rpc_url, "getblockcount", auth=auth, timeout=5))
            except Exception:
                continue
            if height >= target_height:
                pending.remove(idx)
        if not pending:
            return
        if time.time() > deadline:
            raise RuntimeError(f"Timed out waiting for nodes to reach height {target_height}. Pending: {sorted(pending)}")
        time.sleep(poll_s)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a filled-chain snapshot by mining fixed blocks.")
    parser.add_argument("--rpc-user", required=True)
    parser.add_argument("--rpc-pass", required=True)
    parser.add_argument("--miner-host", default="127.0.0.1")
    parser.add_argument("--miner-port", type=int, default=20443)
    parser.add_argument("--blocks", type=int, default=600)
    parser.add_argument("--tx-target", type=int, default=120)
    parser.add_argument("--mempool-timeout", type=int, default=120)
    parser.add_argument("--poll-interval", type=float, default=0.5)
    parser.add_argument("--prefill-mempool", action="store_true")
    parser.add_argument("--prefill-timeout", type=int, default=7200)
    parser.add_argument("--mempool-target", type=int, default=0)
    parser.add_argument("--stop-txgen", action="store_true")
    parser.add_argument("--txgen-containers", default="")
    parser.add_argument("--use-generateblock", action="store_true")
    parser.add_argument("--allow-dependent-txs", action="store_true")
    parser.add_argument("--state-dir", default="/state")
    parser.add_argument("--node-count", type=int, default=32)
    parser.add_argument("--node-rpc-base", type=int, default=20443)
    parser.add_argument("--node-rpc-step", type=int, default=4)
    parser.add_argument("--sync-timeout", type=int, default=1200)
    parser.add_argument("--sync-poll", type=float, default=2.0)
    args = parser.parse_args()

    auth = f"{args.rpc_user}:{args.rpc_pass}"
    miner_url = f"http://{args.miner_host}:{args.miner_port}"
    state_dir = Path(args.state_dir)

    mining_address = load_mining_address(state_dir)
    if not mining_address:
        mining_address = rpc_call(miner_url, "getnewaddress", auth=auth, timeout=30)

    start_height = int(rpc_call(miner_url, "getblockcount", auth=auth, timeout=10))
    target_height = start_height + args.blocks
    mempool_target = args.mempool_target or (args.blocks * args.tx_target)

    if args.prefill_mempool:
        print(f"Prefilling mempool to {mempool_target} transactions...")
        size = wait_for_mempool_size(
            miner_url,
            auth,
            mempool_target,
            args.prefill_timeout,
            args.poll_interval
        )
        print(f"Mempool target reached: {size}")

    if args.stop_txgen:
        containers = [c.strip() for c in args.txgen_containers.split(",") if c.strip()]
        if not containers:
            raise RuntimeError("stop-txgen requested but no txgen containers provided")
        stop_containers(containers)

    if args.use_generateblock:
        txids = fetch_mempool_txids(miner_url, auth, exclude_deps=not args.allow_dependent_txs)
        needed = args.blocks * args.tx_target
        if len(txids) < needed:
            raise RuntimeError(f"Not enough mempool txs for mining: need {needed}, have {len(txids)}")
        for idx in range(1, args.blocks + 1):
            offset = (idx - 1) * args.tx_target
            chunk = txids[offset:offset + args.tx_target]
            if len(chunk) < args.tx_target:
                raise RuntimeError(f"Not enough txs for block {idx}: {len(chunk)} available")
            rpc_call(miner_url, "generateblock", [mining_address, chunk], auth=auth, timeout=60)
            if idx % 25 == 0 or idx == args.blocks:
                height = int(rpc_call(miner_url, "getblockcount", auth=auth, timeout=10))
                print(f"Mined {idx}/{args.blocks} blocks (height {height})")
    else:
        for idx in range(1, args.blocks + 1):
            mempool_size = wait_for_mempool_size(
                miner_url,
                auth,
                args.tx_target,
                args.mempool_timeout,
                args.poll_interval
            )
            if mempool_size < args.tx_target:
                print(f"Warning: mempool size {mempool_size} below target {args.tx_target}, mining anyway.")
            rpc_call(miner_url, "generatetoaddress", [1, mining_address], auth=auth, timeout=30)
            if idx % 25 == 0 or idx == args.blocks:
                height = int(rpc_call(miner_url, "getblockcount", auth=auth, timeout=10))
                print(f"Mined {idx}/{args.blocks} blocks (height {height})")

    print(f"Waiting for all nodes to reach height {target_height}...")
    wait_for_sync(auth, args.node_count, args.node_rpc_base, args.node_rpc_step,
                  target_height, args.sync_timeout, args.sync_poll)
    print("All nodes synced.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
