import argparse, time, json, base64, zmq, csv
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path

def rpc_call(url, method, params=None):
    proto, rest = url.split("://")
    host = rest.split("@")[1] if "@" in rest else rest
    conn = HTTPConnection(host)
    payload = json.dumps({"jsonrpc":"1.0","id":"collector","method":method,"params":params or []})
    headers = {"Content-Type":"application/json"}
    if "@" in rest:
        cred = rest.split("@")[0]
        headers["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode()
    conn.request("POST","/",payload,headers)
    resp = conn.getresponse()
    out = json.loads(resp.read())
    conn.close()
    if out.get("error"):
        raise RuntimeError(out["error"])
    return out["result"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rpc", required=True)
    ap.add_argument("--zmq-block", required=True)
    ap.add_argument("--zmq-tx", required=True)
    ap.add_argument("--txlog", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeout", type=int, default=10)
    args = ap.parse_args()

    sub_ts = {}
    with open(args.txlog, newline='', encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            sub_ts[row["txid"]] = row["submit_ts_utc"]

    ctx = zmq.Context.instance()
    s_block = ctx.socket(zmq.SUB); s_block.connect(args.zmq_block); s_block.setsockopt(zmq.SUBSCRIBE, b"hashblock")
    s_tx    = ctx.socket(zmq.SUB); s_tx.connect(args.zmq_tx);     s_tx.setsockopt(zmq.SUBSCRIBE, b"hashtx")

    last_block = time.time()
    confirmed = []
    poller = zmq.Poller(); poller.register(s_block, zmq.POLLIN); poller.register(s_tx, zmq.POLLIN)

    while True:
        socks = dict(poller.poll(500))
        now = time.time()
        if s_block in socks and socks[s_block] == zmq.POLLIN:
            _topic, body = s_block.recv_multipart()
            bh = body.hex()
            blk = rpc_call(args.rpc, "getblock", [bh, 2])
            block_ts = datetime.fromtimestamp(blk["time"], tz=timezone.utc).isoformat()
            for tx in blk["tx"]:
                txid = tx["txid"]
                if txid in sub_ts:
                    submit_iso = sub_ts[txid]
                    submit_dt = datetime.fromisoformat(submit_iso.replace("Z","+00:00"))
                    lat = (datetime.fromtimestamp(blk["time"], tz=timezone.utc) - submit_dt).total_seconds()
                    confirmed.append((txid, submit_iso, block_ts, bh, f"{lat:.3f}"))
            last_block = now
        if now - last_block > args.timeout:
            break

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline='', encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["txid","submit_ts_utc","block_ts_utc","blockhash","conf_latency_s"])
        w.writerows(confirmed)

if __name__ == "__main__":
    main()
