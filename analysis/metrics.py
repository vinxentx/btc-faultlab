#!/usr/bin/env python3
"""
Enhanced Metrics for Thesis-Focused Analysis
Bitcoin Performance Under Omission and Crash Faults
"""

import argparse
import json
import os
import math
from pathlib import Path
import sys
import re
from datetime import datetime, timezone
from collections import deque

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from timestamp_utils import parse_unix_or_iso8601

# Ensure Matplotlib has a writable cache dir + headless backend before importing it
_default_mpl_dir = Path(
    os.environ.get("MPLCONFIGDIR", Path(__file__).resolve().parent.parent / ".mplcache")
)
_default_mpl_dir.mkdir(parents=True, exist_ok=True)
os.environ["MPLCONFIGDIR"] = str(_default_mpl_dir)
os.environ.setdefault("MPLBACKEND", "Agg")

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

UPDATE_TIP_REGEX = re.compile(
    r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\s+UpdateTip:\s+new\s+best=([a-f0-9]+)\s+height=(\d+)\s+.*?\btx=(\d+)\s+date=\'([^\']+)\''
)

def parse_iso8601(values):
    """Parse timestamps from CSV columns - supports both unix epoch and ISO8601.

    Detects numeric (unix epoch seconds) vs string (ISO8601) automatically,
    so old and new result files both work.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    if numeric.notna().all():
        return pd.to_datetime(numeric, unit="s", utc=True)
    return pd.to_datetime(values, format="ISO8601", errors="coerce")

def parse_events(path):
    """Parse experiment events from log file"""
    events = []
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    parts = line.strip().split(" ")
                    if len(parts) >= 2:
                        ts, evt = parts[0], parts[1]
                        rest = " ".join(parts[2:]) if len(parts) > 2 else ""
                        try:
                            events.append((parse_unix_or_iso8601(ts), evt, rest))
                        except ValueError:
                            continue
    return events

def get_observation_start(events):
    """
    Get the observation start timestamp from events.
    Supports both new 'start_observation' and legacy 'after_netem' event names.
    
    Returns:
        tuple: (timestamp, event_name) or (None, None) if not found
    """
    for (ts, evt, _) in events:
        if evt in ("start_observation", "after_netem"):
            return ts, evt
    return None, None

def get_netem_applied(events):
    """
    Get the netem_applied timestamp (for showing warmup data in plots).
    Falls back to start_warmup if netem_applied not available.

    Returns:
        timestamp or None
    """
    for (ts, evt, _) in events:
        if evt == "netem_applied":
            return ts
    # Fallback: use start_warmup
    for (ts, evt, _) in events:
        if evt == "start_warmup":
            return ts
    return None

def compute_network_fault_metrics(events):
    """
    Parse network fault (netem) events from events.log.

    Extracts:
    - Parameters: latency, loss, jitter, bandwidth
    - Timing: when applied, when cleared
    - Affected nodes: which nodes when fraction < 1.0

    Returns:
        dict with network fault info or None if no netem events
    """
    netem_applied_ts = None
    netem_cleared_ts = None
    params = {}
    affected_nodes = None
    fraction = 1.0

    for (ts, evt, rest) in events:
        if evt == "netem_applied":
            netem_applied_ts = ts
            # Parse parameters from rest: "loss=5% latency=100ms bw=100mbit jitter=5ms fraction=0.25 nodes=node01,node02,..."
            for part in rest.split():
                if "=" in part:
                    key, val = part.split("=", 1)
                    if key == "loss":
                        params["loss_pct"] = float(val.replace("%", ""))
                    elif key == "latency":
                        params["latency_ms"] = float(val.replace("ms", ""))
                    elif key == "jitter":
                        params["jitter_ms"] = float(val.replace("ms", ""))
                    elif key == "bw":
                        params["bandwidth_mbit"] = float(val.replace("mbit", ""))
                    elif key == "fraction":
                        fraction = float(val)
                    elif key == "nodes":
                        if val != "all":
                            affected_nodes = val.split(",")
        elif evt == "netem_cleared":
            netem_cleared_ts = ts

    if netem_applied_ts is None:
        return None

    result = {
        "applied_at": netem_applied_ts.isoformat() if hasattr(netem_applied_ts, 'isoformat') else str(netem_applied_ts),
        "parameters": params,
        "fraction": fraction
    }

    if netem_cleared_ts:
        result["cleared_at"] = netem_cleared_ts.isoformat() if hasattr(netem_cleared_ts, 'isoformat') else str(netem_cleared_ts)
        result["duration_seconds"] = (netem_cleared_ts - netem_applied_ts).total_seconds()

    if affected_nodes:
        result["affected_nodes"] = affected_nodes
        result["affected_count"] = len(affected_nodes)

    return result

def compute_event_recovery_metrics(run_dir, events):
    """Compute comprehensive recovery metrics from events.log.
    
    Parses all crash/recovery related events and computes scientifically
    accurate timing metrics:
    - crash_duration: time from crash_start to crash_complete
    - total_downtime: time from crash_start to recovery_complete
    - wall_clock_recovery_time: time from recovery_start to recovery_complete
    - restart_time: time from recovery_start to all_containers_running
    - block_catchup_time: time from all_containers_running (or recovery_start) to recovery_complete (time to catch up missed blocks)
    """
    if not events:
        return None
    
    # Parse all relevant timestamps
    timestamps = {}
    node_recovery_starts = {}
    node_recovery_ends = {} # We'll try to find individual ends if possible
    crashed_nodes = None
    
    for (ts, evt, rest) in events:
        if evt == "crash_start" and "crash_start" not in timestamps:
            timestamps["crash_start"] = ts
            if rest and "nodes=" in rest:
                crashed_nodes = rest.split("nodes=", 1)[1].strip()
        elif evt == "crash_complete" and "crash_complete" not in timestamps:
            timestamps["crash_complete"] = ts
        elif evt == "recovery_start" and "recovery_start" not in timestamps:
            timestamps["recovery_start"] = ts
        elif evt == "recovery_node_start":
            # Extract node name from rest: "node=nodeXX index=YY"
            match = re.search(r"node=([a-zA-Z0-9_]+)", rest)
            if match:
                node_name = match.group(1)
                node_recovery_starts[node_name] = ts
        elif evt == "all_containers_running" and "all_containers_running" not in timestamps:
            timestamps["all_containers_running"] = ts
        elif evt == "recovery_complete" and "recovery_complete" not in timestamps:
            timestamps["recovery_complete"] = ts
    
    # Need at least recovery_start and recovery_complete for basic metrics
    if "recovery_start" not in timestamps or "recovery_complete" not in timestamps:
        return None
    
    if timestamps["recovery_complete"] < timestamps["recovery_start"]:
        return None
    
    # Build result with all available metrics
    crashed_nodes_list = crashed_nodes.split(",") if crashed_nodes else []
    
    result = {
        "recovery_detected": True,
        "method": "events_log",
        "crashed_nodes_count": len(crashed_nodes_list),
        "crashed_nodes": crashed_nodes_list,
        "timestamps": {
            k: v.isoformat() if hasattr(v, 'isoformat') else str(v) 
            for k, v in timestamps.items()
        },
        "durations_seconds": {}
    }
    
    # Calculate durations based on available timestamps
    durations = result["durations_seconds"]
    
    # Per-node startup analysis (Granularity improvement)
    if node_recovery_starts and "recovery_start" in timestamps:
        startup_delays = [
            (ts - timestamps["recovery_start"]).total_seconds() 
            for ts in node_recovery_starts.values()
        ]
        if startup_delays:
            result["node_startup_stats"] = {
                "mean_delay": float(np.mean(startup_delays)),
                "median_delay": float(np.median(startup_delays)),
                "max_delay": float(np.max(startup_delays)),
                "min_delay": float(np.min(startup_delays)),
                "count": len(startup_delays)
            }

    # crash_injection_time: crash_start → crash_complete (time to stop all nodes)
    if "crash_start" in timestamps and "crash_complete" in timestamps:
        durations["crash_injection_time"] = (timestamps["crash_complete"] - timestamps["crash_start"]).total_seconds()

    # node_downtime: crash_start → recovery_start (actual time nodes are down = crash_duration_s config)
    if "crash_start" in timestamps and "recovery_start" in timestamps:
        durations["node_downtime"] = (timestamps["recovery_start"] - timestamps["crash_start"]).total_seconds()

    # total_downtime: crash_start → recovery_complete (most important metric!)
    if "crash_start" in timestamps:
        durations["total_downtime"] = (timestamps["recovery_complete"] - timestamps["crash_start"]).total_seconds()
    
    # wall_clock_recovery_time: recovery_start → recovery_complete
    if "recovery_start" in timestamps:
        durations["wall_clock_recovery_time"] = (timestamps["recovery_complete"] - timestamps["recovery_start"]).total_seconds()
    
    # restart_time: recovery_start → all_containers_running
    if "all_containers_running" in timestamps:
        durations["restart_time"] = (timestamps["all_containers_running"] - timestamps["recovery_start"]).total_seconds()
    
    # block_catchup_time: all_containers_running → recovery_complete (or recovery_start if all_containers_running not available)
    # This is the time nodes need to catch up on blocks missed during their downtime
    if "all_containers_running" in timestamps:
        durations["block_catchup_time"] = (timestamps["recovery_complete"] - timestamps["all_containers_running"]).total_seconds()
    else:
        # Fallback: use recovery_start (less accurate but backwards compatible)
        durations["block_catchup_time"] = (timestamps["recovery_complete"] - timestamps["recovery_start"]).total_seconds()
    
    return result

def compute_latency_comparison(df_conf, events):
    """
    Compare latency before crash, during crash, and after recovery.

    Scientific use: Proves whether the system truly recovered to baseline performance
    or if there's residual degradation. Also shows impact during the fault.

    Returns:
        dict with pre_crash_median, during_crash_median, post_recovery_median, degradation_percent
        or None if no crash occurred
    """
    if df_conf.empty or not events:
        return None

    # Find crash_start and recovery_complete timestamps
    crash_start = None
    recovery_complete = None

    for (ts, evt, _) in events:
        if evt == "crash_start" and crash_start is None:
            crash_start = ts
        elif evt == "recovery_complete" and recovery_complete is None:
            recovery_complete = ts

    if crash_start is None or recovery_complete is None:
        return None  # No crash in this run

    # Ensure timestamps are timezone-aware for comparison
    if hasattr(crash_start, 'tzinfo') and crash_start.tzinfo is None:
        crash_start = crash_start.replace(tzinfo=timezone.utc)
    if hasattr(recovery_complete, 'tzinfo') and recovery_complete.tzinfo is None:
        recovery_complete = recovery_complete.replace(tzinfo=timezone.utc)

    # Split confirmations into pre-crash, during-crash, and post-recovery
    df_pre = df_conf[df_conf["confirm_ts_utc"] < crash_start].copy()
    df_during = df_conf[
        (df_conf["confirm_ts_utc"] >= crash_start) &
        (df_conf["confirm_ts_utc"] <= recovery_complete)
    ].copy()
    df_post = df_conf[df_conf["confirm_ts_utc"] > recovery_complete].copy()

    if df_pre.empty or df_post.empty:
        return None

    pre_median = float(df_pre["latency_seconds"].median())
    post_median = float(df_post["latency_seconds"].median())

    # During crash may be empty if crash duration is short
    during_median = float(df_during["latency_seconds"].median()) if not df_during.empty else None

    # Calculate degradation (positive = worse after recovery)
    if pre_median > 0:
        degradation_pct = ((post_median - pre_median) / pre_median) * 100
        during_degradation_pct = ((during_median - pre_median) / pre_median) * 100 if during_median else None
    else:
        degradation_pct = 0.0
        during_degradation_pct = None

    result = {
        "pre_crash_median": round(pre_median, 3),
        "post_recovery_median": round(post_median, 3),
        "degradation_percent": round(degradation_pct, 1),
        "pre_crash_samples": len(df_pre),
        "post_recovery_samples": len(df_post)
    }

    # Add during_crash stats if available
    if during_median is not None:
        result["during_crash_median"] = round(during_median, 3)
        result["during_crash_samples"] = len(df_during)
        if during_degradation_pct is not None:
            result["during_crash_degradation_percent"] = round(during_degradation_pct, 1)

    return result

def compute_block_interval_stats(run_dir, events):
    """
    Compute statistics about block intervals during the experiment.
    
    Scientific use: Validates that the block scheduler maintained the configured
    interval (e.g., 12s) and shows variance in block production.
    
    Returns:
        dict with mean, median, min, max intervals and block count
    """
    mining_file = os.path.join(run_dir, "mining.csv")
    if not os.path.exists(mining_file):
        return None
    
    try:
        df_mining = pd.read_csv(mining_file)
    except Exception:
        return None
    
    if df_mining.empty or "timestamp_utc" not in df_mining.columns:
        return None
    
    df_mining["timestamp_utc"] = pd.to_datetime(df_mining["timestamp_utc"])
    
    # Filter to experiment window (after_netem to end_observe)
    start_ts = None
    end_ts = None
    for (ts, evt, _) in events:
        if evt in ("start_observation", "after_netem") and start_ts is None:
            start_ts = ts
        elif evt == "end_observe" and end_ts is None:
            end_ts = ts
    
    if start_ts is not None:
        if start_ts.tzinfo is None:
            start_ts = start_ts.replace(tzinfo=timezone.utc)
        if df_mining["timestamp_utc"].dt.tz is None:
            df_mining["timestamp_utc"] = df_mining["timestamp_utc"].dt.tz_localize('UTC')
        df_mining = df_mining[df_mining["timestamp_utc"] >= start_ts]
    
    if end_ts is not None:
        if end_ts.tzinfo is None:
            end_ts = end_ts.replace(tzinfo=timezone.utc)
        df_mining = df_mining[df_mining["timestamp_utc"] <= end_ts]
    
    if len(df_mining) < 2:
        return None
    
    # Sort by timestamp and calculate intervals
    df_mining = df_mining.sort_values("timestamp_utc")
    timestamps = df_mining["timestamp_utc"].tolist()
    
    intervals = []
    for i in range(1, len(timestamps)):
        delta = (timestamps[i] - timestamps[i-1]).total_seconds()
        intervals.append(delta)
    
    if not intervals:
        return None
    
    arr = np.array(intervals)
    
    return {
        "blocks_mined": len(df_mining),
        "mean_seconds": round(float(np.mean(arr)), 2),
        "median_seconds": round(float(np.median(arr)), 2),
        "std_seconds": round(float(np.std(arr)), 2),
        "min_seconds": round(float(np.min(arr)), 2),
        "max_seconds": round(float(np.max(arr)), 2),
        "p95_seconds": round(float(np.percentile(arr, 95)), 2) if len(arr) > 1 else round(float(arr[0]), 2)
    }

def compute_per_phase_throughput(df_conf_filtered, df_sub_filtered, events, confirmation_buffer_s=180):
    """
    Compute throughput separately for pre-crash, during-crash, and post-recovery phases.

    IMPORTANT: TXs are assigned to phases by SUBMISSION time, not confirmation time.
    This prevents cross-phase contamination from confirmation lag.

    Scientific use: Shows exactly how throughput degrades during crash and recovers after.
    Critical for thesis statements about system resilience.

    For each phase, reports:
    - submitted_count: TXs submitted during this phase
    - confirmed_count: TXs submitted during this phase that got confirmed
    - confirmation_rate: confirmed/submitted (availability metric)
    - mean_confirmation_latency_seconds: avg confirmation delay for TXs in this phase
    - throughput_tx_per_s: submission rate (submitted_count / duration)

    Args:
        confirmation_buffer_s: Buffer before end_observe to ensure TXs have time to confirm.
                               TXs submitted after (end_observe - buffer) are excluded.

    Returns:
        dict with throughput for each phase, or None if no crash events
    """
    if df_conf_filtered.empty or not events:
        return None

    # Find phase boundaries from events
    crash_start = None
    recovery_complete = None
    baseline_start = None  # netem_applied or start_warmup (for pre_crash baseline)
    observation_start = None
    end_observe = None

    start_warmup_ts = None
    netem_applied_ts = None
    for (ts, evt, _) in events:
        if evt == "start_warmup":
            start_warmup_ts = ts
        elif evt == "netem_applied":
            netem_applied_ts = ts
        elif evt in ("start_observation", "after_netem") and observation_start is None:
            observation_start = ts
        elif evt == "crash_start" and crash_start is None:
            crash_start = ts
        elif evt == "recovery_complete" and recovery_complete is None:
            recovery_complete = ts
        elif evt == "end_observe" and end_observe is None:
            end_observe = ts

    # Use netem_applied as baseline (network in stable degraded state)
    # Fall back to start_warmup if netem not found
    baseline_start = netem_applied_ts or start_warmup_ts

    # Need crash events for phase separation
    if crash_start is None or recovery_complete is None:
        return None

    # Make timestamps timezone-aware
    def ensure_tz(ts):
        if ts is not None and hasattr(ts, 'tzinfo') and ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    crash_start = ensure_tz(crash_start)
    recovery_complete = ensure_tz(recovery_complete)
    baseline_start = ensure_tz(baseline_start)
    observation_start = ensure_tz(observation_start)
    end_observe = ensure_tz(end_observe)

    # Ensure df timestamps are timezone-aware
    df_conf = df_conf_filtered.copy()
    if "submit_ts_utc" in df_conf.columns:
        if df_conf["submit_ts_utc"].dt.tz is None:
            df_conf["submit_ts_utc"] = df_conf["submit_ts_utc"].dt.tz_localize('UTC')
    if df_conf["confirm_ts_utc"].dt.tz is None:
        df_conf["confirm_ts_utc"] = df_conf["confirm_ts_utc"].dt.tz_localize('UTC')

    # Also prepare submission data for counting submitted TXs
    df_sub = df_sub_filtered.copy() if not df_sub_filtered.empty else pd.DataFrame()
    if not df_sub.empty and "submit_ts_utc" in df_sub.columns:
        df_sub["submit_ts_utc"] = parse_iso8601(df_sub["submit_ts_utc"])
        if df_sub["submit_ts_utc"].dt.tz is None:
            df_sub["submit_ts_utc"] = df_sub["submit_ts_utc"].dt.tz_localize('UTC')

    def compute_phase_metrics(phase_start, phase_end, phase_name):
        """Compute metrics for a single phase.

        Two perspectives on throughput:
        1. Submission-based: TXs submitted during phase (for availability analysis)
        2. Confirmation-based: TXs confirmed during phase (for true throughput)
        """
        if phase_start is None or phase_end is None:
            return None

        duration = (phase_end - phase_start).total_seconds()
        if duration <= 0:
            return None

        # === SUBMISSION-BASED METRICS ===
        # Count TXs SUBMITTED during this phase
        if not df_sub.empty and "submit_ts_utc" in df_sub.columns:
            submitted = df_sub[
                (df_sub["submit_ts_utc"] >= phase_start) &
                (df_sub["submit_ts_utc"] < phase_end)
            ]
            submitted_count = len(submitted)
        else:
            submitted_count = 0

        # Count TXs that were SUBMITTED during this phase AND got confirmed
        # (confirmation may happen in a later phase - this is for availability calculation)
        if "submit_ts_utc" in df_conf.columns:
            confirmed_of_submitted = df_conf[
                (df_conf["submit_ts_utc"] >= phase_start) &
                (df_conf["submit_ts_utc"] < phase_end)
            ]
        else:
            confirmed_of_submitted = pd.DataFrame()

        confirmed_count = len(confirmed_of_submitted)

        # === CONFIRMATION-BASED METRICS (TRUE THROUGHPUT) ===
        # Count TXs CONFIRMED during this phase (regardless of when submitted)
        # This is the actual processing rate during the phase - no bleeding between phases
        confirmations_in_phase = df_conf[
            (df_conf["confirm_ts_utc"] >= phase_start) &
            (df_conf["confirm_ts_utc"] < phase_end)
        ]
        confirmations_in_phase_count = len(confirmations_in_phase)

        result = {
            # Submission metrics
            "submitted_count": submitted_count,
            "confirmed_count": confirmed_count,  # of TXs submitted in this phase
            "duration_seconds": round(duration, 1),
            "submission_rate_tx_per_s": round(submitted_count / duration, 3) if submitted_count > 0 else 0.0,

            # True throughput: confirmations that actually happened during this phase
            "confirmations_in_phase": confirmations_in_phase_count,
            "confirmation_throughput_tx_per_s": round(confirmations_in_phase_count / duration, 3)
        }

        # Calculate confirmation rate (availability for TXs submitted in this phase)
        if submitted_count > 0:
            result["confirmation_rate"] = round(confirmed_count / submitted_count, 4)

        # Calculate mean confirmation latency for TXs CONFIRMED in this phase
        if "latency_seconds" in confirmations_in_phase.columns and len(confirmations_in_phase) > 0:
            result["mean_confirmation_latency_seconds"] = round(confirmations_in_phase["latency_seconds"].mean(), 2)
            result["median_confirmation_latency_seconds"] = round(confirmations_in_phase["latency_seconds"].median(), 2)

        return result

    result = {}

    # Phase 1: Pre-crash baseline (netem_applied → crash_start)
    pre_crash = compute_phase_metrics(baseline_start, crash_start, "pre_crash")
    if pre_crash:
        result["pre_crash"] = pre_crash

    # Phase 2: During crash (crash_start → recovery_complete)
    during_crash = compute_phase_metrics(crash_start, recovery_complete, "during_crash")
    if during_crash:
        result["during_crash"] = during_crash

    # Phase 3: Post-recovery (recovery_complete → end_observe - buffer)
    # Apply confirmation buffer to ensure TXs have time to confirm
    post_recovery_end = end_observe
    if end_observe is not None and confirmation_buffer_s > 0:
        post_recovery_end = end_observe - pd.Timedelta(seconds=confirmation_buffer_s)
    post_recovery = compute_phase_metrics(recovery_complete, post_recovery_end, "post_recovery")
    if post_recovery:
        result["post_recovery"] = post_recovery

    # Calculate degradation percentages using TRUE throughput (confirmations per second)
    if "pre_crash" in result and "during_crash" in result:
        pre_tps = result["pre_crash"]["confirmation_throughput_tx_per_s"]
        during_tps = result["during_crash"]["confirmation_throughput_tx_per_s"]
        if pre_tps > 0:
            result["throughput_degradation_percent"] = round(((pre_tps - during_tps) / pre_tps) * 100, 1)

    if "pre_crash" in result and "post_recovery" in result:
        pre_tps = result["pre_crash"]["confirmation_throughput_tx_per_s"]
        post_tps = result["post_recovery"]["confirmation_throughput_tx_per_s"]
        if pre_tps > 0:
            recovery_diff = ((post_tps - pre_tps) / pre_tps) * 100
            result["recovery_vs_baseline_percent"] = round(recovery_diff, 1)

    # Calculate confirmation rate degradation (availability of TXs submitted in each phase)
    if "pre_crash" in result and "during_crash" in result:
        pre_rate = result["pre_crash"].get("confirmation_rate", 1.0)
        during_rate = result["during_crash"].get("confirmation_rate", 1.0)
        if pre_rate and pre_rate > 0:
            result["confirmation_rate_degradation_percent"] = round(((pre_rate - during_rate) / pre_rate) * 100, 1)

    return result if result else None


def compute_reorg_metrics(run_dir, events=None, reorg_events_from_propagation=None):
    """
    Analyze blockchain reorgs and fork resolution from chaintips.json and pre-parsed log data.

    Scientific use: Measures network consensus stability under fault conditions.
    Fork resolution time shows how quickly the network converges after partitions.

    Args:
        run_dir: Path to experiment run directory
        events: Parsed events.log data
        reorg_events_from_propagation: Reorg events already detected by compute_block_propagation_metrics
                                       (avoids parsing logs twice)

    Returns:
        dict with reorg count, stale blocks, fork resolution times
    """
    result = {
        "stale_blocks": 0,
        "valid_headers_blocks": 0,
        "valid_fork_blocks": 0,
        "active_tips": 1,
        "total_tips": 1,
        "fork_details": []
    }

    # Read chaintips.json (end-of-experiment snapshot)
    chaintips_path = os.path.join(run_dir, "chaintips.json")
    if os.path.exists(chaintips_path):
        try:
            with open(chaintips_path, 'r') as f:
                tips = json.load(f)

            result["total_tips"] = len(tips)

            for tip in tips:
                status = tip.get("status", "")
                branchlen = tip.get("branchlen", 0)
                height = tip.get("height", 0)

                if status == "active":
                    result["active_tips"] = 1
                    result["active_height"] = height
                elif status == "valid-headers":
                    result["valid_headers_blocks"] += 1
                    result["fork_details"].append({
                        "type": "valid-headers",
                        "height": height,
                        "branchlen": branchlen,
                        "blocks_behind": result.get("active_height", 0) - height
                    })
                elif status == "valid-fork":
                    result["valid_fork_blocks"] += 1
                    result["fork_details"].append({
                        "type": "valid-fork",
                        "height": height,
                        "branchlen": branchlen,
                        "blocks_behind": result.get("active_height", 0) - height
                    })
                elif status == "headers-only":
                    result["stale_blocks"] += 1

            # Total orphaned/stale blocks
            result["orphaned_blocks_total"] = (
                result["stale_blocks"] +
                result["valid_headers_blocks"] +
                result["valid_fork_blocks"]
            )

        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️  Could not parse chaintips.json: {e}")

    # Use reorg events from block_propagation_metrics (already parsed from ALL node logs)
    # This is much more efficient than parsing logs again
    if reorg_events_from_propagation is not None:
        reorg_events = reorg_events_from_propagation
    else:
        reorg_events = []

    result["reorg_count"] = len(reorg_events)

    # Count unique heights that had reorgs (more meaningful than raw event count)
    # NOTE: This is the authoritative fork count from debug.log reorg events.
    # orphaned_blocks_total from getchaintips may undercount because nodes prune
    # old fork data from memory. Use reorg_heights_affected (= forks_detected) for
    # accurate fork counting in analysis.
    if reorg_events:
        unique_reorg_heights = set(e.get("height") for e in reorg_events)
        result["reorg_heights_affected"] = len(unique_reorg_heights)
        # Alias for clarity - this is the true fork count from actual reorg events
        result["forks_detected"] = len(unique_reorg_heights)

        # Count how many nodes saw each reorg and calculate per-fork resolution times
        events_per_height = {}
        for e in reorg_events:
            h = e.get("height")
            if h not in events_per_height:
                events_per_height[h] = []
            events_per_height[h].append(e)

        nodes_per_height = {h: set(e.get("node") for e in evts) for h, evts in events_per_height.items()}
        result["reorg_network_wide"] = sum(1 for nodes in nodes_per_height.values() if len(nodes) > 1)

        # Calculate reorg propagation times (time from first to last node seeing reorg at each height)
        reorg_propagation = []
        for height, evts in events_per_height.items():
            timestamps = []
            for e in evts:
                ts_str = e.get("timestamp")
                if ts_str:
                    try:
                        ts = parse_unix_or_iso8601(ts_str)
                        timestamps.append(ts)
                    except ValueError:
                        pass

            if len(timestamps) >= 2:
                timestamps.sort()
                propagation_time = (timestamps[-1] - timestamps[0]).total_seconds()
                reorg_propagation.append({
                    "height": height,
                    "nodes_affected": len(nodes_per_height[height]),
                    "first_reorg": timestamps[0].isoformat(),
                    "last_reorg": timestamps[-1].isoformat(),
                    "propagation_seconds": round(propagation_time, 2)
                })
            elif len(timestamps) == 1:
                reorg_propagation.append({
                    "height": height,
                    "nodes_affected": len(nodes_per_height[height]),
                    "first_reorg": timestamps[0].isoformat(),
                    "propagation_seconds": 0  # Single node
                })

        # Sort by height descending (most recent first)
        reorg_propagation.sort(key=lambda x: x["height"], reverse=True)
        result["reorg_propagation"] = reorg_propagation

        # Summary stats
        if reorg_propagation:
            propagation_times = [r["propagation_seconds"] for r in reorg_propagation if r["propagation_seconds"] > 0]
            if propagation_times:
                result["max_reorg_propagation_seconds"] = max(propagation_times)
                result["mean_reorg_propagation_seconds"] = round(sum(propagation_times) / len(propagation_times), 2)

        # Store first 10 reorg events for debugging
        result["reorg_events"] = reorg_events[:10]

    return result


def compute_chain_split_metrics(run_dir):
    """
    Detect chain splits from UpdateTip logs across all nodes.

    A chain split occurs when multiple nodes have different block hashes at the same
    height simultaneously. This is different from reorgs:
    - Reorg: A single node switches from one chain to another
    - Chain split: Multiple nodes disagree on the current tip at the same time

    Chain splits indicate temporary consensus failures and are more severe than reorgs.
    They typically resolve when nodes receive blocks and converge back.

    Algorithm:
    1. Parse all UpdateTip events from all node logs with timestamps
    2. Build a timeline of each node's current tip (height, hash)
    3. At each event, check if nodes disagree on the tip hash for the same height
    4. Track split start, duration, and nodes involved

    Args:
        run_dir: Path to experiment run directory

    Returns:
        dict with chain split metrics:
        - total_splits: Number of distinct chain split events
        - max_duration_seconds: Longest split duration
        - max_nodes_diverged: Maximum nodes on different chains simultaneously
        - total_divergence_seconds: Total time network was in split state
        - splits: List of individual split events with details
    """
    result = {
        "total_splits": 0,
        "max_duration_seconds": 0.0,
        "max_nodes_diverged": 0,
        "total_divergence_seconds": 0.0,
        "splits": []
    }

    run_dir = Path(run_dir)
    log_files = sorted(run_dir.glob("node*.log"))

    if not log_files:
        return result

    # Collect all UpdateTip events across all nodes
    # Format: (timestamp, node, height, hash)
    all_events = []

    for log_path in log_files:
        node_name = log_path.stem
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                for line in f:
                    match = UPDATE_TIP_REGEX.search(line)
                    if match:
                        ts_str = match.group(1)
                        block_hash = match.group(2)
                        height = int(match.group(3))

                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            all_events.append((ts, node_name, height, block_hash))
                        except ValueError:
                            continue
        except FileNotFoundError:
            continue

    if not all_events:
        return result

    # Sort events chronologically
    all_events.sort(key=lambda x: x[0])

    # Track current state of each node: node -> (height, hash)
    node_state: dict[str, tuple[int, str]] = {}

    # Track active split state
    current_split = None  # None or {"start_ts": ..., "heights_involved": set(), "hashes_seen": set()}
    splits_detected = []

    def check_for_split():
        """Check if current node states represent a chain split."""
        if len(node_state) < 2:
            return [], 0

        # Group nodes by their current tip
        # For a split, we care about nodes at the SAME height but DIFFERENT hashes
        height_to_nodes = {}
        for node, (height, hash_val) in node_state.items():
            if height not in height_to_nodes:
                height_to_nodes[height] = {}
            if hash_val not in height_to_nodes[height]:
                height_to_nodes[height][hash_val] = set()
            height_to_nodes[height][hash_val].add(node)

        # Find heights where multiple hashes exist (split)
        max_diverged = 0
        split_heights = []

        for height, hash_to_nodes in height_to_nodes.items():
            if len(hash_to_nodes) > 1:
                # Multiple hashes at same height = split
                nodes_involved = sum(len(nodes) for nodes in hash_to_nodes.values())
                max_diverged = max(max_diverged, nodes_involved)
                split_heights.append({
                    "height": height,
                    "forks": {h[:16]: list(nodes) for h, nodes in hash_to_nodes.items()}
                })

        return split_heights, max_diverged

    # Process events chronologically
    prev_in_split = False

    for ts, node, height, block_hash in all_events:
        # Update node state
        node_state[node] = (height, block_hash)

        # Check for split
        split_heights, nodes_diverged = check_for_split()
        in_split = len(split_heights) > 0

        if in_split and not prev_in_split:
            # Split started
            current_split = {
                "start_ts": ts,
                "max_nodes_diverged": nodes_diverged,
                "heights_involved": set(s["height"] for s in split_heights)
            }
        elif in_split and prev_in_split:
            # Still in split - update max diverged
            if current_split:
                current_split["max_nodes_diverged"] = max(
                    current_split["max_nodes_diverged"], nodes_diverged
                )
                for s in split_heights:
                    current_split["heights_involved"].add(s["height"])
        elif not in_split and prev_in_split:
            # Split resolved
            if current_split:
                duration = (ts - current_split["start_ts"]).total_seconds()
                splits_detected.append({
                    "start_ts": current_split["start_ts"].isoformat(),
                    "end_ts": ts.isoformat(),
                    "duration_seconds": round(duration, 2),
                    "max_nodes_diverged": current_split["max_nodes_diverged"],
                    "heights_involved": sorted(current_split["heights_involved"])
                })
                current_split = None

        prev_in_split = in_split

    # Handle split that never resolved (still active at end of logs)
    if current_split:
        last_ts = all_events[-1][0]
        duration = (last_ts - current_split["start_ts"]).total_seconds()
        splits_detected.append({
            "start_ts": current_split["start_ts"].isoformat(),
            "end_ts": last_ts.isoformat() + " (ongoing)",
            "duration_seconds": round(duration, 2),
            "max_nodes_diverged": current_split["max_nodes_diverged"],
            "heights_involved": sorted(current_split["heights_involved"]),
            "unresolved": True
        })

    # Compute summary statistics
    if splits_detected:
        result["total_splits"] = len(splits_detected)
        result["max_duration_seconds"] = max(s["duration_seconds"] for s in splits_detected)
        result["max_nodes_diverged"] = max(s["max_nodes_diverged"] for s in splits_detected)
        result["total_divergence_seconds"] = sum(s["duration_seconds"] for s in splits_detected)

        # Include up to 10 most significant splits (by duration)
        splits_sorted = sorted(splits_detected, key=lambda x: x["duration_seconds"], reverse=True)
        result["splits"] = splits_sorted[:10]

    # === END-OF-EXPERIMENT CONSENSUS VERIFICATION ===
    # node_state contains each node's final tip from their last UpdateTip log entry
    # Check if all nodes converged to the same tip
    if node_state:
        # Group nodes by their final tip (height, hash)
        tip_to_nodes: dict[tuple[int, str], list[str]] = {}
        for node, (height, block_hash) in node_state.items():
            tip = (height, block_hash)
            if tip not in tip_to_nodes:
                tip_to_nodes[tip] = []
            tip_to_nodes[tip].append(node)

        result["final_state"] = {
            "nodes_checked": len(node_state),
            "consensus_reached": len(tip_to_nodes) == 1,
            "unique_tips": len(tip_to_nodes)
        }

        if len(tip_to_nodes) == 1:
            # All nodes agree
            (height, block_hash), nodes = list(tip_to_nodes.items())[0]
            result["final_state"]["final_height"] = height
            result["final_state"]["final_hash"] = block_hash[:16] + "..."
        else:
            # Nodes disagree - report the divergence
            result["final_state"]["divergent_tips"] = [
                {
                    "height": height,
                    "hash": block_hash[:16] + "...",
                    "node_count": len(nodes),
                    "nodes": nodes[:5]  # First 5 nodes as sample
                }
                for (height, block_hash), nodes in sorted(tip_to_nodes.items(), key=lambda x: -len(x[1]))
            ]

    return result


def rolling_rate(times, window=60):
    """Calculate rolling transaction rate"""
    out = []
    dq = deque()
    for t in times:
        dq.append(t)
        while dq and (t - dq[0]).total_seconds() > window:
            dq.popleft()
        out.append((t, len(dq)/window))
    return out

def binned_rate(times, bin_size=10):
    """
    Calculate transaction rate using fixed time bins.
    More accurate than rolling window - no ramp-up/cool-down artifacts.
    
    Args:
        times: List of confirmation timestamps (pd.Timestamp)
        bin_size: Bin size in seconds (default: 10)
    
    Returns:
        List of (timestamp, rate) tuples where rate is tx/s
    """
    if not times or len(times) == 0:
        return []
    
    # Convert to pandas Series for easier manipulation
    times_series = pd.Series(times)
    
    # Create bins from first to last timestamp
    start_time = times_series.min().floor('1s')
    end_time = times_series.max().ceil('1s')
    
    # Create bin edges
    bins = pd.date_range(start=start_time, end=end_time, freq=f'{bin_size}s')
    
    if len(bins) < 2:
        return []
    
    # Count confirmations per bin
    counts, _ = np.histogram(times_series, bins=bins)
    
    # Calculate rate (confirmations per second)
    rates = counts / bin_size
    
    # Return (bin_center_time, rate) tuples
    bin_centers = bins[:-1] + pd.Timedelta(seconds=bin_size/2)
    
    return list(zip(bin_centers, rates))

def extract_updatetip_blocks(run_dir, best_chain_hashes=None):
    """
    Extract block arrival timestamps from UpdateTip logs.
    
    Uses block arrival times (when node saw the block) rather than block creation times.
    Filters to only blocks in the final best chain to handle reorgs.
    
    Args:
        run_dir: Path to experiment run directory
        best_chain_hashes: Set of block hashes in the final best chain (from chaintips.json)
    
    Returns:
        DataFrame with columns: arrival_ts_utc, block_hash, height, tx_count
    """
    # Try to find a representative node log (prefer node01)
    node_logs = [
        Path(run_dir) / "node01.log",
        Path(run_dir) / "node02.log",
    ]
    
    node_log = None
    for log_path in node_logs:
        if log_path.exists():
            node_log = log_path
            break
    
    if not node_log:
        return pd.DataFrame()
    
    blocks = []
    with open(node_log, 'r', encoding='utf-8') as f:
        for line in f:
            match = UPDATE_TIP_REGEX.search(line)
            if match:
                arrival_ts_str, block_hash, height_str, tx_count_str, block_time_str = match.groups()
                
                # Filter to best chain if provided
                if best_chain_hashes and block_hash not in best_chain_hashes:
                    continue
                
                try:
                    arrival_ts = pd.to_datetime(arrival_ts_str.replace('Z', '+00:00'))
                    height = int(height_str)
                    tx_count = int(tx_count_str)
                    
                    blocks.append({
                        'arrival_ts_utc': arrival_ts,
                        'block_hash': block_hash,
                        'height': height,
                        'tx_count': tx_count,
                    })
                except (ValueError, TypeError):
                    continue
    
    if not blocks:
        return pd.DataFrame()
    
    df = pd.DataFrame(blocks)
    df = df.sort_values('arrival_ts_utc')
    return df


def get_best_chain_hashes(run_dir):
    """
    Get all block hashes in the final best chain.
    
    Queries node01 to get the full chain from genesis to tip.
    This allows us to filter out blocks from reorgs.
    
    Args:
        run_dir: Path to experiment run directory
    
    Returns:
        Set of block hashes in the best chain, or None if unavailable
    """
    import subprocess
    
    try:
        # Get the tip hash from chaintips.json
        chaintips_path = os.path.join(run_dir, "chaintips.json")
        if not os.path.exists(chaintips_path):
            return None
        
        with open(chaintips_path, 'r') as f:
            tips = json.load(f)
        
        tip_hash = None
        tip_height = None
        for tip in tips:
            if tip.get('status') == 'active':
                tip_hash = tip['hash']
                tip_height = tip['height']
                break
        
        if not tip_hash:
            return None
        
        # Query node01 to get all block hashes in the chain
        # We'll walk backwards from the tip to get all hashes
        best_chain_hashes = set()
        current_hash = tip_hash
        
        # Limit to reasonable number of blocks to avoid infinite loops
        max_blocks = tip_height + 100
        blocks_checked = 0
        
        while current_hash and blocks_checked < max_blocks:
            best_chain_hashes.add(current_hash)
            
            # Get block info to find parent
            cmd = ["docker", "exec", "node01", "bitcoin-cli", "-regtest", 
                   "getblock", current_hash]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if proc.returncode != 0:
                break
            
            try:
                block_data = json.loads(proc.stdout)
                if 'previousblockhash' in block_data:
                    current_hash = block_data['previousblockhash']
                else:
                    break  # Genesis block
            except (json.JSONDecodeError, KeyError):
                break
            
            blocks_checked += 1
        
        return best_chain_hashes if best_chain_hashes else None
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError, KeyError, Exception) as e:
        # If we can't query the node (container not running, etc.), return None
        # This will cause extract_updatetip_blocks to use all blocks (no filtering)
        return None


def updatetip_throughput(run_dir, df_conf, window_seconds=60):
    """
    Calculate throughput using UpdateTip block arrival timestamps with sliding window.
    
    This is the cleanest, least-biased throughput calculation:
    - Uses k=1 confirmations (first confirmation only)
    - Uses block arrival timestamps (from UpdateTip, when node saw the block)
    - Corrects reorgs offline against the final best chain
    - Uses a sliding time window for smooth visualization
    
    Args:
        run_dir: Path to experiment run directory
        df_conf: DataFrame with confirmations (must have 'confirm_block_hash')
        window_seconds: Size of sliding window in seconds (default: 60)
    
    Returns:
        List of (timestamp, throughput) tuples where throughput is tx/s
        Timestamp is the window center time, throughput is TX confirmed in window / window duration
    """
    # Get all block hashes in the best chain (to filter out reorgs)
    best_chain_hashes = get_best_chain_hashes(run_dir)
    
    # Extract UpdateTip blocks (with best chain filtering)
    df_blocks = extract_updatetip_blocks(run_dir, best_chain_hashes)
    if df_blocks.empty:
        return []
    
    # Get k=1 confirmations (first confirmation only)
    # Match TX to blocks by block hash
    if df_conf.empty or 'confirm_block_hash' not in df_conf.columns:
        return []
    
    # Count TX per block hash (k=1 confirmations)
    tx_per_block = df_conf.groupby('confirm_block_hash').size()
    
    # Build throughput series with sliding window
    throughput_series = []
    
    # Use block arrival times for window calculation
    min_time = df_blocks['arrival_ts_utc'].min()
    max_time = df_blocks['arrival_ts_utc'].max()
    
    # Slide window across time
    window_half = pd.Timedelta(seconds=window_seconds / 2)
    current_time = min_time + window_half
    
    while current_time <= max_time - window_half:
        window_start = current_time - window_half
        window_end = current_time + window_half
        
        # Find blocks that arrived in this window
        blocks_in_window = df_blocks[
            (df_blocks['arrival_ts_utc'] >= window_start) &
            (df_blocks['arrival_ts_utc'] < window_end)
        ]
        
        if len(blocks_in_window) > 0:
            # Count TX confirmed in blocks that arrived in this window
            tx_count = 0
            for block_hash in blocks_in_window['block_hash']:
                if block_hash in tx_per_block.index:
                    tx_count += tx_per_block[block_hash]
            
            # Throughput = TX in window / window duration
            throughput = tx_count / window_seconds
            throughput_series.append((current_time, throughput))
        
        # Slide window forward (step by 1/2 of window size for smoother, less noisy visualization)
        # This reduces the number of data points and makes the plot less "jumpy"
        current_time += pd.Timedelta(seconds=window_seconds / 2)
    
    return throughput_series


def block_based_throughput(run_dir, df_conf):
    """
    Calculate throughput using official Bitcoin network definition:
    TX per block / Block interval (block-based, not TX-based)
    
    This is the official method used by Bitcoin network analysis tools.
    Uses block hashes to exactly match TX to blocks for perfect accuracy.
    
    DEPRECATED: Prefer updatetip_throughput() which uses block arrival times
    and handles reorgs correctly.
    
    Args:
        run_dir: Path to experiment run directory
        df_conf: DataFrame with confirmations (must have 'confirm_ts_utc' and 'confirm_block_hash')
    
    Returns:
        List of (timestamp, throughput) tuples where throughput is tx/s
        Timestamp is the block timestamp, throughput is TX in that block / block interval
    """
    if df_conf.empty or 'confirm_ts_utc' not in df_conf.columns:
        return []
    
    mining_file = os.path.join(run_dir, "mining.csv")
    if not os.path.exists(mining_file):
        return []
    
    try:
        df_mining = pd.read_csv(mining_file)
        if "timestamp_utc" not in df_mining.columns:
            return []
        
        df_mining["timestamp_utc"] = pd.to_datetime(df_mining["timestamp_utc"])
        df_mining = df_mining.sort_values("timestamp_utc")
        
        df_conf["confirm_ts_utc"] = parse_iso8601(df_conf["confirm_ts_utc"])

        # Check if we have block hashes for exact matching
        has_block_hash_in_mining = "block_hash" in df_mining.columns
        has_block_hash_in_conf = "confirm_block_hash" in df_conf.columns
        use_hash_matching = has_block_hash_in_mining and has_block_hash_in_conf
        
        if use_hash_matching:
            # Perfect accuracy: Match TX to blocks by hash
            # Count TX per block by hash
            tx_per_block_hash = df_conf.groupby('confirm_block_hash').size() if has_block_hash_in_conf else pd.Series()
            
            throughput_series = []
            for idx, row in df_mining.iterrows():
                block_time = row['timestamp_utc']
                block_hash = row.get('block_hash', '')
                
                # Calculate block interval
                if idx > 0:
                    prev_time = df_mining.iloc[idx - 1]['timestamp_utc']
                    block_interval = (block_time - prev_time).total_seconds()
                else:
                    # First block: use average interval
                    if len(df_mining) > 1:
                        intervals = df_mining['timestamp_utc'].diff().dt.total_seconds().dropna()
                        block_interval = intervals.mean() if len(intervals) > 0 else 6.0
                    else:
                        block_interval = 6.0
                
                # Get TX count for this exact block hash
                if block_hash and block_hash in tx_per_block_hash.index:
                    tx_count = tx_per_block_hash[block_hash]
                else:
                    tx_count = 0
                
                if block_interval > 0:
                    throughput = tx_count / block_interval
                    throughput_series.append((block_time, throughput))
        else:
            # Fallback: Use time-based matching - assign TX to nearest block
            # This is less accurate than hash-based but works when hashes are unavailable
            throughput_series = []
            
            for idx in range(len(df_mining)):
                block_time = df_mining.iloc[idx]['timestamp_utc']
                
                # Calculate block interval
                if idx > 0:
                    prev_time = df_mining.iloc[idx - 1]['timestamp_utc']
                    block_interval = (block_time - prev_time).total_seconds()
                else:
                    # First block: use average interval
                    if len(df_mining) > 1:
                        intervals = df_mining['timestamp_utc'].diff().dt.total_seconds().dropna()
                        block_interval = intervals.mean() if len(intervals) > 0 else 6.0
                    else:
                        block_interval = 6.0
                
                # Find TX confirmed closest to this block's timestamp
                # Use a window: from midpoint of previous interval to midpoint of next interval
                if idx > 0:
                    prev_time = df_mining.iloc[idx - 1]['timestamp_utc']
                    window_start = prev_time + pd.Timedelta(seconds=(block_time - prev_time).total_seconds() / 2)
                else:
                    window_start = block_time - pd.Timedelta(seconds=block_interval / 2)
                
                if idx < len(df_mining) - 1:
                    next_time = df_mining.iloc[idx + 1]['timestamp_utc']
                    window_end = block_time + pd.Timedelta(seconds=(next_time - block_time).total_seconds() / 2)
                else:
                    window_end = block_time + pd.Timedelta(seconds=block_interval / 2)
                
                # Count TX confirmed in this block's time window
                tx_in_block = df_conf[
                    (df_conf['confirm_ts_utc'] >= window_start) &
                    (df_conf['confirm_ts_utc'] < window_end)
                ]
                tx_count = len(tx_in_block)
                
                if block_interval > 0:
                    throughput = tx_count / block_interval
                    throughput_series.append((block_time, throughput))
        
        return throughput_series
    except Exception as e:
        print(f"⚠️  Error calculating block-based throughput: {e}")
        import traceback
        traceback.print_exc()
        return []

def compute_block_propagation_metrics(run_dir, best_chain_hashes=None, events=None):
    """
    Measure block propagation delay across all node logs.
    
    Uses mining timestamps (when the block was created) and UpdateTip timestamps
    (when each node accepted the block) to derive per-node, per-block, and global
    propagation statistics. Results are persisted to CSV files for further analysis.
    
    Only considers blocks mined within the experiment window (after_netem to end_observe)
    to exclude funding/warmup phase blocks that would skew the metrics.
    
    SCIENTIFIC IMPROVEMENT (2024-12): 
    Separates propagation metrics into:
    - online_nodes: Real network propagation (nodes that were online when block was mined)
    - recovery_sync: Sync delays for crashed nodes catching up after recovery
    - combined: All samples for completeness
    
    This separation prevents recovery sync times from skewing propagation statistics.
    """
    mining_file = os.path.join(run_dir, "mining.csv")
    if not os.path.exists(mining_file):
        return None
    
    try:
        df_mining = pd.read_csv(mining_file)
    except Exception as exc:
        print(f"⚠️  Could not load mining.csv for propagation metrics: {exc}")
        return None
    
    required_cols = {"timestamp_utc", "block_hash"}
    if df_mining.empty or not required_cols.issubset(df_mining.columns):
        return None
    
    df_mining = df_mining.dropna(subset=["timestamp_utc", "block_hash"])
    if df_mining.empty:
        return None
    
    df_mining["timestamp_utc"] = pd.to_datetime(df_mining["timestamp_utc"])
    
    # Parse crash/recovery events for separating online vs recovery sync samples
    crashed_nodes_set = set()
    crash_start_ts = None
    recovery_start_ts = None
    recovery_complete_ts = None

    if events:
        for (ts, evt, rest) in events:
            if evt == "crash_start" and crash_start_ts is None:
                crash_start_ts = ts
                if rest and "nodes=" in rest:
                    nodes_str = rest.split("nodes=", 1)[1].strip()
                    crashed_nodes_set = set(nodes_str.split(","))
            elif evt == "recovery_start" and recovery_start_ts is None:
                recovery_start_ts = ts
            elif evt == "recovery_complete" and recovery_complete_ts is None:
                recovery_complete_ts = ts
                if rest and "nodes=" in rest and not crashed_nodes_set:
                    nodes_str = rest.split("nodes=", 1)[1].strip()
                    crashed_nodes_set = set(nodes_str.split(","))
    
    # Filter to experiment window (after_netem to end_observe) if events are provided
    # This excludes funding/warmup phase blocks that would skew metrics
    if events:
        after_netem_ts = None
        end_observe_ts = None
        for (ts, evt, _) in events:
            if evt in ("start_observation", "after_netem") and after_netem_ts is None:
                after_netem_ts = ts
            elif evt == "end_observe" and end_observe_ts is None:
                end_observe_ts = ts
        
        if after_netem_ts and end_observe_ts:
            # Make timestamps timezone-aware if needed
            if after_netem_ts.tzinfo is None:
                after_netem_ts = after_netem_ts.replace(tzinfo=pd.Timestamp.now(tz='UTC').tzinfo)
            if end_observe_ts.tzinfo is None:
                end_observe_ts = end_observe_ts.replace(tzinfo=pd.Timestamp.now(tz='UTC').tzinfo)
            
            # Ensure mining timestamps are timezone-aware
            if df_mining["timestamp_utc"].dt.tz is None:
                df_mining["timestamp_utc"] = df_mining["timestamp_utc"].dt.tz_localize('UTC')
            
            original_count = len(df_mining)
            df_mining = df_mining[
                (df_mining["timestamp_utc"] >= after_netem_ts) &
                (df_mining["timestamp_utc"] <= end_observe_ts)
            ]
            filtered_count = len(df_mining)
            if filtered_count < original_count:
                print(f"   📊 Block propagation: Filtered to experiment window ({filtered_count}/{original_count} blocks)")
            
            if df_mining.empty:
                print("   ⚠️  No blocks in experiment window for propagation metrics")
                return None
    
    block_times = {
        row["block_hash"]: row["timestamp_utc"]
        for _, row in df_mining.iterrows()
    }
    
    log_files = sorted(Path(run_dir).glob("node*.log"))
    if not log_files:
        return None
    
    # IMPORTANT:
    # Nodes may emit multiple UpdateTip logs for the same block hash (e.g. around reorgs,
    # re-processing, or repeated logging). If we count every line as an independent sample,
    # "propagation delay" develops heavy tails that are actually measurement artifacts.
    #
    # To measure propagation robustly we keep ONE sample per (block_hash, node) and take
    # the earliest observation (minimum delay) as "first-seen".
    #
    # Data structures:
    # - seen[(block_hash, node)] = (min_delay, mined_ts, earliest_arrival_ts)
    # - per_block_node_delay[block_hash][node] = min_delay
    # - reorg_events: list of detected reorgs (same height, different hash)
    # - height_to_hash_per_node[node][height] = first seen hash (for reorg detection)
    seen: dict[tuple[str, str], tuple[float, "pd.Timestamp", "pd.Timestamp"]] = {}
    per_block_node_delay: dict[str, dict[str, float]] = {}
    reorg_events: list[dict] = []
    height_to_hash_per_node: dict[str, dict[int, str]] = {}

    for log_path in log_files:
        node_name = log_path.stem
        height_to_hash_per_node[node_name] = {}

        try:
            with open(log_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    match = UPDATE_TIP_REGEX.search(line)
                    if not match:
                        continue

                    ts_str = match.group(1)
                    arrival_ts = pd.to_datetime(ts_str.replace("Z", "+00:00"))
                    block_hash = match.group(2)
                    height = int(match.group(3))

                    # === REORG DETECTION (combined into this loop for efficiency) ===
                    # Detect when same height appears with different hash = reorg
                    node_heights = height_to_hash_per_node[node_name]
                    if height in node_heights:
                        if node_heights[height] != block_hash:
                            reorg_events.append({
                                "node": node_name,
                                "height": height,
                                "timestamp": ts_str,
                                "old_hash": node_heights[height][:16] + "...",
                                "new_hash": block_hash[:16] + "..."
                            })
                            node_heights[height] = block_hash  # Update to new hash
                    else:
                        node_heights[height] = block_hash

                    # === BLOCK PROPAGATION (existing logic) ===
                    if best_chain_hashes and block_hash not in best_chain_hashes:
                        continue

                    mined_ts = block_times.get(block_hash)
                    if mined_ts is None:
                        continue

                    delay = (arrival_ts - mined_ts).total_seconds()
                    if math.isnan(delay):
                        continue

                    # Allow small clock skew, clamp negatives to zero
                    if delay < -2:
                        continue
                    delay = max(0.0, delay)

                    # Ignore extreme outliers (>10 minutes)
                    if delay > 600:
                        continue

                    key = (block_hash, node_name)
                    prev = seen.get(key)
                    if prev is None or delay < prev[0]:
                        # Keep the earliest observation (minimum delay)
                        seen[key] = (delay, mined_ts, arrival_ts)
                        per_block_node_delay.setdefault(block_hash, {})[node_name] = delay
        except FileNotFoundError:
            continue

    if not seen:
        return None

    # =========================================================================
    # SCIENTIFIC SEPARATION: online_nodes vs recovery_sync
    # =========================================================================
    # A sample belongs to "recovery_sync" if:
    #   - The node was in the crash list, AND
    #   - The block was mined BEFORE recovery_complete (i.e., during outage)
    # All other samples are "online_nodes" (true network propagation)
    # =========================================================================
    
    online_samples = []
    recovery_samples = []
    partition_samples = []  # For non-crashed nodes that received blocks via network heal/reorg

    # Make timestamps timezone-aware if needed
    recovery_complete_ts_aware = None
    if recovery_complete_ts is not None:
        if recovery_complete_ts.tzinfo is None:
            recovery_complete_ts_aware = recovery_complete_ts.replace(tzinfo=timezone.utc)
        else:
            recovery_complete_ts_aware = recovery_complete_ts

    recovery_start_ts_aware = None
    if recovery_start_ts is not None:
        if recovery_start_ts.tzinfo is None:
            recovery_start_ts_aware = recovery_start_ts.replace(tzinfo=timezone.utc)
        else:
            recovery_start_ts_aware = recovery_start_ts

    crash_start_ts_aware = None
    if crash_start_ts is not None:
        if crash_start_ts.tzinfo is None:
            crash_start_ts_aware = crash_start_ts.replace(tzinfo=timezone.utc)
        else:
            crash_start_ts_aware = crash_start_ts

    for (block_hash, node_name), (delay, mined_ts, arrival_ts) in seen.items():
        # Ensure timestamps are timezone-aware
        mined_ts_aware = mined_ts
        if hasattr(mined_ts, 'tzinfo') and mined_ts.tzinfo is None:
            mined_ts_aware = mined_ts.replace(tzinfo=timezone.utc)
        arrival_ts_aware = arrival_ts
        if hasattr(arrival_ts, 'tzinfo') and arrival_ts.tzinfo is None:
            arrival_ts_aware = arrival_ts.replace(tzinfo=timezone.utc)

        sample_type = "online"  # Default

        # Threshold for abnormal delay that indicates partition (vs normal network variance)
        # Normal P2P propagation is <1s; anything >10s during crash period is partition effect
        PARTITION_DELAY_THRESHOLD = 10.0  # seconds

        if crashed_nodes_set and node_name in crashed_nodes_set:
            # Node was crashed - check if block was mined during outage
            if recovery_complete_ts_aware is not None and mined_ts_aware < recovery_complete_ts_aware:
                sample_type = "recovery_sync"
        elif crash_start_ts_aware and recovery_complete_ts_aware:
            # Non-crashed node: detect partition_resync
            # A non-crashed node with abnormally high delay during crash/recovery period
            # indicates it was isolated (on wrong fork) and received blocks via reorg
            block_mined_during_outage = (crash_start_ts_aware <= mined_ts_aware < recovery_complete_ts_aware)
            abnormal_delay = (delay > PARTITION_DELAY_THRESHOLD)
            if block_mined_during_outage and abnormal_delay:
                sample_type = "partition_resync"

        if sample_type == "recovery_sync":
            recovery_samples.append((delay, mined_ts, arrival_ts, block_hash, node_name))
        elif sample_type == "partition_resync":
            partition_samples.append((delay, mined_ts, arrival_ts, block_hash, node_name))
        else:
            online_samples.append((delay, mined_ts, arrival_ts, block_hash, node_name))
    
    # Helper function to compute statistics from a list of samples
    def compute_delay_stats(samples, description=""):
        if not samples:
            return None
        delays = [s[0] for s in samples]
        arr = np.array(delays)
        return {
            "description": description,
            "total_samples": int(len(delays)),
            "mean_seconds": float(np.mean(arr)),
            "median_seconds": float(np.median(arr)),
            "p90_seconds": float(np.percentile(arr, 90)) if len(arr) > 1 else float(arr[0]),
            "p95_seconds": float(np.percentile(arr, 95)) if len(arr) > 1 else float(arr[0]),
            "p99_seconds": float(np.percentile(arr, 99)) if len(arr) > 1 else float(arr[0]),
            "max_seconds": float(np.max(arr)),
        }
    
    # Compute separated statistics
    online_stats = compute_delay_stats(
        online_samples,
        "Network propagation for nodes online at mining time"
    )
    recovery_stats = compute_delay_stats(
        recovery_samples,
        "Per-block arrival delay (arrival_time - mined_time) for crashed nodes. NOTE: This is NOT wall-clock recovery time. For post-restart recovery time, see recovery_analysis.durations_seconds.wall_clock_recovery_time"
    )
    partition_stats = compute_delay_stats(
        partition_samples,
        "Partition resync - online nodes that received blocks via reorg after network healed"
    )

    # Build per-node delays from ONLINE samples only (exclude recovery_sync AND partition_resync)
    # This ensures CSV exports reflect true P2P propagation, not crash recovery or partition effects
    per_node_delays: dict[str, list[float]] = {}
    online_sample_keys = {(bh, nn) for (delay, mts, ats, bh, nn) in online_samples}
    for (block_hash, node_name), (delay, _, _) in seen.items():
        if (block_hash, node_name) in online_sample_keys:
            per_node_delays.setdefault(node_name, []).append(delay)

    all_delays = [delay for (delay, _, _) in seen.values()]
    delays_array = np.array(all_delays)
    nodes_per_block = np.array([len(v) for v in per_block_node_delay.values()])
    total_nodes = len(per_node_delays)
    
    # Combined summary (for backwards compatibility)
    summary = {
        "total_samples": int(len(all_delays)),
        "nodes_with_data": int(total_nodes),
        "blocks_with_data": int(len(per_block_node_delay)),
        "mean_seconds": float(np.mean(delays_array)),
        "median_seconds": float(np.median(delays_array)),
        "p90_seconds": float(np.percentile(delays_array, 90)),
        "p95_seconds": float(np.percentile(delays_array, 95)),
        "p99_seconds": float(np.percentile(delays_array, 99)),
        "max_seconds": float(np.max(delays_array)),
        "mean_nodes_per_block": float(nodes_per_block.mean()) if len(nodes_per_block) > 0 else 0.0,
        "median_nodes_per_block": float(np.median(nodes_per_block)) if len(nodes_per_block) > 0 else 0.0,
        "blocks_full_coverage": int(sum(1 for count in nodes_per_block if count == total_nodes)),
    }
    
    # Count blocks during outage for recovery analysis
    blocks_during_outage = 0
    if crashed_nodes_set and crash_start_ts and recovery_complete_ts_aware:
        crash_start_ts_aware = crash_start_ts
        if crash_start_ts.tzinfo is None:
            crash_start_ts_aware = crash_start_ts.replace(tzinfo=timezone.utc)
        
        for block_hash, mined_ts in block_times.items():
            mined_ts_aware = mined_ts
            if hasattr(mined_ts, 'tzinfo') and mined_ts.tzinfo is None:
                mined_ts_aware = mined_ts.replace(tzinfo=timezone.utc)
            if crash_start_ts_aware <= mined_ts_aware < recovery_complete_ts_aware:
                blocks_during_outage += 1
    
    # Calculate per-node summary (for CSV export only, not included in JSON)
    per_node_summary = {}
    for node, delays in per_node_delays.items():
        arr = np.array(delays)
        per_node_summary[node] = {
            "samples": int(arr.size),
            "mean_seconds": float(arr.mean()),
            "median_seconds": float(np.median(arr)),
            "p95_seconds": float(np.percentile(arr, 95)) if arr.size > 1 else float(arr[0]),
            "max_seconds": float(arr.max())
        }
    
    # Build sets of recovery and partition sample keys (needed for filtering)
    recovery_sample_keys = set()
    for (delay, mined_ts, arrival_ts, block_hash, node_name) in recovery_samples:
        recovery_sample_keys.add((block_hash, node_name))

    partition_sample_keys = set()
    for (delay, mined_ts, arrival_ts, block_hash, node_name) in partition_samples:
        partition_sample_keys.add((block_hash, node_name))

    # Combined set of non-online samples (for filtering)
    non_online_keys = recovery_sample_keys | partition_sample_keys

    # Build block summaries using ONLY online samples (exclude recovery_sync AND partition_resync)
    block_summaries = []
    for block_hash, node_map in per_block_node_delay.items():
        # Filter out recovery_sync and partition_resync samples from this block
        online_delays = [
            delay for node_name, delay in node_map.items()
            if (block_hash, node_name) not in non_online_keys
        ]
        if not online_delays:
            # Block only has non-online samples - skip
            continue
        arr = np.array(online_delays)
        block_summaries.append({
            "block_hash": block_hash,
            "mined_ts_utc": block_times[block_hash].isoformat(),
            # After filtering, this is online nodes only
            "sampled_nodes": int(arr.size),
            "median_seconds": float(np.median(arr)),
            "p90_seconds": float(np.percentile(arr, 90)) if arr.size > 1 else float(arr[0]),
            "max_seconds": float(arr.max())
        })
    
    # Persist raw samples and block-level stats for offline analysis
    try:
        # Export deduped samples with sample_type classification
        def get_sample_type(block_hash, node_name):
            if (block_hash, node_name) in recovery_sample_keys:
                return "recovery_sync"
            elif (block_hash, node_name) in partition_sample_keys:
                return "partition_resync"
            else:
                return "online"

        samples = [
            {
                "block_hash": block_hash,
                "node": node_name,
                "mined_ts_utc": mined_ts.isoformat(),
                "arrival_ts_utc": arrival_ts.isoformat(),
                "delay_seconds": delay,
                "sample_type": get_sample_type(block_hash, node_name),
            }
            for (block_hash, node_name), (delay, mined_ts, arrival_ts) in seen.items()
        ]
        samples_df = pd.DataFrame(samples)
        samples_df.to_csv(os.path.join(run_dir, "block_propagation_samples.csv"), index=False)
        
        blocks_df = pd.DataFrame(block_summaries)
        blocks_df.to_csv(os.path.join(run_dir, "block_propagation_blocks.csv"), index=False)
        
        # Also export per-node summary to CSV
        per_node_df = pd.DataFrame([
            {
                "node": node,
                "samples": stats["samples"],
                "mean_seconds": stats["mean_seconds"],
                "median_seconds": stats["median_seconds"],
                "p95_seconds": stats["p95_seconds"],
                "max_seconds": stats["max_seconds"]
            }
            for node, stats in per_node_summary.items()
        ])
        per_node_df.to_csv(os.path.join(run_dir, "block_propagation_per_node.csv"), index=False)
    except Exception as exc:
        print(f"⚠️  Could not write block propagation CSVs: {exc}")
    
    # Build result with separated metrics
    result = {}

    # Add separated metrics if available
    if online_stats:
        result["online_nodes"] = online_stats

    if recovery_stats:
        result["recovery_sync"] = recovery_stats
        result["recovery_sync"]["blocks_during_outage"] = blocks_during_outage
        result["recovery_sync"]["crashed_nodes_count"] = len(crashed_nodes_set)

    if partition_stats:
        result["partition_resync"] = partition_stats
        result["partition_resync"]["description"] = "Non-crashed nodes that received blocks via reorg after network partition healed"

    # Include reorg events detected during log parsing (for use by compute_reorg_metrics)
    result["_reorg_events"] = reorg_events

    return result


def compute_availability(submit_times, confirmed_times, t1=None, t2=None):
    """Compute system availability"""
    if not submit_times:
        return np.nan
    if t1 is None:
        t1 = min(submit_times[0], confirmed_times[0] if confirmed_times else submit_times[0])
    if t2 is None:
        t2 = max(submit_times[-1], confirmed_times[-1] if confirmed_times else submit_times[-1])
    
    sub_in = [t for t in submit_times if t >= t1 and t <= t2]
    conf_in = [t for t in confirmed_times if t >= t1 and t <= t2]
    
    if len(sub_in) == 0:
        return 0.0
    
    return len(conf_in) / len(sub_in)

def detect_recovery_completion(df_conf, events, baseline_latency=None, threshold_pct=10, crash_fraction=0):
    """
    Detect when system recovery is complete after fault injection.
    
    Recovery is considered complete when confirmation latency returns to within
    threshold_pct of baseline performance.
    
    Args:
        df_conf: DataFrame with confirmation data
        events: List of (timestamp, event_type, details) tuples
        baseline_latency: Baseline median latency (if None, computed from pre-fault data)
        threshold_pct: Percentage threshold for recovery (default 10%)
        crash_fraction: Fraction of nodes that crashed (0 = baseline test)
    
    Returns:
        dict with recovery metrics or None if recovery not detected
    """
    # Skip recovery analysis for baseline tests (no crashes)
    if crash_fraction == 0:
        return None
        
    if df_conf.empty or not events:
        return None
    
    # Find fault injection time
    fault_time = None
    for (ts, evt, _) in events:
        if evt in ("start_observation", "after_netem"):
            fault_time = ts
            break
    
    if fault_time is None:
        return None
    
    # Compute baseline latency from pre-fault data
    pre_fault_data = df_conf[df_conf['confirm_ts_utc'] < fault_time]
    if baseline_latency is None:
        if not pre_fault_data.empty:
            baseline_latency = pre_fault_data['latency_seconds'].median()
        else:
            return None
    
    # Analyze post-fault recovery
    post_fault_data = df_conf[df_conf['confirm_ts_utc'] >= fault_time].copy()
    if post_fault_data.empty:
        return None
    
    # Calculate rolling median latency (window = 20 transactions)
    window_size = min(20, len(post_fault_data) // 5)
    if window_size < 5:
        return None
    
    post_fault_data['time_since_fault'] = (post_fault_data['confirm_ts_utc'] - fault_time).dt.total_seconds()
    post_fault_data['rolling_latency'] = post_fault_data['latency_seconds'].rolling(window=window_size, center=True).median()
    
    # Define recovery threshold
    recovery_threshold = baseline_latency * (1 + threshold_pct / 100)
    
    # Find when latency stabilizes below threshold
    recovery_idx = None
    for idx, row in post_fault_data.iterrows():
        if not pd.isna(row['rolling_latency']) and row['rolling_latency'] <= recovery_threshold:
            # Check if it stays below threshold for next 10 transactions
            next_data = post_fault_data.loc[idx:idx+10, 'rolling_latency']
            if not next_data.empty and (next_data <= recovery_threshold).all():
                recovery_idx = idx
                break
    
    if recovery_idx is None:
        return {
            "recovery_detected": False,
            "baseline_latency": baseline_latency,
            "recovery_threshold": recovery_threshold,
            "max_latency_observed": post_fault_data['latency_seconds'].max(),
            "final_latency": post_fault_data['latency_seconds'].iloc[-1],
            "observation_duration": post_fault_data['time_since_fault'].iloc[-1]
        }
    
    recovery_time = post_fault_data.loc[recovery_idx, 'time_since_fault']
    recovery_timestamp = post_fault_data.loc[recovery_idx, 'confirm_ts_utc']
    
    # Calculate recovery metrics
    peak_latency = post_fault_data[post_fault_data['time_since_fault'] < recovery_time]['latency_seconds'].max()
    
    return {
        "recovery_detected": True,
        "baseline_latency": baseline_latency,
        "recovery_threshold": recovery_threshold,
        "recovery_time_seconds": recovery_time,
        "recovery_timestamp": recovery_timestamp,
        "peak_latency_during_recovery": peak_latency,
        "latency_degradation_pct": ((peak_latency - baseline_latency) / baseline_latency * 100) if baseline_latency > 0 else 0,
        "final_latency": post_fault_data['latency_seconds'].iloc[-1],
        "observation_duration": post_fault_data['time_since_fault'].iloc[-1]
    }

def clean_confirmation_data(df_conf):
    """Clean and validate confirmation data"""
    if df_conf.empty:
        return df_conf
    
    # Ensure proper data types
    df_conf['submit_ts_utc'] = parse_iso8601(df_conf['submit_ts_utc'])
    df_conf['confirm_ts_utc'] = parse_iso8601(df_conf['confirm_ts_utc'])
    
    # Calculate latency if not present
    if 'latency_seconds' not in df_conf.columns:
        df_conf['latency_seconds'] = (df_conf['confirm_ts_utc'] - df_conf['submit_ts_utc']).dt.total_seconds()
    else:
        df_conf['latency_seconds'] = pd.to_numeric(df_conf['latency_seconds'], errors='coerce')
    
    # Remove invalid data
    df_conf = df_conf.dropna(subset=['submit_ts_utc', 'confirm_ts_utc', 'latency_seconds'])
    
    # Fix negative latency values (timing issues)
    df_conf['latency_seconds'] = df_conf['latency_seconds'].clip(lower=0)
    
    # Remove extreme outliers (latency > 1 hour)
    df_conf = df_conf[df_conf['latency_seconds'] <= 3600]
    
    # Sort by confirmation time
    df_conf = df_conf.sort_values('confirm_ts_utc').reset_index(drop=True)
    
    return df_conf

def create_enhanced_plots(run_dir, events, df_sub, df_conf, tps_series, avg_throughput_official=0.0):
    """Create enhanced thesis-focused plots"""
    plots_dir = os.path.join(run_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
    # SCIENTIFIC FIX: Determine experiment window for plots
    # For PLOTS: Start from netem_applied to show warmup phase under degraded conditions
    # For METRICS: We still use start_observation (handled elsewhere)
    netem_applied_ts = get_netem_applied(events)
    observation_start_ts, _ = get_observation_start(events)
    end_observe_ts = None
    for (ts, evt, _) in events:
        if evt == "end_observe" and end_observe_ts is None:
            end_observe_ts = ts
    
    # Use netem_applied for plots (includes warmup), fall back to observation_start
    plot_start_ts = netem_applied_ts or observation_start_ts
    
    # Filter data for plots (includes warmup under degraded conditions)
    df_conf_plot = df_conf.copy() if not df_conf.empty else pd.DataFrame()
    if not df_conf_plot.empty and plot_start_ts is not None:
        df_conf_plot["confirm_ts_utc"] = parse_iso8601(df_conf_plot["confirm_ts_utc"])
        df_conf_plot = df_conf_plot[df_conf_plot["confirm_ts_utc"] >= plot_start_ts]
        if end_observe_ts is not None:
            df_conf_plot = df_conf_plot[df_conf_plot["confirm_ts_utc"] <= end_observe_ts]
    
    # Load experiment configuration to customize plots
    metadata_file = os.path.join(run_dir, "metadata.yml")
    exp_config = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, 'r') as f:
            for line in f:
                if ':' in line:
                    key, val = line.strip().split(':', 1)
                    exp_config[key.strip()] = val.strip()
    
    # Determine experiment type for better labeling
    crash_frac = float(exp_config.get('crash_fraction', 0))
    loss_pct = float(exp_config.get('loss_pct', 0))
    latency_ms = float(exp_config.get('latency_ms', 0))
    
    # Define thresholds for baseline vs fault injection
    # Realistic baseline networks have some latency but no packet loss or crashes
    BASELINE_LATENCY_THRESHOLD = 100  # ms - typical internet latency
    BASELINE_LOSS_THRESHOLD = 1  # % - minimal acceptable loss
    
    if crash_frac == 0 and loss_pct <= BASELINE_LOSS_THRESHOLD and latency_ms <= BASELINE_LATENCY_THRESHOLD:
        exp_type = "Baseline (No Faults)"
        has_faults = False
    elif crash_frac > 0 and loss_pct <= BASELINE_LOSS_THRESHOLD and latency_ms <= BASELINE_LATENCY_THRESHOLD:
        exp_type = f"Crash-Only ({crash_frac*100:.0f}% Node Failures)"
        has_faults = True
    elif crash_frac == 0 and (loss_pct > BASELINE_LOSS_THRESHOLD or latency_ms > BASELINE_LATENCY_THRESHOLD):
        exp_type = f"Network-Only ({loss_pct:.0f}% Loss, {latency_ms:.0f}ms Latency)"
        has_faults = True
    else:
        exp_type = f"Combined Faults ({crash_frac*100:.0f}% Crashes + {loss_pct:.0f}% Loss + {latency_ms:.0f}ms Latency)"
        has_faults = True
    
    # Set publication-ready style
    plt.style.use('default')
    plt.rcParams.update({
            'font.size': 10,
        'axes.titlesize': 12,
        'axes.labelsize': 10,
        'xtick.labelsize': 9,
        'ytick.labelsize': 9,
        'legend.fontsize': 9,
        'figure.titlesize': 14,
        'lines.linewidth': 2,
        'grid.alpha': 0.3
    })
    
    # 1. Performance Timeline with Fault Events
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10))
    fig.suptitle(f'Bitcoin Performance Timeline - {exp_type}', fontsize=16, fontweight='bold')
    
    # SCIENTIFIC FIX: Use 30s binned throughput (smoother, less noise, only observation data)
    # This is more appropriate for scientific visualization than per-block spikes
    conf_times_plot = sorted(df_conf_plot["confirm_ts_utc"].tolist()) if not df_conf_plot.empty else []
    throughput_binned = binned_rate(conf_times_plot, bin_size=30) if conf_times_plot else []
    
    target_rate = float(exp_config.get('tx_rate', 10))
    
    if throughput_binned and len(throughput_binned) > 0:
        x = [t for (t, _) in throughput_binned]
        y = [v for (_, v) in throughput_binned]
        ax1.plot(x, y, linewidth=2, color='#2196F3', alpha=0.9, label='Throughput (30s bins)')
        ax1.fill_between(x, y, alpha=0.2, color='#2196F3')
        
        # Add target rate line
        if target_rate > 0:
            ax1.axhline(target_rate, linestyle='--', color='#4CAF50', linewidth=1.5, 
                       alpha=0.7, label=f'Target: {target_rate} tx/s')
    else:
        ax1.text(0.5, 0.5, 'No throughput data available', ha='center', va='center', transform=ax1.transAxes)
        ax1.set_title('Transaction Throughput Over Time')
        return
        
    # Add fault event markers
    if has_faults:
        event_labels = set()
        for (ts, evt, _) in events:
            # Mark observation start (end of warmup)
            if evt in ("start_observation", "after_netem"):
                label = "Observation Start"
                if label not in event_labels:
                    ax1.axvline(ts, linestyle="--", alpha=0.6, color='#4CAF50', linewidth=1.5, label=label)
                    event_labels.add(label)
            elif evt == "crash_start":
                label = "Crash Start"
                if label not in event_labels:
                    ax1.axvline(ts, linestyle="--", alpha=0.8, color='#F44336', linewidth=2, label=label)
                    event_labels.add(label)
            elif evt == "recovery_start":
                label = "Recovery Start"
                if label not in event_labels:
                    ax1.axvline(ts, linestyle=":", alpha=0.8, color='#9C27B0', linewidth=2, label=label)
                    event_labels.add(label)
            elif evt == "recovery_complete":
                label = "Recovery Complete"
                if label not in event_labels:
                    ax1.axvline(ts, linestyle=":", alpha=0.8, color='#2196F3', linewidth=2, label=label)
                    event_labels.add(label)
    
    ax1.set_ylabel('Throughput (tx/s)')
    ax1.set_title('Transaction Throughput Over Time (Warmup + Observation)')
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='upper right')
    
    # Format x-axis
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    
    # Confirmation latency over time (FILTERED to observation window)
    if not df_conf_plot.empty and len(df_conf_plot) > 0:
        # Rolling average latency with proper window size
        window_size = max(5, min(50, len(df_conf_plot) // 20))
        if window_size > 1:
            rolling_latency = df_conf_plot['latency_seconds'].rolling(window=window_size, center=True).mean()
            ax2.plot(df_conf_plot['confirm_ts_utc'], rolling_latency, linewidth=2, color='#FF9800', alpha=0.9, label=f'Latency (rolling avg, window={window_size})')
        else:
            ax2.plot(df_conf_plot['confirm_ts_utc'], df_conf_plot['latency_seconds'], linewidth=1, color='#FF9800', alpha=0.6, label='Latency')
        
        # Add fault event markers
        if has_faults:
            for (ts, evt, _) in events:
                if evt in ("start_observation", "after_netem"):
                    ax2.axvline(ts, linestyle="--", alpha=0.6, color='#4CAF50', linewidth=1.5)
                elif evt == "crash_start":
                    ax2.axvline(ts, linestyle="--", alpha=0.8, color='#F44336', linewidth=2)
                elif evt == "recovery_start":
                    ax2.axvline(ts, linestyle=":", alpha=0.8, color='#9C27B0', linewidth=2)
                elif evt == "recovery_complete":
                    ax2.axvline(ts, linestyle=":", alpha=0.8, color='#2196F3', linewidth=2)
        
        ax2.set_ylabel('Confirmation Latency (s)')
        ax2.set_title('Transaction Confirmation Latency Over Time (Warmup + Observation)')
        ax2.grid(True, alpha=0.3)
        ax2.legend(loc='upper right')
        
        # Format x-axis
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
    else:
        ax2.text(0.5, 0.5, 'No confirmation data available', ha='center', va='center', transform=ax2.transAxes)
        ax2.set_title('Transaction Confirmation Latency Over Time')
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "performance_timeline.png"), dpi=300, bbox_inches='tight')
    plt.close()
    
    # 2. Enhanced Fault Impact Analysis
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    title_suffix = "Impact Analysis" if has_faults else "Performance Analysis"
    fig.suptitle(f'{exp_type} - {title_suffix}', fontsize=16, fontweight='bold')
    
    # Confirmation latency distribution (FILTERED to observation window)
    if not df_conf_plot.empty and len(df_conf_plot) > 0:
        ax1 = axes[0, 0]
        latencies = df_conf_plot['latency_seconds'].astype(float)
        
        # Remove extreme outliers for better visualization
        q99 = latencies.quantile(0.99)
        latencies_clean = latencies[latencies <= q99]
        
        if len(latencies_clean) > 0:
            # Create histogram with statistics
            n, bins, patches = ax1.hist(latencies_clean, bins=min(30, len(latencies_clean)//5), 
                                      alpha=0.7, color='skyblue', edgecolor='black')
            
            # Add statistics
            mean_lat = latencies_clean.mean()
            median_lat = latencies_clean.median()
            p95_lat = latencies_clean.quantile(0.95)
            
            ax1.axvline(mean_lat, color='red', linestyle='--', linewidth=2, label=f'Mean: {mean_lat:.2f}s')
            ax1.axvline(median_lat, color='orange', linestyle='--', linewidth=2, label=f'Median: {median_lat:.2f}s')
            ax1.axvline(p95_lat, color='purple', linestyle='--', linewidth=2, label=f'P95: {p95_lat:.2f}s')
            
            ax1.set_xlabel('Confirmation Latency (s)')
            ax1.set_ylabel('Frequency')
            ax1.set_title('Confirmation Latency Distribution')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
        else:
            ax1.text(0.5, 0.5, 'No valid latency data', ha='center', va='center', transform=ax1.transAxes)
            ax1.set_title('Confirmation Latency Distribution')
    else:
        axes[0, 0].text(0.5, 0.5, 'No confirmation data', ha='center', va='center', transform=axes[0, 0].transAxes)
        axes[0, 0].set_title('Confirmation Latency Distribution')
    
    # Throughput distribution (FILTERED to observation window, using 30s bins)
    if throughput_binned and len(throughput_binned) > 0:
        ax2 = axes[0, 1]
        throughputs = [v for (_, v) in throughput_binned if not np.isnan(v)]
        
        if throughputs:
            ax2.hist(throughputs, bins=min(20, max(1, len(throughputs)//3)), alpha=0.7, color='lightgreen', edgecolor='black')
            mean_tps = np.mean(throughputs)
            ax2.axvline(mean_tps, color='red', linestyle='--', linewidth=2, 
                       label=f'Mean: {mean_tps:.2f} tx/s')
            ax2.set_xlabel('Throughput (tx/s)')
            ax2.set_ylabel('Frequency')
            ax2.set_title('Throughput Distribution (30s bins)')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
        else:
            ax2.text(0.5, 0.5, 'No valid throughput data', ha='center', va='center', transform=ax2.transAxes)
            ax2.set_title('Throughput Distribution')
    else:
        axes[0, 1].text(0.5, 0.5, 'No throughput data', ha='center', va='center', transform=axes[0, 1].transAxes)
        axes[0, 1].set_title('Throughput Distribution')
    
    # Performance before/after CRASH (not observation start!)
    # Uses 30s binned data for smooth visualization
    if has_faults and throughput_binned and events and len(throughput_binned) > 0:
        ax3 = axes[1, 0]
        
        # Find crash_start time (when nodes actually crash, not observation start)
        crash_time = None
        for (ts, evt, _) in events:
            if evt == "crash_start":
                crash_time = ts
                break
        
        if crash_time:
            # Split binned throughput data at crash_start
            before_crash = [(t, v) for (t, v) in throughput_binned if t < crash_time]
            after_crash = [(t, v) for (t, v) in throughput_binned if t >= crash_time]
            
            if before_crash or after_crash:
                if before_crash:
                    before_times = [t for (t, _) in before_crash]
                    before_vals = [v for (_, v) in before_crash]
                    ax3.plot(before_times, before_vals, linewidth=2.5, color='#4CAF50', alpha=0.9, label='Pre-Crash (Baseline)')
                    ax3.fill_between(before_times, before_vals, alpha=0.2, color='#4CAF50')
                
                if after_crash:
                    after_times = [t for (t, _) in after_crash]
                    after_vals = [v for (_, v) in after_crash]
                    ax3.plot(after_times, after_vals, linewidth=2.5, color='#F44336', alpha=0.9, label='After Crash')
                    ax3.fill_between(after_times, after_vals, alpha=0.2, color='#F44336')
                
                ax3.axvline(crash_time, linestyle="--", alpha=0.8, color='black', linewidth=2, label='Crash Start')
                
                # Add target rate line
                if target_rate > 0:
                    ax3.axhline(target_rate, linestyle=':', color='gray', alpha=0.5, label=f'Target: {target_rate} tx/s')
                
                ax3.set_ylabel('Throughput (tx/s)')
                ax3.set_xlabel('Time (UTC)')
                ax3.set_title('Performance Before/After Crash (30s bins)')
                ax3.legend(loc='upper right')
                ax3.grid(True, alpha=0.3)
                
                # Format x-axis
                ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
                plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)
            else:
                ax3.text(0.5, 0.5, 'Insufficient data for before/after analysis', ha='center', va='center', transform=ax3.transAxes)
                ax3.set_title('Performance Before/After Crash')
        else:
            ax3.text(0.5, 0.5, 'No crash_start event found', ha='center', va='center', transform=ax3.transAxes)
            ax3.set_title('Performance Before/After Crash')
    elif not has_faults:
        # For baseline, show throughput stability over time using 30s binned data
        ax3 = axes[1, 0]
        if throughput_binned and len(throughput_binned) > 0:
            times = [t for (t, _) in throughput_binned]
            vals = [v for (_, v) in throughput_binned]
            ax3.plot(times, vals, linewidth=2.5, color='#2196F3', alpha=0.9, label='Throughput (30s bins)')
            ax3.fill_between(times, vals, alpha=0.2, color='#2196F3')
            
            # Add median and stability band
            median_tps = np.median(vals)
            ax3.axhline(median_tps, linestyle='--', color='#FF9800', linewidth=2, label=f'Median: {median_tps:.2f} tx/s')
            ax3.fill_between(times, median_tps * 0.9, median_tps * 1.1, alpha=0.1, color='#FF9800', label='±10% band')
            
            if target_rate > 0:
                ax3.axhline(target_rate, linestyle=':', color='gray', alpha=0.5, label=f'Target: {target_rate} tx/s')
            
            ax3.set_ylabel('Throughput (tx/s)')
            ax3.set_xlabel('Time (UTC)')
            ax3.set_title('Throughput Stability (Baseline, 30s bins)')
            ax3.legend(loc='upper right')
            ax3.grid(True, alpha=0.3)
            ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)
        else:
            ax3.text(0.5, 0.5, 'Insufficient data for throughput analysis', 
                    ha='center', va='center', transform=ax3.transAxes)
            ax3.set_title('Baseline Throughput Stability')
    else:
        axes[1, 0].text(0.5, 0.5, 'No data for before/after analysis', ha='center', va='center', transform=axes[1, 0].transAxes)
        axes[1, 0].set_title('Performance Before/After Fault Injection')
    
    # System recovery analysis (only show if faults exist)
    if has_faults and not df_conf.empty and events and len(df_conf) > 0:
        ax4 = axes[1, 1]

        # Find crash_start time (when faults actually begin, not observation start)
        crash_time = None
        observation_time = None
        for (ts, evt, _) in events:
            if evt == "crash_start":
                crash_time = ts
            if evt in ("start_observation", "after_netem") and observation_time is None:
                observation_time = ts

        # Use crash_start if available, fall back to observation start
        reference_time = crash_time or observation_time
        time_label = "Time Since Crash Start (s)" if crash_time else "Time Since Observation Start (s)"

        if reference_time:
            # Analyze recovery after fault
            recovery_data = df_conf[df_conf['confirm_ts_utc'] >= reference_time].copy()
            if not recovery_data.empty:
                recovery_data['time_since_fault'] = (recovery_data['confirm_ts_utc'] - reference_time).dt.total_seconds()

                # Rolling average for recovery analysis
                window_size = min(20, len(recovery_data) // 5)
                if window_size > 1:
                    rolling_latency = recovery_data['latency_seconds'].rolling(window=window_size).mean()
                    ax4.plot(recovery_data['time_since_fault'], rolling_latency,
                           linewidth=2, color='red', alpha=0.8, label=f'Recovery Latency (window={window_size})')
                else:
                    ax4.plot(recovery_data['time_since_fault'], recovery_data['latency_seconds'],
                           linewidth=1, color='red', alpha=0.6, label='Recovery Latency')

                # Add recovery event markers (relative to reference_time)
                recovery_start_time = None
                recovery_complete_time = None
                for (ts, evt, _) in events:
                    if evt == "recovery_start":
                        recovery_start_time = (ts - reference_time).total_seconds()
                        ax4.axvline(recovery_start_time, linestyle=":", alpha=0.8, color='purple',
                                  linewidth=2, label='Recovery Start')
                    elif evt == "recovery_complete":
                        recovery_complete_time = (ts - reference_time).total_seconds()
                        ax4.axvline(recovery_complete_time, linestyle=":", alpha=0.8, color='blue',
                                  linewidth=2, label='Recovery Complete')

                ax4.set_xlabel(time_label)
                ax4.set_ylabel('Confirmation Latency (s)')
                ax4.set_title('System Recovery Analysis')
                ax4.legend()
                ax4.grid(True, alpha=0.3)
            else:
                ax4.text(0.5, 0.5, 'No recovery data available', ha='center', va='center', transform=ax4.transAxes)
                ax4.set_title('System Recovery Analysis')
        else:
            ax4.text(0.5, 0.5, 'No crash or observation start event found', ha='center', va='center', transform=ax4.transAxes)
            ax4.set_title('System Recovery Analysis')
    elif not has_faults:
        # For baseline, show latency stability over time
        ax4 = axes[1, 1]
        if not df_conf.empty and len(df_conf) > 0:
            # Skip first minute (warmup artifacts)
            if events:
                warmup_start = events[0][0] if events else df_conf['confirm_ts_utc'].min()
                warmup_cutoff = warmup_start + pd.Timedelta(seconds=60)
                stable_data = df_conf[df_conf['confirm_ts_utc'] >= warmup_cutoff].copy()
            else:
                stable_data = df_conf.copy()
            
            if not stable_data.empty:
                # Rolling average for smoother visualization
                window_size = min(50, len(stable_data) // 10)
                if window_size > 1:
                    rolling_latency = stable_data['latency_seconds'].rolling(window=window_size).mean()
                    ax4.plot(stable_data['confirm_ts_utc'], rolling_latency, 
                           linewidth=2, color='orange', alpha=0.8, 
                           label=f'Latency (rolling avg, window={window_size})')
                else:
                    ax4.plot(stable_data['confirm_ts_utc'], stable_data['latency_seconds'], 
                           linewidth=1, color='orange', alpha=0.6, label='Latency')
                
                # Add median line
                median_latency = stable_data['latency_seconds'].median()
                ax4.axhline(median_latency, linestyle='--', color='blue', linewidth=2,
                          label=f'Median: {median_latency:.2f}s')
                
                # Add stability band (±20%)
                ax4.fill_between(stable_data['confirm_ts_utc'], 
                               median_latency * 0.8, median_latency * 1.2,
                               alpha=0.2, color='orange', label='±20% band')
                
                ax4.set_ylabel('Confirmation Latency (s)')
                ax4.set_title('Baseline Latency Stability')
                ax4.legend()
                ax4.grid(True, alpha=0.3)
                ax4.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
                ax4.xaxis.set_major_locator(mdates.MinuteLocator(interval=2))
                plt.setp(ax4.xaxis.get_majorticklabels(), rotation=45)
            else:
                ax4.text(0.5, 0.5, 'Insufficient stable data', 
                        ha='center', va='center', transform=ax4.transAxes)
                ax4.set_title('Baseline Latency Stability')
        else:
            ax4.text(0.5, 0.5, 'No confirmation data available', 
                    ha='center', va='center', transform=ax4.transAxes)
            ax4.set_title('Baseline Latency Stability')
    else:
        axes[1, 1].text(0.5, 0.5, 'No data for recovery analysis', ha='center', va='center', transform=axes[1, 1].transAxes)
        axes[1, 1].set_title('System Recovery Analysis')
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "fault_impact_analysis.png"), dpi=300, bbox_inches='tight')
    plt.close()
    
    # 3. System Health Dashboard
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    fig.suptitle(f'System Health Dashboard - {exp_type}', fontsize=16, fontweight='bold')
    
    # TRUE Availability over time (Confirmed / Submitted per time window)
    # SCIENTIFIC FIX: Use actual TX counts, not throughput-based estimation
    ax1 = axes[0, 0]
    
    if not df_sub.empty and not df_conf_plot.empty:
        # Ensure timestamps are datetime
        df_sub_copy = df_sub.copy()
        df_conf_copy = df_conf_plot.copy()
        df_sub_copy["submit_ts_utc"] = parse_iso8601(df_sub_copy["submit_ts_utc"])
        df_conf_copy["confirm_ts_utc"] = parse_iso8601(df_conf_copy["confirm_ts_utc"])
        
        # Filter to plot window
        if plot_start_ts is not None:
            df_sub_copy = df_sub_copy[df_sub_copy["submit_ts_utc"] >= plot_start_ts]
        if end_observe_ts is not None:
            df_sub_copy = df_sub_copy[df_sub_copy["submit_ts_utc"] <= end_observe_ts]
        
        if not df_sub_copy.empty:
            # Calculate availability in 60-second rolling windows based on SUBMIT time
            window_size_s = 60
            time_windows = []
            availability_windows = []
            
            start_time = df_sub_copy["submit_ts_utc"].min()
            end_time = df_sub_copy["submit_ts_utc"].max()
            
            current_time = start_time
            while current_time <= end_time:
                window_end = current_time + pd.Timedelta(seconds=window_size_s)
                
                # Count TXs submitted in this window
                submitted_in_window = df_sub_copy[
                    (df_sub_copy["submit_ts_utc"] >= current_time) & 
                    (df_sub_copy["submit_ts_utc"] < window_end)
                ]
                
                # Count how many of those were confirmed (by matching txid if available)
                if "txid" in df_sub_copy.columns and "txid" in df_conf_copy.columns:
                    submitted_txids = set(submitted_in_window["txid"].tolist())
                    confirmed_txids = set(df_conf_copy["txid"].tolist())
                    confirmed_count = len(submitted_txids & confirmed_txids)
                else:
                    # Fallback: count confirmations with submit_ts in window
                    if "submit_ts_utc" in df_conf_copy.columns:
                        confirmed_count = len(df_conf_copy[
                            (df_conf_copy["submit_ts_utc"] >= current_time) & 
                            (df_conf_copy["submit_ts_utc"] < window_end)
                        ])
                    else:
                        confirmed_count = len(submitted_in_window)  # Assume 100%
                
                submitted_count = len(submitted_in_window)
                if submitted_count > 0:
                    availability = min(1.0, confirmed_count / submitted_count)
                    time_windows.append(current_time + pd.Timedelta(seconds=window_size_s/2))
                    availability_windows.append(availability)
                
                current_time = window_end
            
            if time_windows:
                color = '#4CAF50' if not has_faults else '#FF9800'
                ax1.plot(time_windows, availability_windows, linewidth=2.5, color=color, alpha=0.9, 
                        label='Availability (Confirmed/Submitted)')
                ax1.fill_between(time_windows, availability_windows, alpha=0.2, color=color)
                
                # Add 100% target line
                ax1.axhline(1.0, linestyle='--', color='#2196F3', linewidth=1.5, alpha=0.7, label='Target: 100%')
                
                # Show average
                avg_availability = np.mean(availability_windows)
                ax1.text(0.02, 0.98, f'Avg: {avg_availability*100:.1f}%', 
                        transform=ax1.transAxes, va='top', ha='left', fontsize=11,
                        bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
                
                ax1.set_ylabel('Availability (Confirmed / Submitted)')
                ax1.set_title('Transaction Availability Over Time (60s windows)')
                ax1.legend(loc='lower right')
                ax1.grid(True, alpha=0.3)
                ax1.set_ylim(0, 1.05)
                
                ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
                plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
            else:
                ax1.text(0.5, 0.5, 'Insufficient data for availability', ha='center', va='center', transform=ax1.transAxes)
                ax1.set_title('Transaction Availability Over Time')
        else:
            ax1.text(0.5, 0.5, 'No submissions in plot window', ha='center', va='center', transform=ax1.transAxes)
            ax1.set_title('Transaction Availability Over Time')
    else:
        ax1.text(0.5, 0.5, 'No data for availability analysis', ha='center', va='center', transform=ax1.transAxes)
        ax1.set_title('Transaction Availability Over Time')
    
    # Transaction success rate (FILTERED exactly like metrics.json for consistency)
    # metrics.json uses: netem_applied -> (end_observe - 60s buffer)
    # and filters confirmations by submit_ts_utc
    if not df_conf.empty and df_sub is not None and len(df_conf) > 0:
        ax2 = axes[0, 1]

        # Use same filtering as metrics.json (netem_applied as start, with buffer)
        # Buffer must be long enough for TXs to confirm under fault conditions
        # (p99 latency can be ~140s, max ~185s under heavy faults)
        CONFIRMATION_BUFFER_SECONDS = 180
        start_ts = netem_applied_ts or observation_start_ts
        end_ts_with_buffer = end_observe_ts - pd.Timedelta(seconds=CONFIRMATION_BUFFER_SECONDS) if end_observe_ts else None

        # Filter SUBMISSIONS
        df_sub_obs = df_sub.copy()
        df_sub_obs["submit_ts_utc"] = parse_iso8601(df_sub_obs["submit_ts_utc"])
        if start_ts is not None:
            df_sub_obs = df_sub_obs[df_sub_obs["submit_ts_utc"] >= start_ts]
        if end_ts_with_buffer is not None:
            df_sub_obs = df_sub_obs[df_sub_obs["submit_ts_utc"] <= end_ts_with_buffer]

        # Filter CONFIRMATIONS by their SUBMIT time (same as metrics.json)
        df_conf_obs = df_conf.copy()
        if "submit_ts_utc" in df_conf_obs.columns:
            df_conf_obs["submit_ts_utc"] = parse_iso8601(df_conf_obs["submit_ts_utc"])
            if start_ts is not None:
                df_conf_obs = df_conf_obs[df_conf_obs["submit_ts_utc"] >= start_ts]
            if end_ts_with_buffer is not None:
                df_conf_obs = df_conf_obs[df_conf_obs["submit_ts_utc"] <= end_ts_with_buffer]
        else:
            # Fallback: filter by confirm time if submit time not available
            df_conf_obs["confirm_ts_utc"] = parse_iso8601(df_conf_obs["confirm_ts_utc"])
            if start_ts is not None:
                df_conf_obs = df_conf_obs[df_conf_obs["confirm_ts_utc"] >= start_ts]
            if end_ts_with_buffer is not None:
                df_conf_obs = df_conf_obs[df_conf_obs["confirm_ts_utc"] <= end_ts_with_buffer]

        total_submitted = len(df_sub_obs)
        total_confirmed = len(df_conf_obs)
        success_rate = total_confirmed / total_submitted if total_submitted > 0 else 0
        
        # Create a simple bar chart
        categories = ['Submitted', 'Confirmed']
        values = [total_submitted, total_confirmed]
        colors = ['lightcoral', 'lightgreen']
        
        bars = ax2.bar(categories, values, color=colors, alpha=0.7, edgecolor='black')
        ax2.set_ylabel('Number of Transactions')
        ax2.set_title(f'Transaction Success Rate: {success_rate:.1%}')
        ax2.grid(True, alpha=0.3)
        
        # Add value labels on bars
        for bar, value in zip(bars, values):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + height*0.01,
                    f'{value}', ha='center', va='bottom')
    else:
        axes[0, 1].text(0.5, 0.5, 'No data for success rate analysis', ha='center', va='center', transform=axes[0, 1].transAxes)
        axes[0, 1].set_title('Transaction Success Rate')
    
    # TX Backlog Over Time - Shows crash impact on confirmation rate
    # SCIENTIFIC VALUE: During crashes, submitted TXs pile up as confirmations slow down
    ax3 = axes[1, 0]
    
    if not df_sub.empty and not df_conf_plot.empty:
        df_sub_sorted = df_sub.copy()
        df_conf_sorted = df_conf_plot.copy()
        df_sub_sorted["submit_ts_utc"] = parse_iso8601(df_sub_sorted["submit_ts_utc"])
        df_conf_sorted["confirm_ts_utc"] = parse_iso8601(df_conf_sorted["confirm_ts_utc"])
        
        # Filter to plot window
        if plot_start_ts is not None:
            df_sub_sorted = df_sub_sorted[df_sub_sorted["submit_ts_utc"] >= plot_start_ts]
        if end_observe_ts is not None:
            df_sub_sorted = df_sub_sorted[df_sub_sorted["submit_ts_utc"] <= end_observe_ts]
        
        if not df_sub_sorted.empty and not df_conf_sorted.empty:
            # Sort by timestamp
            df_sub_sorted = df_sub_sorted.sort_values("submit_ts_utc")
            df_conf_sorted = df_conf_sorted.sort_values("confirm_ts_utc")
            
            # Calculate cumulative counts
            submit_times = df_sub_sorted["submit_ts_utc"].tolist()
            confirm_times = df_conf_sorted["confirm_ts_utc"].tolist()
            
            # Create unified timeline
            all_times = sorted(set(submit_times + confirm_times))
            
            cumulative_submitted = []
            cumulative_confirmed = []
            backlog = []
            
            sub_count = 0
            conf_count = 0
            sub_idx = 0
            conf_idx = 0
            
            for t in all_times:
                # Count submissions up to this time
                while sub_idx < len(submit_times) and submit_times[sub_idx] <= t:
                    sub_count += 1
                    sub_idx += 1
                # Count confirmations up to this time
                while conf_idx < len(confirm_times) and confirm_times[conf_idx] <= t:
                    conf_count += 1
                    conf_idx += 1
                
                cumulative_submitted.append(sub_count)
                cumulative_confirmed.append(conf_count)
                backlog.append(sub_count - conf_count)
            
            # Plot cumulative lines
            ax3.plot(all_times, cumulative_submitted, linewidth=2, color='#F44336', alpha=0.8, 
                    label='Cumulative Submitted')
            ax3.plot(all_times, cumulative_confirmed, linewidth=2, color='#4CAF50', alpha=0.8, 
                    label='Cumulative Confirmed')
            
            # Fill the gap (backlog area)
            ax3.fill_between(all_times, cumulative_confirmed, cumulative_submitted, 
                           alpha=0.3, color='#FF9800', label='Pending (Backlog)')
            
            # Add crash event markers
            if has_faults:
                for (ts, evt, _) in events:
                    if evt == "crash_start":
                        ax3.axvline(ts, linestyle="--", alpha=0.8, color='black', linewidth=2)
                        ax3.text(ts, max(cumulative_submitted) * 0.95, ' Crash', fontsize=9, va='top')
                    elif evt == "recovery_complete":
                        ax3.axvline(ts, linestyle=":", alpha=0.8, color='#2196F3', linewidth=2)
                        ax3.text(ts, max(cumulative_submitted) * 0.95, ' Recovered', fontsize=9, va='top')
            
            # Add max backlog annotation
            max_backlog = max(backlog)
            max_backlog_idx = backlog.index(max_backlog)
            max_backlog_time = all_times[max_backlog_idx]
            ax3.annotate(f'Max Backlog: {max_backlog}', 
                        xy=(max_backlog_time, cumulative_submitted[max_backlog_idx] - max_backlog/2),
                        fontsize=10, ha='center',
                        bbox=dict(boxstyle='round', facecolor='yellow', alpha=0.8))
            
            ax3.set_ylabel('Transaction Count')
            ax3.set_xlabel('Time (UTC)')
            ax3.set_title('TX Submission vs Confirmation (Backlog = Crash Impact)')
            ax3.legend(loc='upper left')
            ax3.grid(True, alpha=0.3)
            ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)
        else:
            ax3.text(0.5, 0.5, 'Insufficient data for backlog analysis', ha='center', va='center', transform=ax3.transAxes)
            ax3.set_title('TX Backlog Over Time')
    else:
        ax3.text(0.5, 0.5, 'No data for backlog analysis', ha='center', va='center', transform=ax3.transAxes)
        ax3.set_title('TX Backlog Over Time')
    
    # Experiment phases timeline
    ax4 = axes[1, 1]
    
    if events:
        event_times = []
        event_labels = []
        event_colors = []
        
        for (ts, evt, _) in events:
            if evt in ("start_warmup", "after_netem", "end_observe", "recovery_start", "recovery_complete"):
                event_times.append(ts)
                # Adjust labels based on experiment type
                if not has_faults and evt in ("start_observation", "after_netem"):
                    label = "Start Observation"
                elif evt == "recovery_start":
                    label = "Recovery Start"
                elif evt == "recovery_complete":
                    label = "Recovery Complete"
                else:
                    label = evt.replace('_', ' ').title()
                event_labels.append(label)
                # Color coding: green=start, red=fault, blue=end, purple=recovery_start, cyan=recovery_complete
                if not has_faults and evt in ("start_observation", "after_netem"):
                    event_colors.append('orange')
                elif evt == "recovery_start":
                    event_colors.append('purple')
                elif evt == "recovery_complete":
                    event_colors.append('cyan')
                else:
                    event_colors.append('green' if evt == "start_warmup" else 'red' if evt in ("start_observation", "after_netem") else 'blue')

        
        if event_times:
            y_pos = range(len(event_times))
            ax4.barh(y_pos, [1] * len(event_times), color=event_colors, alpha=0.7)
            ax4.set_yticks(y_pos)
            ax4.set_yticklabels(event_labels)
            ax4.set_xlabel('Event Timeline')
            title = 'Experiment Phases' if not has_faults else 'Fault Events Timeline'
            ax4.set_title(title)
            ax4.grid(True, alpha=0.3)
            
            # Add time labels
            for i, (time, label) in enumerate(zip(event_times, event_labels)):
                ax4.text(0.5, i, time.strftime('%H:%M:%S'), ha='center', va='center', 
                        transform=ax4.get_yaxis_transform(), fontsize=8)
        else:
            ax4.text(0.5, 0.5, 'No events found', ha='center', va='center', transform=ax4.transAxes)
            title = 'Experiment Phases' if not has_faults else 'Fault Events Timeline'
            ax4.set_title(title)
    else:
        ax4.text(0.5, 0.5, 'No events data', ha='center', va='center', transform=ax4.transAxes)
        title = 'Experiment Phases' if not has_faults else 'Fault Events Timeline'
        ax4.set_title(title)
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "system_health_dashboard.png"), dpi=300, bbox_inches='tight')
    plt.close()

def create_throughput_comparison_plot(run_dir, events, df_sub, df_conf, avg_throughput_official=0.0):
    """
    Create a detailed throughput comparison plot using multiple methods.
    This is for METHODOLOGY VALIDATION only - shows why we chose specific binning.
    
    NOTE: This plot is optional and useful for thesis appendix to justify method choice.
    For main results, use performance_timeline.png which shows only the clean 30s binned data.
    """
    plots_dir = os.path.join(run_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
    # SCIENTIFIC FIX: Filter data to experiment window
    # Use netem_applied as baseline start (for throughput_by_phase analysis)
    # Fall back to start_observation if netem_applied not found
    baseline_filter_ts = None
    end_observe_ts = None
    start_warmup_ts = None
    netem_applied_ts = None
    start_observation_ts = None
    for (ts, evt, _) in events:
        if evt == "start_warmup" and start_warmup_ts is None:
            start_warmup_ts = ts
        elif evt == "netem_applied" and netem_applied_ts is None:
            netem_applied_ts = ts
        elif evt in ("start_observation", "after_netem") and start_observation_ts is None:
            start_observation_ts = ts
        elif evt == "end_observe" and end_observe_ts is None:
            end_observe_ts = ts

    # Use netem_applied for baseline (includes warmup period under degraded conditions)
    # Fall back to start_warmup, then start_observation
    baseline_filter_ts = netem_applied_ts or start_warmup_ts or start_observation_ts

    # Filter dataframes to experiment window (from baseline, not just observation)
    df_sub_filtered = df_sub.copy() if not df_sub.empty else pd.DataFrame()
    df_conf_filtered = df_conf.copy() if not df_conf.empty else pd.DataFrame()

    if not df_sub_filtered.empty and baseline_filter_ts is not None:
        df_sub_filtered["submit_ts_utc"] = parse_iso8601(df_sub_filtered["submit_ts_utc"])
        df_sub_filtered = df_sub_filtered[df_sub_filtered["submit_ts_utc"] >= baseline_filter_ts]
        if end_observe_ts is not None:
            df_sub_filtered = df_sub_filtered[df_sub_filtered["submit_ts_utc"] <= end_observe_ts]

    if not df_conf_filtered.empty and baseline_filter_ts is not None:
        df_conf_filtered["confirm_ts_utc"] = parse_iso8601(df_conf_filtered["confirm_ts_utc"])
        # For throughput_by_phase, filter by SUBMISSION time (not confirmation time)
        # This ensures TXs submitted during observation are counted even if confirmed later
        if "submit_ts_utc" in df_conf_filtered.columns:
            df_conf_filtered["submit_ts_utc"] = parse_iso8601(df_conf_filtered["submit_ts_utc"])
            df_conf_filtered = df_conf_filtered[df_conf_filtered["submit_ts_utc"] >= baseline_filter_ts]
            if end_observe_ts is not None:
                df_conf_filtered = df_conf_filtered[df_conf_filtered["submit_ts_utc"] <= end_observe_ts]
        else:
            # Fallback to confirm_ts filtering if submit_ts not available
            df_conf_filtered = df_conf_filtered[df_conf_filtered["confirm_ts_utc"] >= baseline_filter_ts]
            if end_observe_ts is not None:
                df_conf_filtered = df_conf_filtered[df_conf_filtered["confirm_ts_utc"] <= end_observe_ts]
    
    # Load experiment configuration
    metadata_file = os.path.join(run_dir, "metadata.yml")
    exp_config = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, 'r') as f:
            for line in f:
                if ':' in line:
                    key, val = line.strip().split(':', 1)
                    exp_config[key.strip()] = val.strip()
    
    # Determine experiment type
    crash_frac = float(exp_config.get('crash_fraction', 0))
    loss_pct = float(exp_config.get('loss_pct', 0))
    latency_ms = float(exp_config.get('latency_ms', 0))
    
    BASELINE_LATENCY_THRESHOLD = 100
    BASELINE_LOSS_THRESHOLD = 1
    
    if crash_frac == 0 and loss_pct <= BASELINE_LOSS_THRESHOLD and latency_ms <= BASELINE_LATENCY_THRESHOLD:
        exp_type = "Baseline (No Faults)"
        has_faults = False
    elif crash_frac > 0 and loss_pct <= BASELINE_LOSS_THRESHOLD and latency_ms <= BASELINE_LATENCY_THRESHOLD:
        exp_type = f"Crash-Only ({crash_frac*100:.0f}% Node Failures)"
        has_faults = True
    elif crash_frac == 0 and (loss_pct > BASELINE_LOSS_THRESHOLD or latency_ms > BASELINE_LATENCY_THRESHOLD):
        exp_type = f"Network-Only ({loss_pct:.0f}% Loss, {latency_ms:.0f}ms Latency)"
        has_faults = True
    else:
        exp_type = f"Combined Faults ({crash_frac*100:.0f}% Crashes + {loss_pct:.0f}% Loss + {latency_ms:.0f}ms Latency)"
        has_faults = True
    
    # Prepare FILTERED data
    submit_times = sorted(df_sub_filtered["submit_ts_utc"].tolist()) if not df_sub_filtered.empty else []
    conf_times = sorted(df_conf_filtered["confirm_ts_utc"].tolist()) if not df_conf_filtered.empty else []
    
    if not conf_times:
        print("⚠️  No confirmation data for throughput comparison plot")
        return
    
    # Calculate throughput using different methods
    # Official method: Block-based throughput
    block_throughput = block_based_throughput(run_dir, df_conf)
    
    # Alternative methods for comparison
    binned_10s = binned_rate(conf_times, bin_size=10)
    binned_30s = binned_rate(conf_times, bin_size=30)
    rolling_60s = rolling_rate(conf_times, window=60)
    
    # Also calculate submission rate for comparison
    submission_binned = binned_rate(submit_times, bin_size=10) if submit_times else []
    
    # Create figure with 3 subplots
    fig, axes = plt.subplots(3, 1, figsize=(16, 12))
    fig.suptitle(f'Throughput Analysis - Method Comparison\n{exp_type}', 
                 fontsize=16, fontweight='bold')
    
    # Plot 1: Official Block-based vs Alternative Methods
    ax1 = axes[0]
    if block_throughput:
        times_block = [t for (t, _) in block_throughput]
        values_block = [v for (_, v) in block_throughput]
        ax1.plot(times_block, values_block, linewidth=2.5, color='green', alpha=0.9, 
                label='Official: Block-based (TX/Block ÷ Block Interval)', marker='o', markersize=4)
    
    if binned_10s:
        times_10s = [t for (t, _) in binned_10s]
        values_10s = [v for (_, v) in binned_10s]
        ax1.plot(times_10s, values_10s, linewidth=1.5, color='blue', alpha=0.6, 
                label='Fixed Bins (10s) - TX-based', marker='s', markersize=2)
    
    if rolling_60s:
        times_rolling = [t for (t, _) in rolling_60s]
        values_rolling = [v for (_, v) in rolling_60s]
        ax1.plot(times_rolling, values_rolling, linewidth=1, color='red', alpha=0.4, 
                linestyle='--', label='Rolling Window (60s) - Has Artifacts')
    
    # Add event markers
    if has_faults and events:
        for (ts, evt, _) in events:
            if evt in ("start_observation", "after_netem"):
                ax1.axvline(ts, linestyle=":", alpha=0.7, color='red', 
                          linewidth=2, label='Fault Injection')
            elif evt == "recovery_start":
                ax1.axvline(ts, linestyle=":", alpha=0.8, color='purple', 
                          linewidth=2, label='Recovery Start')
            elif evt == "recovery_complete":
                ax1.axvline(ts, linestyle=":", alpha=0.8, color='blue', 
                          linewidth=2, label='Recovery Complete')
    elif events:
        for (ts, evt, _) in events:
            if evt == "start_warmup":
                ax1.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='Start')
            elif evt == "end_observe":
                ax1.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='End')
    
    ax1.set_ylabel('Confirmation Rate (tx/s)', fontsize=11)
    ax1.set_title('Method Comparison: Fixed Time Bins vs Rolling Window', fontsize=12)
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc='best')
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    
    # Plot 2: Official Block-based vs Bin Sizes
    ax2 = axes[1]
    if block_throughput:
        times_block = [t for (t, _) in block_throughput]
        values_block = [v for (_, v) in block_throughput]
        ax2.plot(times_block, values_block, linewidth=2.5, color='green', alpha=0.9, 
                label='Official: Block-based', marker='o', markersize=4)
    
    if binned_10s:
        times_10s = [t for (t, _) in binned_10s]
        values_10s = [v for (_, v) in binned_10s]
        ax2.plot(times_10s, values_10s, linewidth=1.5, color='blue', alpha=0.6, 
                label='10-second bins (TX-based)', marker='s', markersize=2)
    
    if binned_30s:
        times_30s = [t for (t, _) in binned_30s]
        values_30s = [v for (_, v) in binned_30s]
        ax2.plot(times_30s, values_30s, linewidth=1.5, color='orange', alpha=0.6, 
                label='30-second bins (TX-based)', marker='^', markersize=3)
    
    # Add target rate line
    target_rate = float(exp_config.get('tx_rate', 10))
    if target_rate > 0:
        ax2.axhline(target_rate, linestyle='--', color='red', linewidth=2, 
                   alpha=0.7, label=f'Target Rate: {target_rate} tx/s')
    
    ax2.set_ylabel('Throughput (tx/s)', fontsize=11)
    ax2.set_title('Official Block-based vs TX-based Binning', fontsize=12)
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc='best')
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
    
    # Plot 3: Submission Rate vs Official Block-based Throughput
    ax3 = axes[2]
    if submission_binned:
        times_sub = [t for (t, _) in submission_binned]
        values_sub = [v for (_, v) in submission_binned]
        ax3.plot(times_sub, values_sub, linewidth=2, color='purple', alpha=0.6, 
                linestyle=':', label='Submission Rate (Mempool Input)', marker='x', markersize=3)
    
    if block_throughput:
        times_block = [t for (t, _) in block_throughput]
        values_block = [v for (_, v) in block_throughput]
        ax3.plot(times_block, values_block, linewidth=2.5, color='green', alpha=0.9, 
                label='Official Throughput (Blockchain Output)', marker='o', markersize=4)
    elif binned_10s:
        times_10s = [t for (t, _) in binned_10s]
        values_10s = [v for (_, v) in binned_10s]
        ax3.plot(times_10s, values_10s, linewidth=2, color='blue', alpha=0.8, 
                label='Confirmation Rate (TX-based, Fallback)', marker='o', markersize=3)
    
    # Calculate and show backlog if submission > confirmation
    if submission_binned and len(submission_binned) > 0:
        avg_submit = np.mean([v for (_, v) in submission_binned])
        if block_throughput and len(block_throughput) > 0:
            avg_confirm = np.mean([v for (_, v) in block_throughput])
        elif binned_10s and len(binned_10s) > 0:
            avg_confirm = np.mean([v for (_, v) in binned_10s])
        else:
            avg_confirm = 0
        
        backlog_rate = avg_submit - avg_confirm
        
        if abs(backlog_rate) > 0.1:
            backlog_text = f"Avg Backlog Rate: {backlog_rate:+.2f} tx/s"
            if backlog_rate > 0:
                backlog_text += " (Mempool growing)"
            else:
                backlog_text += " (Mempool shrinking)"
            ax3.text(0.02, 0.98, backlog_text, transform=ax3.transAxes, 
                    fontsize=10, verticalalignment='top',
                    bbox=dict(boxstyle="round,pad=0.5", facecolor="yellow", alpha=0.7))
    
    # Add event markers to Plot 3
    if has_faults and events:
        for (ts, evt, _) in events:
            if evt in ("start_observation", "after_netem"):
                ax3.axvline(ts, linestyle=":", alpha=0.7, color='red', 
                          linewidth=2, label='Fault Injection')
            elif evt == "recovery_start":
                ax3.axvline(ts, linestyle=":", alpha=0.8, color='purple', 
                          linewidth=2, label='Recovery Start')
            elif evt == "recovery_complete":
                ax3.axvline(ts, linestyle=":", alpha=0.8, color='blue', 
                          linewidth=2, label='Recovery Complete')
    elif events:
        for (ts, evt, _) in events:
            if evt == "start_warmup":
                ax3.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='Start')
            elif evt == "end_observe":
                ax3.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='End')
    
    ax3.set_ylabel('Transaction Rate (tx/s)', fontsize=11)
    ax3.set_xlabel('Time (UTC)', fontsize=11)
    ax3.set_title('Mempool Dynamics: Submission vs Confirmation Rate', fontsize=12)
    ax3.grid(True, alpha=0.3)
    ax3.legend(loc='best')
    ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)
    
    # Add summary statistics box
    if binned_10s:
        values = [v for (_, v) in binned_10s]
        stats_text = (
            f"Fixed Bin Statistics (10s):\n"
            f"Official Avg: {avg_throughput_official:.2f} tx/s\n"
            f"Mean: {np.mean(values):.2f} tx/s\n"
            f"Median: {np.median(values):.2f} tx/s\n"
            f"Std Dev: {np.std(values):.2f} tx/s\n"
            f"Min: {np.min(values):.2f} tx/s\n"
            f"Max: {np.max(values):.2f} tx/s"
        )
        fig.text(0.98, 0.02, stats_text, fontsize=9, verticalalignment='bottom',
                horizontalalignment='right', fontfamily='monospace',
                bbox=dict(boxstyle="round,pad=0.5", facecolor="lightblue", alpha=0.8))
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "throughput_comparison.png"), dpi=300, bbox_inches='tight')
    plt.close()
    
    print("✅ Created throughput_comparison.png")


def create_mempool_plot(run_dir, events):
    """
    Create a plot showing mempool size over time across nodes.
    """
    plots_dir = os.path.join(run_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
    mempool_file = os.path.join(run_dir, "mempool_timeseries.csv")
    if not os.path.exists(mempool_file):
        print("⚠️  No mempool_timeseries.csv found, skipping mempool plot")
        return
    
    # Load mempool data (supports both old and new format)
    try:
        df_mempool = pd.read_csv(mempool_file)
        if df_mempool.empty:
            print("⚠️  mempool_timeseries.csv is empty, skipping mempool plot")
            return

        # Handle both old format (timestamp_utc) and new format (cycle_start_utc)
        if "cycle_start_utc" in df_mempool.columns:
            # New format with cycle-based sampling
            # Filter out corrupted rows (where timestamp column doesn't look like a timestamp)
            valid_mask = df_mempool["cycle_start_utc"].str.startswith("202", na=False)
            if not valid_mask.all():
                bad_count = (~valid_mask).sum()
                print(f"⚠️  Filtered {bad_count} corrupted rows from mempool data")
                df_mempool = df_mempool[valid_mask].copy()
            df_mempool["timestamp_utc"] = pd.to_datetime(df_mempool["cycle_start_utc"])
        elif "timestamp_utc" in df_mempool.columns:
            # Old format - also filter corrupted rows
            valid_mask = df_mempool["timestamp_utc"].str.startswith("202", na=False)
            if not valid_mask.all():
                bad_count = (~valid_mask).sum()
                print(f"⚠️  Filtered {bad_count} corrupted rows from mempool data")
                df_mempool = df_mempool[valid_mask].copy()
            df_mempool["timestamp_utc"] = pd.to_datetime(df_mempool["timestamp_utc"])
        else:
            print("⚠️  mempool_timeseries.csv has unknown format, skipping mempool plot")
            return

        if df_mempool.empty:
            print("⚠️  No valid mempool data after filtering, skipping mempool plot")
            return
    except Exception as e:
        print(f"⚠️  Could not load mempool_timeseries.csv: {e}")
        return
    
    # Load experiment configuration
    metadata_file = os.path.join(run_dir, "metadata.yml")
    exp_config = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, 'r') as f:
            for line in f:
                if ':' in line:
                    key, val = line.strip().split(':', 1)
                    exp_config[key.strip()] = val.strip()
    
    crash_frac = float(exp_config.get('crash_fraction', 0))
    loss_pct = float(exp_config.get('loss_pct', 0))
    latency_ms = float(exp_config.get('latency_ms', 0))
    
    BASELINE_LATENCY_THRESHOLD = 100
    BASELINE_LOSS_THRESHOLD = 1
    
    if crash_frac == 0 and loss_pct <= BASELINE_LOSS_THRESHOLD and latency_ms <= BASELINE_LATENCY_THRESHOLD:
        exp_type = "Baseline (No Faults)"
        has_faults = False
    elif crash_frac > 0 and loss_pct <= BASELINE_LOSS_THRESHOLD and latency_ms <= BASELINE_LATENCY_THRESHOLD:
        exp_type = f"Crash-Only ({crash_frac*100:.0f}% Node Failures)"
        has_faults = True
    elif crash_frac == 0 and (loss_pct > BASELINE_LOSS_THRESHOLD or latency_ms > BASELINE_LATENCY_THRESHOLD):
        exp_type = f"Network-Only ({loss_pct:.0f}% Loss, {latency_ms:.0f}ms Latency)"
        has_faults = True
    else:
        exp_type = f"Combined Faults ({crash_frac*100:.0f}% Crashes + {loss_pct:.0f}% Loss + {latency_ms:.0f}ms Latency)"
        has_faults = True
    
    # Create plot
    fig, axes = plt.subplots(2, 1, figsize=(14, 10))
    fig.suptitle(f'Mempool Analysis - {exp_type}', fontsize=16, fontweight='bold')
    
    # Plot 1: Mempool size over time (all nodes, aggregated)
    ax1 = axes[0]
    
    # Group by timestamp and calculate statistics
    mempool_by_time = df_mempool.groupby('timestamp_utc')['mempool_size'].agg(['mean', 'median', 'min', 'max'])
    
    times = mempool_by_time.index
    ax1.plot(times, mempool_by_time['mean'], linewidth=2, color='blue', alpha=0.8, label='Mean Mempool Size')
    ax1.plot(times, mempool_by_time['median'], linewidth=2, color='green', alpha=0.8, label='Median Mempool Size')
    ax1.fill_between(times, mempool_by_time['min'], mempool_by_time['max'], 
                     alpha=0.2, color='blue', label='Min-Max Range')
    
    # Add fault event markers
    event_labels_added = set()
    if has_faults:
        for (ts, evt, _) in events:
            if evt == "start_warmup" and "Start Warmup" not in event_labels_added:
                ax1.axvline(ts, linestyle="--", alpha=0.7, color='green', label="Start Warmup")
                event_labels_added.add("Start Warmup")
            elif evt == "crash_start" and "Crash Start" not in event_labels_added:
                ax1.axvline(ts, linestyle="--", alpha=0.8, color='red', linewidth=2, label="Crash Start")
                event_labels_added.add("Crash Start")
            elif evt == "end_observe" and "End Observe" not in event_labels_added:
                ax1.axvline(ts, linestyle="--", alpha=0.7, color='gray', label="End Observe")
                event_labels_added.add("End Observe")
            elif evt == "recovery_start" and "Recovery Start" not in event_labels_added:
                ax1.axvline(ts, linestyle=":", alpha=0.8, color='purple', linewidth=2, label="Recovery Start")
                event_labels_added.add("Recovery Start")
            elif evt == "recovery_complete" and "Recovery Complete" not in event_labels_added:
                ax1.axvline(ts, linestyle=":", alpha=0.8, color='blue', linewidth=2, label="Recovery Complete")
                event_labels_added.add("Recovery Complete")
    else:
        for (ts, evt, _) in events:
            if evt == "start_warmup":
                ax1.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='Start Warmup')
            elif evt == "end_observe":
                ax1.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='End Observation')
    
    ax1.set_ylabel('Mempool Size (transactions)', fontsize=11)
    ax1.set_title('Mempool Size Over Time (Aggregated Across Nodes)', fontsize=12)
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    ax1.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    
    # Plot 2: Mempool bytes over time
    ax2 = axes[1]
    
    mempool_bytes_by_time = df_mempool.groupby('timestamp_utc')['mempool_bytes'].agg(['mean', 'median', 'min', 'max'])
    
    times_bytes = mempool_bytes_by_time.index
    ax2.plot(times_bytes, mempool_bytes_by_time['mean'], linewidth=2, color='orange', alpha=0.8, label='Mean Mempool Bytes')
    ax2.plot(times_bytes, mempool_bytes_by_time['median'], linewidth=2, color='purple', alpha=0.8, label='Median Mempool Bytes')
    ax2.fill_between(times_bytes, mempool_bytes_by_time['min'], mempool_bytes_by_time['max'], 
                     alpha=0.2, color='orange', label='Min-Max Range')
    
    # Add fault event markers (same as above but no labels - they're in legend from ax1)
    if has_faults:
        for (ts, evt, _) in events:
            if evt == "start_warmup":
                ax2.axvline(ts, linestyle="--", alpha=0.7, color='green')
            elif evt == "crash_start":
                ax2.axvline(ts, linestyle="--", alpha=0.8, color='red', linewidth=2)
            elif evt == "end_observe":
                ax2.axvline(ts, linestyle="--", alpha=0.7, color='gray')
            elif evt == "recovery_start":
                ax2.axvline(ts, linestyle=":", alpha=0.8, color='purple', linewidth=2)
            elif evt == "recovery_complete":
                ax2.axvline(ts, linestyle=":", alpha=0.8, color='blue', linewidth=2)
    else:
        for (ts, evt, _) in events:
            if evt == "start_warmup":
                ax2.axvline(ts, linestyle=":", alpha=0.5, color='gray')
            elif evt == "end_observe":
                ax2.axvline(ts, linestyle=":", alpha=0.5, color='gray')
    
    ax2.set_ylabel('Mempool Size (bytes)', fontsize=11)
    ax2.set_xlabel('Time (UTC)', fontsize=11)
    ax2.set_title('Mempool Size in Bytes Over Time (Aggregated Across Nodes)', fontsize=12)
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    ax2.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
    plt.setp(ax2.xaxis.get_majorticklabels(), rotation=45)
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "mempool_analysis.png"), dpi=300, bbox_inches='tight')
    plt.close()

    print("✅ Created mempool_analysis.png")


def create_latency_cdf_plot(run_dir, events, df_conf):
    """
    Create a Stabl-style CDF comparison plot showing latency distributions
    for pre-crash, during-crash, and post-recovery phases.

    This visualization follows academic standards (Gramoli et al., 2024) for
    comparing system performance across fault injection phases.

    The CDF shows "what percentage of transactions were confirmed within X seconds"
    making it easy to compare entire distributions, not just summary statistics.
    """
    plots_dir = os.path.join(run_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)

    if df_conf.empty or not events:
        print("⚠️  Skipping CDF plot: no confirmation data or events")
        return None

    # Find phase boundaries from events
    crash_start = None
    recovery_complete = None
    observation_start = None
    observation_end = None

    for (ts, evt, _) in events:
        if evt == "crash_start" and crash_start is None:
            crash_start = ts
        elif evt == "recovery_complete" and recovery_complete is None:
            recovery_complete = ts
        elif evt in ("start_observation", "netem_applied") and observation_start is None:
            observation_start = ts
        elif evt == "end_observe" and observation_end is None:
            observation_end = ts

    # Ensure timestamps are timezone-aware
    def make_aware(ts):
        if ts is not None and hasattr(ts, 'tzinfo') and ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    crash_start = make_aware(crash_start)
    recovery_complete = make_aware(recovery_complete)
    observation_start = make_aware(observation_start)
    observation_end = make_aware(observation_end)

    # Filter to observation window if available
    df_plot = df_conf.copy()
    if observation_start and observation_end:
        df_plot = df_plot[
            (df_plot["confirm_ts_utc"] >= observation_start) &
            (df_plot["confirm_ts_utc"] <= observation_end)
        ]

    if df_plot.empty:
        print("⚠️  Skipping CDF plot: no data in observation window")
        return None

    # Determine if this is a fault injection run or baseline
    has_crash = crash_start is not None and recovery_complete is not None

    # Create figure with appropriate layout
    if has_crash:
        fig, (ax_main, ax_zoom) = plt.subplots(1, 2, figsize=(14, 6))
    else:
        fig, ax_main = plt.subplots(1, 1, figsize=(8, 6))
        ax_zoom = None

    # Color scheme - colorblind friendly
    colors = {
        'pre_crash': '#2ecc71',      # Green
        'during_crash': '#e74c3c',   # Red
        'post_recovery': '#3498db',  # Blue
        'baseline': '#9b59b6'        # Purple (for non-fault runs)
    }

    phase_data = {}

    if has_crash:
        # Split into phases
        df_pre = df_plot[df_plot["confirm_ts_utc"] < crash_start]
        df_during = df_plot[
            (df_plot["confirm_ts_utc"] >= crash_start) &
            (df_plot["confirm_ts_utc"] <= recovery_complete)
        ]
        df_post = df_plot[df_plot["confirm_ts_utc"] > recovery_complete]

        if not df_pre.empty:
            phase_data['pre_crash'] = {
                'latencies': np.sort(df_pre["latency_seconds"].values),
                'label': f'Pre-Crash (n={len(df_pre)})',
                'color': colors['pre_crash'],
                'linestyle': '-'
            }
        if not df_during.empty:
            phase_data['during_crash'] = {
                'latencies': np.sort(df_during["latency_seconds"].values),
                'label': f'During Crash (n={len(df_during)})',
                'color': colors['during_crash'],
                'linestyle': '-'
            }
        if not df_post.empty:
            phase_data['post_recovery'] = {
                'latencies': np.sort(df_post["latency_seconds"].values),
                'label': f'Post-Recovery (n={len(df_post)})',
                'color': colors['post_recovery'],
                'linestyle': '-'
            }
    else:
        # Baseline run - single distribution
        phase_data['baseline'] = {
            'latencies': np.sort(df_plot["latency_seconds"].values),
            'label': f'Baseline (n={len(df_plot)})',
            'color': colors['baseline'],
            'linestyle': '-'
        }

    if not phase_data:
        print("⚠️  Skipping CDF plot: no phase data available")
        plt.close()
        return None

    # Compute and plot CDFs
    cdf_data = {}
    max_latency = 0

    for phase_name, data in phase_data.items():
        latencies = data['latencies']
        n = len(latencies)
        cdf = np.arange(1, n + 1) / n

        # Store for sensitivity calculation
        cdf_data[phase_name] = {'latencies': latencies, 'cdf': cdf}
        max_latency = max(max_latency, latencies[-1] if len(latencies) > 0 else 0)

        # Plot on main axis
        ax_main.plot(latencies, cdf * 100,
                    color=data['color'],
                    linestyle=data['linestyle'],
                    linewidth=2.5,
                    label=data['label'],
                    alpha=0.9)

    # Style main plot
    ax_main.set_xlabel('Confirmation Latency (seconds)', fontsize=12)
    ax_main.set_ylabel('Cumulative Percentage (%)', fontsize=12)
    ax_main.set_ylim(0, 105)
    ax_main.set_xlim(0, min(max_latency * 1.1, np.percentile(df_plot["latency_seconds"], 99) * 1.5))
    ax_main.grid(True, alpha=0.3, linestyle='--')
    ax_main.legend(loc='lower right', fontsize=10, framealpha=0.9)

    # Add reference lines
    ax_main.axhline(50, color='gray', linestyle=':', alpha=0.5, linewidth=1)
    ax_main.axhline(95, color='gray', linestyle=':', alpha=0.5, linewidth=1)
    ax_main.text(ax_main.get_xlim()[1] * 0.02, 51, 'Median', fontsize=9, color='gray', alpha=0.7)
    ax_main.text(ax_main.get_xlim()[1] * 0.02, 96, 'P95', fontsize=9, color='gray', alpha=0.7)

    if has_crash:
        ax_main.set_title('Latency CDF by Phase (Full Range)', fontsize=13, fontweight='bold')
    else:
        ax_main.set_title('Latency CDF (Baseline)', fontsize=13, fontweight='bold')

    # Create zoomed view for fault runs (focus on median region)
    if ax_zoom is not None and has_crash:
        for phase_name, data in phase_data.items():
            latencies = cdf_data[phase_name]['latencies']
            cdf = cdf_data[phase_name]['cdf']
            ax_zoom.plot(latencies, cdf * 100,
                        color=phase_data[phase_name]['color'],
                        linestyle=phase_data[phase_name]['linestyle'],
                        linewidth=2.5,
                        label=phase_data[phase_name]['label'],
                        alpha=0.9)

        # Zoom to interesting region (20-80 percentile range)
        all_latencies = df_plot["latency_seconds"]
        p20 = np.percentile(all_latencies, 10)
        p80 = np.percentile(all_latencies, 90)

        ax_zoom.set_xlim(max(0, p20 * 0.5), p80 * 1.2)
        ax_zoom.set_ylim(0, 105)
        ax_zoom.set_xlabel('Confirmation Latency (seconds)', fontsize=12)
        ax_zoom.set_ylabel('Cumulative Percentage (%)', fontsize=12)
        ax_zoom.set_title('Latency CDF (Zoomed to P10-P90 Range)', fontsize=13, fontweight='bold')
        ax_zoom.grid(True, alpha=0.3, linestyle='--')
        ax_zoom.legend(loc='lower right', fontsize=10, framealpha=0.9)

        # Add reference lines
        ax_zoom.axhline(50, color='gray', linestyle=':', alpha=0.5, linewidth=1)
        ax_zoom.axhline(95, color='gray', linestyle=':', alpha=0.5, linewidth=1)

        # Shade area between pre-crash and during-crash CDFs (sensitivity visualization)
        if 'pre_crash' in cdf_data and 'during_crash' in cdf_data:
            pre_lat = cdf_data['pre_crash']['latencies']
            pre_cdf = cdf_data['pre_crash']['cdf']
            during_lat = cdf_data['during_crash']['latencies']
            during_cdf = cdf_data['during_crash']['cdf']

            # Interpolate to common x-axis for shading
            common_x = np.linspace(0, min(pre_lat[-1], during_lat[-1]), 200)
            pre_interp = np.interp(common_x, pre_lat, pre_cdf)
            during_interp = np.interp(common_x, during_lat, during_cdf)

            ax_zoom.fill_betweenx(
                pre_interp * 100,
                np.interp(pre_interp, pre_cdf, pre_lat),
                np.interp(pre_interp, during_cdf, during_lat),
                alpha=0.15,
                color='red',
                label='_Degradation Area'
            )

    # Add statistics annotation
    stats_text = []
    for phase_name in ['pre_crash', 'during_crash', 'post_recovery', 'baseline']:
        if phase_name in cdf_data:
            lat = cdf_data[phase_name]['latencies']
            median = np.median(lat)
            p95 = np.percentile(lat, 95)
            phase_label = phase_name.replace('_', ' ').title()
            stats_text.append(f"{phase_label}: median={median:.1f}s, P95={p95:.1f}s")

    # Add text box with statistics
    textstr = '\n'.join(stats_text)
    props = dict(boxstyle='round', facecolor='white', alpha=0.8, edgecolor='gray')
    ax_main.text(0.98, 0.02, textstr, transform=ax_main.transAxes, fontsize=9,
                verticalalignment='bottom', horizontalalignment='right', bbox=props)

    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "latency_cdf_comparison.png"), dpi=300, bbox_inches='tight')
    plt.close()

    print("✅ Created latency_cdf_comparison.png")

    # Return sensitivity metric if applicable
    if has_crash and 'pre_crash' in cdf_data and 'during_crash' in cdf_data:
        # Compute simplified sensitivity score (area between CDFs)
        pre_lat = cdf_data['pre_crash']['latencies']
        pre_cdf = cdf_data['pre_crash']['cdf']
        during_lat = cdf_data['during_crash']['latencies']
        during_cdf = cdf_data['during_crash']['cdf']

        # Interpolate to common percentile points
        percentiles = np.linspace(0.01, 0.99, 100)
        pre_quantiles = np.percentile(pre_lat, percentiles * 100)
        during_quantiles = np.percentile(during_lat, percentiles * 100)

        # Area = integral of |during - pre| over percentiles
        # This is similar to Stabl's sensitivity score
        sensitivity = np.trapz(np.abs(during_quantiles - pre_quantiles), percentiles)

        return {'sensitivity_score': round(sensitivity, 2)}

    return None


def main():
    parser = argparse.ArgumentParser(description="Enhanced Bitcoin fault analysis")
    parser.add_argument("--run-dir", help="Path to experiment run directory")
    parser.add_argument("--runs-dir", default="results", help="Path to results directory")
    args = parser.parse_args()
    
    if not args.run_dir:
        runs = [d for d in os.listdir(args.runs_dir) if os.path.isdir(os.path.join(args.runs_dir, d))]
        if not runs:
            print("No experiment runs found")
            return
        runs.sort(reverse=True)
        print(f"Available runs: {runs[:5]}")
        if len(runs) > 1:
            print("Using most recent run")
        args.run_dir = os.path.join(args.runs_dir, runs[0])
    
    run_dir = args.run_dir
    txlog = os.path.join(run_dir, "txlog.csv")
    conf = os.path.join(run_dir, "confirmations.csv")
    events = parse_events(os.path.join(run_dir, "events.log"))
    
    # Load metadata to get crash_fraction
    metadata_file = os.path.join(run_dir, "metadata.yml")
    exp_config = {}
    if os.path.exists(metadata_file):
        with open(metadata_file, 'r') as f:
            for line in f:
                if ':' in line:
                    key, val = line.strip().split(':', 1)
                    exp_config[key.strip()] = val.strip()
    
    crash_fraction = float(exp_config.get('crash_fraction', 0))
    
    # Load and clean data
    df_sub = pd.read_csv(txlog) if os.path.exists(txlog) else pd.DataFrame()
    if not df_sub.empty and "submit_ts_utc" in df_sub.columns:
        df_sub["submit_ts_utc"] = parse_iso8601(df_sub["submit_ts_utc"])
    
    # Load confirmations.csv - support both old and new column names
    df_conf = pd.DataFrame()
    if os.path.exists(conf):
        df_conf = pd.read_csv(conf)
        # Normalize column names: submit_time -> submit_ts_utc, confirm_time -> confirm_ts_utc
        if "submit_time" in df_conf.columns and "submit_ts_utc" not in df_conf.columns:
            df_conf["submit_ts_utc"] = parse_iso8601(df_conf["submit_time"])
        if "confirm_time" in df_conf.columns and "confirm_ts_utc" not in df_conf.columns:
            df_conf["confirm_ts_utc"] = parse_iso8601(df_conf["confirm_time"])
        # Parse dates if columns exist
        if "submit_ts_utc" in df_conf.columns:
            df_conf["submit_ts_utc"] = parse_iso8601(df_conf["submit_ts_utc"])
        if "confirm_ts_utc" in df_conf.columns:
            df_conf["confirm_ts_utc"] = parse_iso8601(df_conf["confirm_ts_utc"])
    
    df_conf = clean_confirmation_data(df_conf)
    
    conf_times = sorted(df_conf["confirm_ts_utc"].tolist()) if not df_conf.empty and "confirm_ts_utc" in df_conf.columns else []
    tps_series = rolling_rate(conf_times, 60) if conf_times else []

    if not df_conf.empty:
        cl = df_conf["latency_seconds"].astype(float)
        cl50 = float(np.percentile(cl, 50))
        cl95 = float(np.percentile(cl, 95))
    else:
        cl50 = cl95 = float("nan")
    
    # Extract experiment time window from events
    # SCIENTIFIC FIX: Use netem_applied as start (when network conditions are active)
    # This includes warmup data under degraded conditions, which is scientifically valuable
    start_experiment = None
    observation_start = None
    end_observe = None
    for (ts, evt, _) in events:
        if evt == "netem_applied" and start_experiment is None:
            start_experiment = ts  # Start when NetEm is active
        elif evt in ("start_observation", "after_netem") and observation_start is None:
            observation_start = ts
            # Fallback: If netem_applied not found (old runs), use observation start
            if start_experiment is None:
                start_experiment = ts
        elif evt == "end_observe" and end_observe is None:
            end_observe = ts
    
    # Final fallback: If no netem events found, use start_warmup (for baseline runs without NetEm)
    if start_experiment is None:
        for (ts, evt, _) in events:
            if evt == "start_warmup":
                start_experiment = ts
                break
    
    # Filter submissions to experiment window only (exclude pre-funding, warmup, cooldown, etc.)
    # IMPORTANT: We apply a confirmation buffer to exclude TXs submitted too close to the end
    # of observation. These TXs may not have had time to confirm, which would unfairly lower
    # availability metrics. The buffer ensures we only count TXs that had a fair chance to confirm.
    # Under fault conditions, p99 latency can be ~140s and max ~185s, so we use 180s buffer.
    CONFIRMATION_BUFFER_SECONDS = 180

    df_sub_filtered = df_sub.copy()
    if not df_sub_filtered.empty and "submit_ts_utc" in df_sub_filtered.columns:
        if start_experiment is not None:
            df_sub_filtered = df_sub_filtered[df_sub_filtered["submit_ts_utc"] >= start_experiment]
        if end_observe is not None:
            # Apply confirmation buffer: only count TXs submitted before (end_observe - buffer)
            # This ensures every counted TX had time to confirm during cooldown
            submission_cutoff = end_observe - pd.Timedelta(seconds=CONFIRMATION_BUFFER_SECONDS)
            df_sub_filtered = df_sub_filtered[df_sub_filtered["submit_ts_utc"] <= submission_cutoff]

    # Filter confirmations to experiment window only (exclude pre-funding, warmup, etc.)
    # IMPORTANT: We filter by SUBMIT time (not confirm time) to include confirmations
    # that happen during cooldown for transactions submitted during observation.
    # This gives a fair availability metric that accounts for variable block times.
    df_conf_filtered = df_conf.copy()
    if not df_conf_filtered.empty:
        if start_experiment is not None and "submit_ts_utc" in df_conf_filtered.columns:
            df_conf_filtered = df_conf_filtered[df_conf_filtered["submit_ts_utc"] >= start_experiment]
        if end_observe is not None and "submit_ts_utc" in df_conf_filtered.columns:
            # Use same cutoff as submissions for consistency
            submission_cutoff = end_observe - pd.Timedelta(seconds=CONFIRMATION_BUFFER_SECONDS)
            df_conf_filtered = df_conf_filtered[df_conf_filtered["submit_ts_utc"] <= submission_cutoff]
        # Also filter out confirmations before experiment start (safety check)
        if start_experiment is not None and "confirm_ts_utc" in df_conf_filtered.columns:
            df_conf_filtered = df_conf_filtered[df_conf_filtered["confirm_ts_utc"] >= start_experiment]
    
    # Calculate availability using filtered data (only observation window)
    # IMPORTANT: We already filtered confirmations by submit_ts_utc (not confirm_ts_utc),
    # so we can safely compare counts. Using compute_availability would filter
    # confirmed_times by time range again, which would exclude cooldown confirmations.
    # Instead, we directly compare the counts since both lists are already correctly filtered.
    sub_times_filtered = sorted(df_sub_filtered["submit_ts_utc"].tolist()) if not df_sub_filtered.empty else []
    conf_times_filtered = sorted(df_conf_filtered["confirm_ts_utc"].tolist()) if not df_conf_filtered.empty else []
    
    # Direct count comparison (prevents >100% availability since we only count
    # confirmations of transactions submitted during observation)
    if len(sub_times_filtered) == 0:
        A = 0.0
    else:
        # Ensure we don't count more confirmations than submissions
        # (can happen if same txid appears multiple times in confirmations due to reorgs)
        # Use unique txid count to prevent double-counting from reorgs
        if not df_conf_filtered.empty and "txid" in df_conf_filtered.columns:
            confirmed_count = len(set(df_conf_filtered["txid"].tolist()))
        else:
            # Fallback: use count if txid column missing
            confirmed_count = len(conf_times_filtered)
        
        A = min(1.0, confirmed_count / len(sub_times_filtered))  # Cap at 100%
    
    # Calculate average throughput over the measurement window
    # IMPORTANT: Use same time window as filtered TXs for consistency
    # Window: start_experiment → (end_observe - CONFIRMATION_BUFFER)
    avg_throughput_official = 0.0
    if not df_conf_filtered.empty and len(df_conf_filtered) > 0:
        if start_experiment is not None and end_observe is not None:
            # Time window must match TX filter (with confirmation buffer applied)
            end_ts_with_buffer = end_observe - pd.Timedelta(seconds=CONFIRMATION_BUFFER_SECONDS)
            total_time = (end_ts_with_buffer - start_experiment).total_seconds()
            if total_time > 0:
                avg_throughput_official = len(df_conf_filtered) / total_time
    
    # Create enhanced plots
    create_enhanced_plots(run_dir, events, df_sub, df_conf, tps_series, avg_throughput_official)
    
    # Create throughput comparison plot (new - addresses rolling window artifacts)
    # REMOVED: throughput_comparison plot (redundant, confusing for thesis)
    # The performance_timeline.png now shows clean 30s binned throughput
    
    # Create mempool analysis plot
    create_mempool_plot(run_dir, events)

    # Create latency CDF comparison plot (Stabl-style)
    cdf_result = create_latency_cdf_plot(run_dir, events, df_conf)

    # Calculate latency metrics from filtered confirmations (only observation window)
    if not df_conf_filtered.empty:
        cl_filtered = df_conf_filtered["latency_seconds"].astype(float)
        cl50 = float(np.percentile(cl_filtered, 50))
        cl95 = float(np.percentile(cl_filtered, 95))
        cl99 = float(np.percentile(cl_filtered, 99))
        cl_mean = float(np.mean(cl_filtered))
        cl_min = float(np.min(cl_filtered))
        cl_max = float(np.max(cl_filtered))
    else:
        cl50 = cl95 = cl99 = cl_mean = cl_min = cl_max = float("nan")

    # Calculate k=6 confirmation latency (time from submit to 6 confirmations deep)
    k6_median = k6_p95 = float("nan")
    mining_file = os.path.join(run_dir, "mining.csv")
    if not df_conf_filtered.empty and os.path.exists(mining_file):
        try:
            df_mining = pd.read_csv(mining_file)
            df_mining['timestamp_utc'] = pd.to_datetime(df_mining['timestamp_utc'])
            # Create lookups: block_hash -> block_number, block_number -> timestamp
            hash_to_num = dict(zip(df_mining['block_hash'], df_mining['block_number']))
            num_to_time = dict(zip(df_mining['block_number'], df_mining['timestamp_utc']))
            max_block = df_mining['block_number'].max()

            k6_latencies = []
            for _, row in df_conf_filtered.iterrows():
                confirm_hash = row.get('confirm_block_hash')
                if pd.notna(confirm_hash) and confirm_hash in hash_to_num:
                    k1_num = hash_to_num[confirm_hash]
                    k6_num = k1_num + 5
                    if k6_num in num_to_time:
                        submit_ts = row['submit_ts_utc']
                        if isinstance(submit_ts, str):
                            submit_ts = pd.to_datetime(submit_ts)
                        k6_ts = num_to_time[k6_num]
                        k6_lat = (k6_ts - submit_ts).total_seconds()
                        if k6_lat > 0:
                            k6_latencies.append(k6_lat)

            if k6_latencies:
                k6_median = float(np.percentile(k6_latencies, 50))
                k6_p95 = float(np.percentile(k6_latencies, 95))
                print(f"\n🔒 K=6 CONFIRMATION LATENCY:")
                print(f"   Samples: {len(k6_latencies)} / {len(df_conf_filtered)} ({100*len(k6_latencies)/len(df_conf_filtered):.1f}%)")
                print(f"   Median: {k6_median:.2f}s, P95: {k6_p95:.2f}s")
        except Exception as e:
            print(f"⚠️  Could not calculate k=6 latency: {e}")

    # Calculate metrics (using filtered data - only observation window)
    metrics = {
        "run_dir": run_dir,
        "total_submitted": len(df_sub_filtered),  # Only transactions in observation window
        "total_confirmed": len(df_conf_filtered),  # Only confirmations in observation window
        "availability": A,
        "confirmation_latency_stats": {
            "min": cl_min,
            "mean": cl_mean,
            "median": cl50,
            "p95": cl95,
            "p99": cl99,
            "max": cl_max
        },
        # k6 = 6-confirmation latency (time until TX has 6 confirmations, Bitcoin's standard finality)
        "k6_confirmation_latency": {
            "median": k6_median,
            "p95": k6_p95
        } if k6_median is not None else None,
        "avg_throughput": avg_throughput_official  # Official Bitcoin network throughput definition
    }

    # Add sensitivity score from CDF analysis (Stabl-style metric)
    if cdf_result and 'sensitivity_score' in cdf_result:
        metrics["latency_sensitivity_score"] = cdf_result['sensitivity_score']

    block_propagation_metrics = compute_block_propagation_metrics(run_dir, events=events)
    reorg_events_from_logs = []  # Will be populated from block_propagation if available
    if block_propagation_metrics:
        # Extract reorg events (internal field, not saved to JSON)
        reorg_events_from_logs = block_propagation_metrics.pop("_reorg_events", [])
        metrics["block_propagation"] = block_propagation_metrics
        
        # Print block propagation summary with separation info
        print(f"\n📡 BLOCK PROPAGATION:")
        
        # Show separated metrics if available
        online = block_propagation_metrics.get("online_nodes")
        if online:
            print(f"   Online nodes (P2P): mean={online.get('mean_seconds', 0):.2f}s, p95={online.get('p95_seconds', 0):.2f}s, max={online.get('max_seconds', 0):.2f}s ({online.get('total_samples', 0)} samples)")
        
        recovery_sync = block_propagation_metrics.get("recovery_sync")
        if recovery_sync:
            print(f"   Recovery sync: mean={recovery_sync.get('mean_seconds', 0):.2f}s, max={recovery_sync.get('max_seconds', 0):.2f}s ({recovery_sync.get('total_samples', 0)} samples, {recovery_sync.get('blocks_during_outage', 0)} blocks during outage)")

        partition_resync = block_propagation_metrics.get("partition_resync")
        if partition_resync:
            print(f"   Partition resync: mean={partition_resync.get('mean_seconds', 0):.2f}s, max={partition_resync.get('max_seconds', 0):.2f}s ({partition_resync.get('total_samples', 0)} samples)")

    # Prefer events-based recovery (Option 1); fall back to latency-based if missing
    events_recovery = compute_event_recovery_metrics(run_dir, events)
    if events_recovery:
        metrics["recovery_analysis"] = events_recovery
        durations = events_recovery.get("durations_seconds", {})
        # Promote wall_clock_recovery_time to top-level for discoverability
        if "wall_clock_recovery_time" in durations:
            metrics["wall_clock_recovery_time_seconds"] = durations["wall_clock_recovery_time"]
        # Keep block_catchup_time only inside recovery_analysis to avoid duplicate top-level metrics
        print(f"\n🔄 RECOVERY ANALYSIS (events-based):")
        print(f"   Crashed nodes: {events_recovery.get('crashed_nodes_count', 0)}")
        if "node_downtime" in durations:
            print(f"   Node downtime: {durations['node_downtime']:.0f}s ({durations['node_downtime']/60:.1f} min)")
        if "crash_injection_time" in durations:
            print(f"   Crash injection time: {durations['crash_injection_time']:.0f}s")
        if "total_downtime" in durations:
            print(f"   Total downtime: {durations['total_downtime']:.0f}s ({durations['total_downtime']/60:.1f} min)")
        if "wall_clock_recovery_time" in durations:
            print(f"   Wall-clock recovery time: {durations['wall_clock_recovery_time']:.0f}s")
        if "restart_time" in durations:
            print(f"   Restart time: {durations['restart_time']:.0f}s")
        if "block_catchup_time" in durations:
            print(f"   Block catchup time: {durations['block_catchup_time']:.0f}s")
    else:
        # Add recovery detection metrics (pass crash_fraction to skip for baseline tests)
        recovery_metrics = detect_recovery_completion(df_conf, events, crash_fraction=crash_fraction)
        if recovery_metrics:
            metrics["recovery_analysis"] = recovery_metrics
            
            if recovery_metrics.get("recovery_detected"):
                print(f"\n🔄 RECOVERY DETECTED:")
                print(f"   Baseline latency: {recovery_metrics['baseline_latency']:.2f}s")
                print(f"   Peak latency: {recovery_metrics['peak_latency_during_recovery']:.2f}s")
                print(f"   Degradation: {recovery_metrics['latency_degradation_pct']:.1f}%")
                print(f"   Recovery time: {recovery_metrics['recovery_time_seconds']:.0f}s ({recovery_metrics['recovery_time_seconds']/60:.1f} min)")
            else:
                print(f"\n⚠️  RECOVERY NOT COMPLETE:")
                print(f"   Baseline latency: {recovery_metrics['baseline_latency']:.2f}s")
                print(f"   Current latency: {recovery_metrics['final_latency']:.2f}s")
                print(f"   Observation duration: {recovery_metrics['observation_duration']:.0f}s")
    
    # Compute network fault (netem) metrics
    network_fault_metrics = compute_network_fault_metrics(events)
    if network_fault_metrics:
        metrics["network_fault"] = network_fault_metrics
        params = network_fault_metrics.get("parameters", {})
        fraction = network_fault_metrics.get("fraction", 1.0)
        print(f"\n🌐 NETWORK FAULT:")
        print(f"   Latency: {params.get('latency_ms', 0):.0f}ms, Loss: {params.get('loss_pct', 0):.0f}%, Jitter: {params.get('jitter_ms', 0):.0f}ms")
        if "duration_seconds" in network_fault_metrics:
            print(f"   Duration: {network_fault_metrics['duration_seconds']:.0f}s")
        if fraction < 1.0:
            affected_count = network_fault_metrics.get("affected_count", 0)
            print(f"   Partial: {fraction*100:.0f}% of nodes ({affected_count} nodes affected)")
            if network_fault_metrics.get("affected_nodes"):
                nodes_preview = ", ".join(network_fault_metrics["affected_nodes"][:5])
                if affected_count > 5:
                    nodes_preview += f"... (+{affected_count - 5} more)"
                print(f"   Affected: {nodes_preview}")
        else:
            print(f"   Scope: All nodes")

    # Compute latency comparison (pre-crash vs during-crash vs post-recovery)
    latency_comparison = compute_latency_comparison(df_conf_filtered, events)
    if latency_comparison:
        metrics["confirmation_latency_comparison"] = latency_comparison
        print(f"\n📈 CONFIRMATION LATENCY COMPARISON:")
        print(f"   Pre-crash median: {latency_comparison['pre_crash_median']:.2f}s ({latency_comparison['pre_crash_samples']} samples)")
        if 'during_crash_median' in latency_comparison:
            during_deg = latency_comparison.get('during_crash_degradation_percent', 0)
            status = "⚠️" if during_deg > 0 else "✅"
            print(f"   During crash median: {latency_comparison['during_crash_median']:.2f}s ({latency_comparison['during_crash_samples']} samples) {status} {during_deg:+.1f}%")
        print(f"   Post-recovery median: {latency_comparison['post_recovery_median']:.2f}s ({latency_comparison['post_recovery_samples']} samples)")
        if latency_comparison['degradation_percent'] > 0:
            print(f"   ⚠️  Post-recovery degradation: +{latency_comparison['degradation_percent']:.1f}%")
        else:
            print(f"   ✅ Post-recovery improvement: {latency_comparison['degradation_percent']:.1f}%")
    
    # Compute block interval statistics
    block_interval_stats = compute_block_interval_stats(run_dir, events)
    if block_interval_stats:
        metrics["block_interval_stats"] = block_interval_stats
        print(f"\n⛏️  BLOCK INTERVALS:")
        print(f"   Blocks mined: {block_interval_stats['blocks_mined']}")
        print(f"   Mean interval: {block_interval_stats['mean_seconds']:.1f}s ± {block_interval_stats['std_seconds']:.1f}s")
        print(f"   Range: {block_interval_stats['min_seconds']:.1f}s - {block_interval_stats['max_seconds']:.1f}s")

    # Compute per-phase throughput (pre-crash, during-crash, post-recovery)
    # Uses submission time for phase assignment to avoid confirmation lag artifacts
    # IMPORTANT: For throughput analysis, we DON'T apply the confirmation buffer
    # because we want to measure all TXs submitted in each phase (confirmation_rate handles unconfirmed TXs)
    df_sub_for_throughput = df_sub.copy()
    df_conf_for_throughput = df_conf.copy()
    if not df_sub_for_throughput.empty and "submit_ts_utc" in df_sub_for_throughput.columns:
        if start_experiment is not None:
            df_sub_for_throughput = df_sub_for_throughput[df_sub_for_throughput["submit_ts_utc"] >= start_experiment]
        if end_observe is not None:
            df_sub_for_throughput = df_sub_for_throughput[df_sub_for_throughput["submit_ts_utc"] <= end_observe]
    if not df_conf_for_throughput.empty and "submit_ts_utc" in df_conf_for_throughput.columns:
        if start_experiment is not None:
            df_conf_for_throughput = df_conf_for_throughput[df_conf_for_throughput["submit_ts_utc"] >= start_experiment]
        if end_observe is not None:
            df_conf_for_throughput = df_conf_for_throughput[df_conf_for_throughput["submit_ts_utc"] <= end_observe]

    # Confirmation buffer is applied inside compute_per_phase_throughput for post_recovery phase
    # This ensures TXs have enough time to confirm before being counted in confirmation_rate
    per_phase_throughput = compute_per_phase_throughput(
        df_conf_for_throughput, df_sub_for_throughput, events,
        confirmation_buffer_s=CONFIRMATION_BUFFER_SECONDS
    )
    if per_phase_throughput:
        metrics["throughput_by_phase"] = per_phase_throughput
        print(f"\n📊 THROUGHPUT BY PHASE:")
        for phase_name, phase_label in [("pre_crash", "Pre-crash"), ("during_crash", "During crash"), ("post_recovery", "Post-recovery")]:
            if phase_name in per_phase_throughput:
                p = per_phase_throughput[phase_name]
                submitted = p.get('submitted_count', 0)
                confirmed_of_submitted = p.get('confirmed_count', 0)
                confirmations_in_phase = p.get('confirmations_in_phase', 0)
                rate = p.get('confirmation_rate', 0)
                latency = p.get('median_confirmation_latency_seconds', 0)
                duration = p.get('duration_seconds', 0)
                conf_tps = p.get('confirmation_throughput_tx_per_s', 0)

                rate_str = f", avail={rate*100:.1f}%" if rate else ""
                latency_str = f", latency={latency:.1f}s" if latency else ""
                print(f"   {phase_label}: {conf_tps:.2f} tx/s confirmed ({confirmations_in_phase} conf in {duration:.0f}s{rate_str}{latency_str})")

        if "throughput_degradation_percent" in per_phase_throughput:
            deg = per_phase_throughput["throughput_degradation_percent"]
            status = "⚠️" if deg > 5 else "✅"
            print(f"   {status} Throughput degradation during crash: {deg:+.1f}%")
        if "recovery_vs_baseline_percent" in per_phase_throughput:
            rec = per_phase_throughput["recovery_vs_baseline_percent"]
            status = "✅" if rec >= -5 else "⚠️"
            print(f"   {status} Recovery throughput vs baseline: {rec:+.1f}%")

    # Compute reorg and fork metrics (uses reorg events from block_propagation - no extra log parsing)
    reorg_metrics = compute_reorg_metrics(run_dir, events, reorg_events_from_propagation=reorg_events_from_logs)
    if reorg_metrics:
        metrics["chain_consensus"] = reorg_metrics
        print(f"\n🔗 CHAIN CONSENSUS:")
        print(f"   Active chain tips: {reorg_metrics.get('active_tips', 1)}")
        print(f"   Orphaned blocks (getchaintips): {reorg_metrics.get('orphaned_blocks_total', 0)}")
        print(f"   Reorg events: {reorg_metrics.get('reorg_count', 0)}")
        if reorg_metrics.get('forks_detected'):
            print(f"   Forks detected (from reorgs): {reorg_metrics['forks_detected']}")
        if reorg_metrics.get('reorg_network_wide'):
            print(f"   Network-wide reorgs: {reorg_metrics['reorg_network_wide']}")
        if reorg_metrics.get('max_reorg_propagation_seconds') is not None:
            print(f"   Max reorg propagation: {reorg_metrics['max_reorg_propagation_seconds']:.1f}s")
        if reorg_metrics.get('mean_reorg_propagation_seconds') is not None:
            print(f"   Mean reorg propagation: {reorg_metrics['mean_reorg_propagation_seconds']:.1f}s")
        if reorg_metrics.get('fork_details'):
            for fork in reorg_metrics['fork_details'][:3]:
                print(f"   └─ {fork['type']} at height {fork['height']} ({fork['blocks_behind']} blocks behind)")
        if reorg_metrics.get('reorg_propagation'):
            print(f"   Reorg propagation by height:")
            for rp in reorg_metrics['reorg_propagation'][:5]:
                print(f"      └─ height {rp['height']}: {rp['propagation_seconds']:.1f}s ({rp['nodes_affected']} nodes)")

    # Compute chain split metrics (periods where nodes disagree on current tip)
    chain_split_metrics = compute_chain_split_metrics(run_dir)
    if chain_split_metrics:
        # Add to chain_consensus or create new section
        if "chain_consensus" not in metrics:
            metrics["chain_consensus"] = {}
        metrics["chain_consensus"]["chain_splits"] = chain_split_metrics

        if chain_split_metrics["total_splits"] > 0:
            print(f"\n⚠️  CHAIN SPLITS DETECTED:")
            print(f"   Total split events: {chain_split_metrics['total_splits']}")
            print(f"   Max duration: {chain_split_metrics['max_duration_seconds']:.2f}s")
            print(f"   Max nodes diverged: {chain_split_metrics['max_nodes_diverged']}")
            print(f"   Total divergence time: {chain_split_metrics['total_divergence_seconds']:.2f}s")
            if chain_split_metrics.get('splits'):
                print(f"   Significant splits (by duration):")
                for split in chain_split_metrics['splits'][:5]:
                    unresolved = " (UNRESOLVED)" if split.get('unresolved') else ""
                    heights = ", ".join(str(h) for h in split['heights_involved'][:3])
                    if len(split['heights_involved']) > 3:
                        heights += f"... (+{len(split['heights_involved'])-3} more)"
                    print(f"      └─ {split['duration_seconds']:.2f}s, {split['max_nodes_diverged']} nodes, heights: {heights}{unresolved}")
        else:
            print(f"\n✅ NO CHAIN SPLITS: Network maintained consensus throughout")

        # Show final consensus state
        final_state = chain_split_metrics.get("final_state", {})
        if final_state:
            nodes_checked = final_state.get("nodes_checked", 0)
            if final_state.get("consensus_reached"):
                height = final_state.get("final_height", "?")
                print(f"   ✅ Final consensus: All {nodes_checked} nodes at height {height}")
            else:
                unique_tips = final_state.get("unique_tips", 0)
                print(f"   ⚠️  FINAL DIVERGENCE: {unique_tips} different tips across {nodes_checked} nodes")
                for tip in final_state.get("divergent_tips", [])[:3]:
                    print(f"      └─ height {tip['height']}: {tip['node_count']} nodes")

    # Calculate stale_block_rate (academic standard metric: forks / total_blocks)
    # This uses forks_detected from reorg events (authoritative) rather than getchaintips
    if "chain_consensus" in metrics and "block_interval_stats" in metrics:
        forks = metrics["chain_consensus"].get("forks_detected", 0)
        blocks_mined = metrics["block_interval_stats"].get("blocks_mined", 0)
        if blocks_mined > 0:
            stale_rate = forks / blocks_mined
            metrics["chain_consensus"]["stale_block_rate"] = round(stale_rate, 6)
            metrics["chain_consensus"]["stale_block_rate_pct"] = round(stale_rate * 100, 3)
            if forks > 0:
                print(f"   📊 Stale block rate: {stale_rate*100:.2f}% ({forks} forks / {blocks_mined} blocks)")

    # Save metrics
    with open(os.path.join(run_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    
    print(f"\n📊 Enhanced analysis complete for {run_dir}")
    print(f"Submitted: {metrics['total_submitted']}, Confirmed: {metrics['total_confirmed']}")
    print(f"Availability: {metrics['availability']:.2%}")
    print(f"Median Confirmation Latency: {metrics['confirmation_latency_stats']['median']:.2f}s")
    print(f"P95 Confirmation Latency: {metrics['confirmation_latency_stats']['p95']:.2f}s")
    print(f"Avg Throughput: {metrics['avg_throughput']:.2f} tx/s")

if __name__ == "__main__":
    main()
