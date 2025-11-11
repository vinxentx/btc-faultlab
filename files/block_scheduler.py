import argparse
import base64
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from http.client import HTTPConnection
from typing import Iterable, List, Optional, Tuple


JSONRPC_HEADERS = {"Content-Type": "application/json"}


def encode_basic_auth(credential: str) -> str:
    return base64.b64encode(credential.encode("utf-8")).decode("ascii")


def rpc_timeout_for(node_count: int) -> int:
    if node_count >= 128:
        return 20
    if node_count >= 64:
        return 15
    return 10


def rpc_call(rpc_url: str, method: str, params=None, *, auth: Optional[str], timeout: int = 10):
    proto, rest = rpc_url.split("://", 1)
    if proto != "http":
        raise ValueError("Only http RPC endpoints are supported")
    conn = HTTPConnection(rest, timeout=timeout)
    payload = json.dumps({
        "jsonrpc": "1.0",
        "id": "scheduler",
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


def decode_rpc_error(err: RuntimeError) -> Tuple[Optional[int], str]:
    if not err.args:
        return None, ""
    payload = err.args[0]
    if isinstance(payload, dict):
        return payload.get("code"), payload.get("message", "")
    return None, str(payload)


def wait_for_rpc(rpc_url: str, auth: str, timeout_s: int = 120) -> None:
    deadline = time.time() + timeout_s
    last_err: Optional[Exception] = None
    while time.time() < deadline:
        try:
            rpc_call(rpc_url, "getblockcount", auth=auth, timeout=5)
            return
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            time.sleep(2.0)
    raise RuntimeError(f"RPC {rpc_url} not reachable: {last_err}")


def compute_miners(node_count: int, percentage: float) -> List[str]:
    miner_count = max(2, int(node_count * percentage))
    return [f"node{i:02d}:18443" for i in range(1, miner_count + 1)]


def miner_cycle(miners: List[str], seed: int) -> Iterable[str]:
    order = miners[:]
    rng = random.Random(seed)
    rng.shuffle(order)
    index = 0
    while True:
        yield order[index % len(order)]
        index += 1


def load_config(path: str, default_interval: float, miners: List[str], seed: int) -> dict:
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    with open(path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
    config.setdefault("interval_s", default_interval)
    config.setdefault("miner_hosts", miners)
    config.setdefault("seed", seed)
    if "mining_address" not in config:
        raise KeyError("Config file missing 'mining_address'")
    return config


def format_ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description="Deterministic block scheduler")
    parser.add_argument("--interval", type=float, required=True,
                        help="Sollintervall zwischen Blöcken (Sekunden)")
    parser.add_argument("--node-count", type=int, required=True,
                        help="Gesamtzahl der Knoten im Netzwerk")
    parser.add_argument("--mining-percentage", type=float, required=True,
                        help="Anteil der Knoten, die minen dürfen (0-1)")
    parser.add_argument("--rpc-user", required=True, help="RPC Benutzername")
    parser.add_argument("--rpc-pass", required=True, help="RPC Passwort")
    parser.add_argument("--seed", type=int, default=1337, help="Deterministischer Seed")
    parser.add_argument("--config", default="/state/mining_targets.json",
                        help="Pfad zur Scheduler-Konfigurationsdatei")
    parser.add_argument("--grace-period", type=float, default=600.0,
                        help="Wartezeit (Sekunden) auf Konfigurationsdatei; <=0 bedeutet unendlich warten")
    args = parser.parse_args()

    if args.interval <= 0:
        raise ValueError("Interval muss > 0 sein")

    auth = f"{args.rpc_user}:{args.rpc_pass}"
    miners = compute_miners(args.node_count, args.mining_percentage)

    if args.grace_period <= 0:
        while not os.path.exists(args.config):
            print(f"{format_ts()} ⏳ warte auf Konfigurationsdatei {args.config} …")
            time.sleep(2.0)
    else:
        deadline = time.time() + args.grace_period
        while time.time() < deadline and not os.path.exists(args.config):
            print(f"{format_ts()} ⏳ warte auf Konfigurationsdatei {args.config} …")
            time.sleep(2.0)

    config = load_config(args.config, args.interval, miners, args.seed)
    mining_address = config["mining_address"]
    miner_hosts: List[str] = config["miner_hosts"]
    interval_s = float(config.get("interval_s", args.interval))
    seed = int(config.get("seed", args.seed))

    print(f"{format_ts()} ✅ Config geladen: {len(miner_hosts)} Miner, "
          f"Intervall {interval_s}s, Mining-Adresse {mining_address}")

    rpc_timeout = rpc_timeout_for(args.node_count)

    for miner in miner_hosts:
        try:
            wait_for_rpc(f"http://{miner}", auth, timeout_s=120)
            print(f"{format_ts()} 🔄 Miner {miner} erreichbar")
        except RuntimeError as err:
            print(f"{format_ts()} ⚠️ Miner {miner} nicht erreichbar: {err}")

    rotation = miner_cycle(miner_hosts, seed)
    next_tick = time.perf_counter()
    produced_blocks = 0
    fail_counts = {miner: 0 for miner in miner_hosts}

    while True:
        now = time.perf_counter()
        sleep_for = next_tick - now
        if sleep_for > 0:
            time.sleep(min(sleep_for, 0.1))
            continue
        next_tick += interval_s

        miner = next(rotation)
        rpc_url = f"http://{miner}"

        try:
            result = rpc_call(
                rpc_url,
                "generatetoaddress",
                [1, mining_address],
                auth=auth,
                timeout=rpc_timeout,
            )
            block_hash = result[0] if result else "unknown"
            produced_blocks += 1
            fail_counts[miner] = 0
            ts = format_ts()
            print(f"{ts} ⛏️  Block #{produced_blocks} auf {miner} "
                  f"(hash {block_hash[:16]}...)")
            print(f"BLOCK_EVENT,{ts},{produced_blocks},{miner},{block_hash}")
        except RuntimeError as err:
            code, message = decode_rpc_error(err)
            fail_counts[miner] = fail_counts.get(miner, 0) + 1
            print(f"{format_ts()} ❌ Miner {miner} Fehler (code={code}): {message}")
            if fail_counts[miner] >= 3:
                print(f"{format_ts()} 🔁 Warte zusätzliche 3s wegen wiederholter Fehler")
                time.sleep(3.0)
                next_tick = max(next_tick, time.perf_counter() + interval_s)
        except Exception as err:  # noqa: BLE001
            print(f"{format_ts()} ❌ Unerwarteter Fehler bei Miner {miner}: {err}")
            time.sleep(2.0)
            next_tick = max(next_tick, time.perf_counter() + interval_s / 2)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(f"{format_ts()} ⏹️ Scheduler beendet (KeyboardInterrupt)")
        sys.exit(0)

