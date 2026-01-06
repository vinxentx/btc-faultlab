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
import re
from datetime import datetime, timezone
from collections import deque

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
    r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+UpdateTip:\s+new\s+best=([a-f0-9]+)\s+height=(\d+)\s+.*?\btx=(\d+)\s+date=\'([^\']+)\''
)

def parse_iso8601(values):
    """Robust ISO8601 parser that tolerates missing microseconds."""
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
                            events.append((datetime.fromisoformat(ts.replace("Z", "+00:00")), evt, rest))
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

def compute_event_recovery_metrics(run_dir, events):
    """Compute comprehensive recovery metrics from events.log.
    
    Parses all crash/recovery related events and computes scientifically
    accurate timing metrics:
    - crash_duration: time from crash_start to crash_complete
    - total_downtime: time from crash_start to recovery_complete
    - recovery_time: time from crash_complete to recovery_complete
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

    # crash_duration: crash_start → crash_complete
    if "crash_start" in timestamps and "crash_complete" in timestamps:
        durations["crash_duration"] = (timestamps["crash_complete"] - timestamps["crash_start"]).total_seconds()
    
    # total_downtime: crash_start → recovery_complete (most important metric!)
    if "crash_start" in timestamps:
        durations["total_downtime"] = (timestamps["recovery_complete"] - timestamps["crash_start"]).total_seconds()
    
    # recovery_time: crash_complete → recovery_complete
    if "crash_complete" in timestamps:
        durations["recovery_time"] = (timestamps["recovery_complete"] - timestamps["crash_complete"]).total_seconds()
    
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
    Compare latency before crash vs after recovery.
    
    Scientific use: Proves whether the system truly recovered to baseline performance
    or if there's residual degradation.
    
    Returns:
        dict with pre_crash_median, post_recovery_median, degradation_percent
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
    
    # Split confirmations into pre-crash and post-recovery
    df_pre = df_conf[df_conf["confirm_ts_utc"] < crash_start].copy()
    df_post = df_conf[df_conf["confirm_ts_utc"] > recovery_complete].copy()
    
    if df_pre.empty or df_post.empty:
        return None
    
    pre_median = float(df_pre["latency_seconds"].median())
    post_median = float(df_post["latency_seconds"].median())
    
    # Calculate degradation (positive = worse after recovery)
    if pre_median > 0:
        degradation_pct = ((post_median - pre_median) / pre_median) * 100
    else:
        degradation_pct = 0.0
    
    return {
        "pre_crash_median": round(pre_median, 3),
        "post_recovery_median": round(post_median, 3),
        "degradation_percent": round(degradation_pct, 1),
        "pre_crash_samples": len(df_pre),
        "post_recovery_samples": len(df_post)
    }

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
        
        df_conf["confirm_ts_utc"] = pd.to_datetime(df_conf["confirm_ts_utc"])
        
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
    seen: dict[tuple[str, str], tuple[float, "pd.Timestamp", "pd.Timestamp"]] = {}
    per_block_node_delay: dict[str, dict[str, float]] = {}
    
    for log_path in log_files:
        node_name = log_path.stem
        
        try:
            with open(log_path, "r", encoding="utf-8") as handle:
                for line in handle:
                    match = UPDATE_TIP_REGEX.search(line)
                    if not match:
                        continue
                    
                    arrival_ts = pd.to_datetime(match.group(1).replace("Z", "+00:00"))
                    block_hash = match.group(2)
                    
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
        "Sync delay for crashed nodes catching up after recovery"
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
        df_conf_plot["confirm_ts_utc"] = pd.to_datetime(df_conf_plot["confirm_ts_utc"])
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
        df_sub_copy["submit_ts_utc"] = pd.to_datetime(df_sub_copy["submit_ts_utc"])
        df_conf_copy["confirm_ts_utc"] = pd.to_datetime(df_conf_copy["confirm_ts_utc"])
        
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

        # Use same filtering as metrics.json (netem_applied as start, with 60s buffer)
        CONFIRMATION_BUFFER_SECONDS = 60
        start_ts = netem_applied_ts or observation_start_ts
        end_ts_with_buffer = end_observe_ts - pd.Timedelta(seconds=CONFIRMATION_BUFFER_SECONDS) if end_observe_ts else None

        # Filter SUBMISSIONS
        df_sub_obs = df_sub.copy()
        df_sub_obs["submit_ts_utc"] = pd.to_datetime(df_sub_obs["submit_ts_utc"])
        if start_ts is not None:
            df_sub_obs = df_sub_obs[df_sub_obs["submit_ts_utc"] >= start_ts]
        if end_ts_with_buffer is not None:
            df_sub_obs = df_sub_obs[df_sub_obs["submit_ts_utc"] <= end_ts_with_buffer]

        # Filter CONFIRMATIONS by their SUBMIT time (same as metrics.json)
        df_conf_obs = df_conf.copy()
        if "submit_ts_utc" in df_conf_obs.columns:
            df_conf_obs["submit_ts_utc"] = pd.to_datetime(df_conf_obs["submit_ts_utc"])
            if start_ts is not None:
                df_conf_obs = df_conf_obs[df_conf_obs["submit_ts_utc"] >= start_ts]
            if end_ts_with_buffer is not None:
                df_conf_obs = df_conf_obs[df_conf_obs["submit_ts_utc"] <= end_ts_with_buffer]
        else:
            # Fallback: filter by confirm time if submit time not available
            df_conf_obs["confirm_ts_utc"] = pd.to_datetime(df_conf_obs["confirm_ts_utc"])
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
        df_sub_sorted["submit_ts_utc"] = pd.to_datetime(df_sub_sorted["submit_ts_utc"])
        df_conf_sorted["confirm_ts_utc"] = pd.to_datetime(df_conf_sorted["confirm_ts_utc"])
        
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
    after_netem_ts = None
    end_observe_ts = None
    for (ts, evt, _) in events:
        if evt in ("start_observation", "after_netem") and after_netem_ts is None:
            after_netem_ts = ts
        elif evt == "end_observe" and end_observe_ts is None:
            end_observe_ts = ts
    
    # Filter dataframes to observation window
    df_sub_filtered = df_sub.copy() if not df_sub.empty else pd.DataFrame()
    df_conf_filtered = df_conf.copy() if not df_conf.empty else pd.DataFrame()
    
    if not df_sub_filtered.empty and after_netem_ts is not None:
        df_sub_filtered["submit_ts_utc"] = pd.to_datetime(df_sub_filtered["submit_ts_utc"])
        df_sub_filtered = df_sub_filtered[df_sub_filtered["submit_ts_utc"] >= after_netem_ts]
        if end_observe_ts is not None:
            df_sub_filtered = df_sub_filtered[df_sub_filtered["submit_ts_utc"] <= end_observe_ts]
    
    if not df_conf_filtered.empty and after_netem_ts is not None:
        df_conf_filtered["confirm_ts_utc"] = pd.to_datetime(df_conf_filtered["confirm_ts_utc"])
        df_conf_filtered = df_conf_filtered[df_conf_filtered["confirm_ts_utc"] >= after_netem_ts]
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
    CONFIRMATION_BUFFER_SECONDS = 60  # Time needed for TX to get into a block and confirm

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
    
    # Calculate official Bitcoin network throughput
    # Official definition: Total confirmed transactions / Total time (experiment window)
    avg_throughput_official = 0.0
    if not df_conf_filtered.empty and len(df_conf_filtered) > 1:
        # Use experiment window time if available, otherwise use first to last confirmation
        if start_experiment is not None and end_observe is not None:
            total_time = (end_observe - start_experiment).total_seconds()
        else:
            # Use already-computed conf_times_filtered (from line above) for consistency
            total_time = (conf_times_filtered[-1] - conf_times_filtered[0]).total_seconds() if conf_times_filtered else 0
        
        if total_time > 0:
            avg_throughput_official = len(df_conf_filtered) / total_time
        
        # Alternative: Use mining.csv if available for block-based calculation
        mining_file = os.path.join(run_dir, "mining.csv")
        if os.path.exists(mining_file):
            try:
                df_mining = pd.read_csv(mining_file)
                if "timestamp_utc" in df_mining.columns and len(df_mining) > 1:
                    df_mining["timestamp_utc"] = pd.to_datetime(df_mining["timestamp_utc"])
                    # Filter mining blocks to experiment window
                    if start_experiment is not None:
                        df_mining = df_mining[df_mining["timestamp_utc"] >= start_experiment]
                    if end_observe is not None:
                        df_mining = df_mining[df_mining["timestamp_utc"] <= end_observe]
                    
                    if len(df_mining) > 1:
                        mining_times = sorted(df_mining["timestamp_utc"].tolist())
                        block_time_span = (mining_times[-1] - mining_times[0]).total_seconds()
                        if block_time_span > 0:
                            # Method 2: Average TX per block × Blocks per second
                            tx_per_block = len(df_conf_filtered) / len(df_mining)
                            blocks_per_second = len(df_mining) / block_time_span
                            avg_throughput_block_based = tx_per_block * blocks_per_second
                            # Use block-based if it's more accurate (covers full experiment duration)
                            if block_time_span > total_time * 0.8:  # If block span covers most of experiment
                                avg_throughput_official = avg_throughput_block_based
            except Exception as e:
                print(f"⚠️  Could not read mining.csv for throughput calculation: {e}")
    
    # Create enhanced plots
    create_enhanced_plots(run_dir, events, df_sub, df_conf, tps_series, avg_throughput_official)
    
    # Create throughput comparison plot (new - addresses rolling window artifacts)
    # REMOVED: throughput_comparison plot (redundant, confusing for thesis)
    # The performance_timeline.png now shows clean 30s binned throughput
    
    # Create mempool analysis plot
    create_mempool_plot(run_dir, events)
    
    # Calculate latency metrics from filtered confirmations (only observation window)
    if not df_conf_filtered.empty:
        cl_filtered = df_conf_filtered["latency_seconds"].astype(float)
        cl50 = float(np.percentile(cl_filtered, 50))
        cl95 = float(np.percentile(cl_filtered, 95))
    else:
        cl50 = cl95 = float("nan")

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
        "median_latency": cl50,
        "p95_latency": cl95,
        "k6_median_latency": k6_median,
        "k6_p95_latency": k6_p95,
        "avg_throughput": avg_throughput_official  # Official Bitcoin network throughput definition
    }
    
    block_propagation_metrics = compute_block_propagation_metrics(run_dir, events=events)
    if block_propagation_metrics:
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
        print(f"\n🔄 RECOVERY ANALYSIS (events-based):")
        print(f"   Crashed nodes: {events_recovery.get('crashed_nodes_count', 0)}")
        if "crash_duration" in durations:
            print(f"   Crash duration: {durations['crash_duration']:.0f}s")
        if "total_downtime" in durations:
            print(f"   Total downtime: {durations['total_downtime']:.0f}s ({durations['total_downtime']/60:.1f} min)")
        if "recovery_time" in durations:
            print(f"   Recovery time: {durations['recovery_time']:.0f}s")
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
    
    # Compute latency comparison (pre-crash vs post-recovery)
    latency_comparison = compute_latency_comparison(df_conf_filtered, events)
    if latency_comparison:
        metrics["latency_comparison"] = latency_comparison
        print(f"\n📈 LATENCY COMPARISON:")
        print(f"   Pre-crash median: {latency_comparison['pre_crash_median']:.2f}s ({latency_comparison['pre_crash_samples']} samples)")
        print(f"   Post-recovery median: {latency_comparison['post_recovery_median']:.2f}s ({latency_comparison['post_recovery_samples']} samples)")
        if latency_comparison['degradation_percent'] > 0:
            print(f"   ⚠️  Degradation: +{latency_comparison['degradation_percent']:.1f}%")
        else:
            print(f"   ✅ Improvement: {latency_comparison['degradation_percent']:.1f}%")
    
    # Compute block interval statistics
    block_interval_stats = compute_block_interval_stats(run_dir, events)
    if block_interval_stats:
        metrics["block_interval_stats"] = block_interval_stats
        print(f"\n⛏️  BLOCK INTERVALS:")
        print(f"   Blocks mined: {block_interval_stats['blocks_mined']}")
        print(f"   Mean interval: {block_interval_stats['mean_seconds']:.1f}s ± {block_interval_stats['std_seconds']:.1f}s")
        print(f"   Range: {block_interval_stats['min_seconds']:.1f}s - {block_interval_stats['max_seconds']:.1f}s")
    
    # Save metrics
    with open(os.path.join(run_dir, "metrics.json"), "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    
    print(f"\n📊 Enhanced analysis complete for {run_dir}")
    print(f"Submitted: {metrics['total_submitted']}, Confirmed: {metrics['total_confirmed']}")
    print(f"Availability: {metrics['availability']:.2%}")
    print(f"Median Latency: {metrics['median_latency']:.2f}s")
    print(f"P95 Latency: {metrics['p95_latency']:.2f}s")
    print(f"Avg Throughput: {metrics['avg_throughput']:.2f} tx/s")

if __name__ == "__main__":
    main()
