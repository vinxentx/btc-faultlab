import argparse, json, os, math
from datetime import datetime, timezone
from collections import deque
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

def parse_events(path):
    events=[]
    if os.path.exists(path):
        with open(path,"r",encoding="utf-8") as f:
            for line in f:
                ts, evt, *rest = line.strip().split(" ")
                events.append((datetime.fromisoformat(ts.replace("Z","+00:00")), evt, " ".join(rest)))
    return events

def rolling_rate(times, window=60):
    out=[]; dq=deque()
    for t in times:
        dq.append(t)
        while (t - dq[0]).total_seconds() > window:
            dq.popleft()
        out.append((t, len(dq)/window))
    return out

def compute_availability(submit_times, confirmed_times, t1=None, t2=None):
    if not submit_times: return np.nan
    if t1 is None: t1 = min(submit_times[0], confirmed_times[0] if confirmed_times else submit_times[0])
    if t2 is None: t2 = max(submit_times[-1], confirmed_times[-1] if confirmed_times else submit_times[-1])
    sub_in=[t for t in submit_times if t>=t1 and t<=t2]
    conf_in=[t for t in confirmed_times if t<=t2]
    if len(sub_in)==0: return np.nan
    return len(conf_in)/len(sub_in)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", help="single run directory")
    ap.add_argument("--runs-dir", help="parent results directory")
    args = ap.parse_args()
    if not args.run_dir:
        runs = sorted(os.listdir(args.runs_dir or "results"), reverse=True)
        if not runs: print("No runs found"); return
        args.run_dir = os.path.join(args.runs_dir or "results", runs[0])

    run_dir=args.run_dir
    txlog=os.path.join(run_dir,"txlog.csv")
    conf =os.path.join(run_dir,"confirmations.csv")
    events=parse_events(os.path.join(run_dir,"events.log"))

    df_sub=pd.read_csv(txlog,parse_dates=["submit_ts_utc"])
    df_conf=pd.read_csv(conf,parse_dates=["submit_ts_utc","confirm_ts_utc"]) if os.path.exists(conf) else pd.DataFrame()

    conf_times=sorted(df_conf["confirm_ts_utc"].tolist()) if not df_conf.empty else []
    tps_series=rolling_rate(conf_times, 60) if conf_times else []

    if not df_conf.empty:
        cl=df_conf["latency_seconds"].astype(float)
        cl50=float(np.percentile(cl,50)); cl95=float(np.percentile(cl,95))
    else:
        cl50=cl95=float("nan")

    sub_times=sorted(df_sub["submit_ts_utc"].tolist())
    A=compute_availability(sub_times, conf_times)

    plots_dir=os.path.join(run_dir,"plots"); os.makedirs(plots_dir,exist_ok=True)

    if tps_series:
        x=[t for (t,_) in tps_series]; y=[v for (_,v) in tps_series]
        plt.figure(); plt.plot(x,y)
        for (ts,evt,_) in events:
            if evt in ("start_warmup","after_netem","end_observe"):
                plt.axvline(ts, linestyle="--")
        plt.title("Throughput (confirmed tx/s, 60s rolling)"); plt.xlabel("time (UTC)"); plt.ylabel("tx/s")
        plt.tight_layout(); plt.savefig(os.path.join(plots_dir,"tps.png")); plt.close()

    if not df_conf.empty:
        plt.figure(); plt.hist(df_conf["latency_seconds"].astype(float), bins=30)
        plt.title("Confirmation latency distribution"); plt.xlabel("seconds"); plt.ylabel("count")
        plt.tight_layout(); plt.savefig(os.path.join(plots_dir,"cl_hist.png")); plt.close()

    metrics={
        "run_dir": run_dir,
        "tx_submitted": int(len(df_sub)),
        "tx_confirmed": int(0 if df_conf.empty else len(df_conf)),
        "availability": None if (A is np.nan) else float(A),
        "cl50_s": None if math.isnan(cl50) else cl50,
        "cl95_s": None if math.isnan(cl95) else cl95
    }
    with open(os.path.join(run_dir,"metrics.json"),"w",encoding="utf-8") as f:
        json.dump(metrics,f,indent=2)
    print(json.dumps(metrics,indent=2))

if __name__=="__main__":
    main()
