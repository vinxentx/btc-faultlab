import argparse, time, json, base64
from datetime import datetime, timezone
from http.client import HTTPConnection

def rpc_call(url, method, params=None, auth=None):
    proto, rest = url.split("://")
    host = rest
    conn = HTTPConnection(host)
    payload = json.dumps({"jsonrpc":"1.0","id":"txgen","method":method,"params":params or []})
    headers = {"Content-Type":"application/json"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    conn.request("POST","/",payload,headers)
    resp = conn.getresponse()
    out = json.loads(resp.read())
    conn.close()
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out["result"]

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, required=True, help="tx per second")
    ap.add_argument("--rpc", type=str, required=True, help="e.g. http://user:pass@node01:18443")
    ap.add_argument("--log", type=str, required=True, help="path to txlog.csv")
    args = ap.parse_args()

    proto, rest = args.rpc.split("://")
    cred, host = rest.split("@")
    auth = cred

    addr = rpc_call(proto + "://" + host, "getnewaddress", auth=auth)
    rpc_call(proto + "://" + host, "generatetoaddress", [101, addr], auth=auth)

    interval = 1.0/args.rate if args.rate > 0 else 0.1
    with open(args.log, "w", encoding="utf-8") as f:
        f.write("submit_ts_utc,txid\n")
        next_mine = time.time() + 10
        while True:
            dst = rpc_call(proto + "://" + host, "getnewaddress", auth=auth)
            txid = rpc_call(proto + "://" + host, "sendtoaddress", [dst, 0.0001], auth=auth)
            submit_ts = datetime.now(timezone.utc).isoformat()
            f.write(f"{submit_ts},{txid}\n"); f.flush()
            if time.time() >= next_mine:
                rpc_call(proto + "://" + host, "generatetoaddress", [1, addr], auth=auth)
                next_mine = time.time() + 10
            time.sleep(max(0, interval))
