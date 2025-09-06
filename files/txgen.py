import argparse, time, json, base64, os
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


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, required=True, help="tx per second")
    ap.add_argument("--rpc", type=str, required=True, help="e.g. http://user:pass@node01:18443")
    ap.add_argument("--log", type=str, required=True, help="path to txlog.csv")
    args = ap.parse_args()

    proto, rest = args.rpc.split("://")
    cred, host = rest.split("@")
    auth = cred

    # Ensure results dir exists and is writable
    log_dir = os.path.dirname(args.log)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    # Wait for RPC to be reachable
    wait_for_rpc(proto + "://" + host, auth=auth, timeout_s=180)

    # Wallet setup
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
    rpc_call(proto + "://" + host, "generatetoaddress", [101, addr], auth=auth, wallet=wallet)

    interval = 1.0 / args.rate if args.rate > 0 else 0.1
    with open(args.log, "w", encoding="utf-8") as f:
        f.write("submit_ts_utc,txid\n")
        next_mine = time.time() + 10
        while True:
            try:
                dst = rpc_call(proto + "://" + host, "getnewaddress", auth=auth, wallet=wallet)
                txid = rpc_call(proto + "://" + host, "sendtoaddress", [dst, 0.0001], auth=auth, wallet=wallet)
                submit_ts = datetime.now(timezone.utc).isoformat()
                f.write(f"{submit_ts},{txid}\n"); f.flush()
                print(f"Submitted transaction {txid}")
            except RuntimeError as e:
                if "Unconfirmed UTXOs" in str(e):
                    print(f"UTXO issue, mining block to confirm transactions...")
                    rpc_call(proto + "://" + host, "generatetoaddress", [1, addr], auth=auth, wallet=wallet)
                    continue
                else:
                    print(f"Transaction failed: {e}")
                    time.sleep(1)
                    continue
            
            if time.time() >= next_mine:
                print("Mining block...")
                rpc_call(proto + "://" + host, "generatetoaddress", [1, addr], auth=auth, wallet=wallet)
                next_mine = time.time() + 10
            time.sleep(max(0, interval))
