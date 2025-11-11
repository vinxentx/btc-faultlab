#!/usr/bin/env python3
"""
Enhanced Metrics for Thesis-Focused Analysis
Bitcoin Performance Under Omission and Crash Faults
"""

import argparse
import json
import os
import math
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime, timezone
from collections import deque

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

def compute_event_recovery_metrics(run_dir, events):
    """Compute recovery metrics purely from events.log (Option 1).

    Looks for 'recovery_start' and 'recovery_complete' events and derives
    duration and involved nodes without relying on initialblockdownload.
    """
    if not events:
        return None
    start_ts = None
    end_ts = None
    start_nodes = None
    end_nodes = None
    for (ts, evt, rest) in events:
        if evt == "recovery_start" and start_ts is None:
            start_ts = ts
            # parse nodes=...
            if rest and "nodes=" in rest:
                start_nodes = rest.split("nodes=", 1)[1].strip()
        elif evt == "recovery_complete" and end_ts is None:
            end_ts = ts
            if rest and "nodes=" in rest:
                end_nodes = rest.split("nodes=", 1)[1].strip()

    if start_ts and end_ts and end_ts >= start_ts:
        duration = (end_ts - start_ts).total_seconds()
        return {
            "recovery_detected": True,
            "method": "events_log",
            "start_timestamp": start_ts,
            "end_timestamp": end_ts,
            "recovery_time_seconds": duration,
            "nodes": end_nodes or start_nodes or ""
        }
    return None

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
    import re
    from pathlib import Path
    
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
    
    # Pattern: timestamp UpdateTip: new best=<hash> height=<height> ... tx=<count> date='<block_time>' ...
    pattern = re.compile(
        r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\s+UpdateTip:\s+new\s+best=([a-f0-9]+)\s+height=(\d+)\s+.*?\btx=(\d+)\s+date=\'([^\']+)\''
    )
    
    blocks = []
    with open(node_log, 'r', encoding='utf-8') as f:
        for line in f:
            match = pattern.search(line)
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
        
        # Slide window forward (step by 1/4 of window size for smoothness)
        current_time += pd.Timedelta(seconds=window_seconds / 4)
    
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
        if evt == "after_netem":
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
    df_conf['submit_ts_utc'] = pd.to_datetime(df_conf['submit_ts_utc'])
    df_conf['confirm_ts_utc'] = pd.to_datetime(df_conf['confirm_ts_utc'])
    
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

def create_enhanced_plots(run_dir, events, df_sub, df_conf, tps_series):
    """Create enhanced thesis-focused plots"""
    plots_dir = os.path.join(run_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
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
    
    # Throughput over time - use official block-based calculation
    # Use UpdateTip-based throughput (cleanest, least-biased method)
    block_throughput = updatetip_throughput(run_dir, df_conf) if not df_conf.empty else []
    # Fallback to old method if UpdateTip not available
    if not block_throughput:
        block_throughput = block_based_throughput(run_dir, df_conf) if not df_conf.empty else []
    
    if block_throughput:
        # Use official block-based throughput
        x = [t for (t, _) in block_throughput]
        y = [v for (_, v) in block_throughput]
        ax1.plot(x, y, linewidth=2, color='blue', alpha=0.8, label='Throughput (Official: Block-based)', marker='o', markersize=3)
    elif tps_series and len(tps_series) > 0:
        # Fallback to rolling window if block-based not available
        x = [t for (t, _) in tps_series]
        y = [v for (_, v) in tps_series]
        ax1.plot(x, y, linewidth=2, color='blue', alpha=0.8, label='Throughput (Rolling Window - Fallback)')
    else:
        ax1.text(0.5, 0.5, 'No throughput data available', ha='center', va='center', transform=ax1.transAxes)
        ax1.set_title('Transaction Throughput Over Time')
        return
    
    # Add fault event markers (only if faults are injected)
    if has_faults:
        event_labels = set()
        for (ts, evt, _) in events:
            if evt in ("start_warmup", "after_netem", "end_observe"):
                color = 'red' if evt == "after_netem" else 'green'
                label = "Fault Injection" if evt == "after_netem" else f"{evt.replace('_', ' ').title()}"
                if label not in event_labels:
                    ax1.axvline(ts, linestyle="--", alpha=0.7, color=color, label=label)
                    event_labels.add(label)
    else:
        # For baseline, just mark warmup and observation periods
        for (ts, evt, _) in events:
            if evt == "start_warmup":
                ax1.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='Start Warmup')
            elif evt == "end_observe":
                ax1.axvline(ts, linestyle=":", alpha=0.5, color='gray', label='End Observation')
    
    ax1.set_ylabel('Throughput (tx/s)')
    ax1.set_title('Transaction Throughput Over Time (Official: Block-based)')
    ax1.grid(True, alpha=0.3)
    ax1.legend()
    
    # Format x-axis
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
    ax1.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
    plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    
    # Confirmation latency over time
    if not df_conf.empty and len(df_conf) > 0:
        # Rolling average latency with proper window size
        window_size = max(5, min(50, len(df_conf) // 20))
        if window_size > 1:
            rolling_latency = df_conf['latency_seconds'].rolling(window=window_size, center=True).mean()
            ax2.plot(df_conf['confirm_ts_utc'], rolling_latency, linewidth=2, color='orange', alpha=0.8, label=f'Latency (rolling avg, window={window_size})')
        else:
            ax2.plot(df_conf['confirm_ts_utc'], df_conf['latency_seconds'], linewidth=1, color='orange', alpha=0.6, label='Latency')
        
        # Add fault event markers (only if faults are injected)
        if has_faults:
            for (ts, evt, _) in events:
                if evt in ("start_warmup", "after_netem", "end_observe"):
                    color = 'red' if evt == "after_netem" else 'green'
                    ax2.axvline(ts, linestyle="--", alpha=0.7, color=color)
        else:
            # For baseline, just mark warmup and observation periods
            for (ts, evt, _) in events:
                if evt == "start_warmup":
                    ax2.axvline(ts, linestyle=":", alpha=0.5, color='gray')
                elif evt == "end_observe":
                    ax2.axvline(ts, linestyle=":", alpha=0.5, color='gray')
        
        ax2.set_ylabel('Confirmation Latency (s)')
        ax2.set_title('Transaction Confirmation Latency Over Time')
        ax2.grid(True, alpha=0.3)
        ax2.legend()
        
        # Format x-axis
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        ax2.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
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
    
    # Confirmation latency distribution
    if not df_conf.empty and len(df_conf) > 0:
        ax1 = axes[0, 0]
        latencies = df_conf['latency_seconds'].astype(float)
        
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
    
    # Throughput distribution
    if tps_series and len(tps_series) > 0:
        ax2 = axes[0, 1]
        throughputs = [v for (_, v) in tps_series if not np.isnan(v)]
        
        if throughputs:
            ax2.hist(throughputs, bins=min(20, len(throughputs)//3), alpha=0.7, color='lightgreen', edgecolor='black')
            mean_tps = np.mean(throughputs)
            ax2.axvline(mean_tps, color='red', linestyle='--', linewidth=2, 
                       label=f'Mean: {mean_tps:.2f} tx/s')
            ax2.set_xlabel('Throughput (tx/s)')
            ax2.set_ylabel('Frequency')
            ax2.set_title('Throughput Distribution')
            ax2.legend()
            ax2.grid(True, alpha=0.3)
        else:
            ax2.text(0.5, 0.5, 'No valid throughput data', ha='center', va='center', transform=ax2.transAxes)
            ax2.set_title('Throughput Distribution')
    else:
        axes[0, 1].text(0.5, 0.5, 'No throughput data', ha='center', va='center', transform=axes[0, 1].transAxes)
        axes[0, 1].set_title('Throughput Distribution')
    
    # Performance before/after fault injection (only show if faults exist)
    if has_faults and tps_series and events and len(tps_series) > 0:
        ax3 = axes[1, 0]
        
        # Find fault injection time
        fault_time = None
        for (ts, evt, _) in events:
            if evt == "after_netem":
                fault_time = ts
                break
        
        if fault_time:
            # Split data into before/after fault
            before_fault = [(t, v) for (t, v) in tps_series if t < fault_time]
            after_fault = [(t, v) for (t, v) in tps_series if t >= fault_time]
            
            if before_fault and after_fault:
                before_times = [t for (t, _) in before_fault]
                before_vals = [v for (_, v) in before_fault]
                after_times = [t for (t, _) in after_fault]
                after_vals = [v for (_, v) in after_fault]
                
                ax3.plot(before_times, before_vals, linewidth=2, color='green', alpha=0.8, label='Before Fault')
                ax3.plot(after_times, after_vals, linewidth=2, color='red', alpha=0.8, label='After Fault')
                ax3.axvline(fault_time, linestyle="--", alpha=0.7, color='black', label='Fault Injection')
                
                ax3.set_ylabel('Throughput (tx/s)')
                ax3.set_title('Performance Before/After Fault Injection')
                ax3.legend()
                ax3.grid(True, alpha=0.3)
                
                # Format x-axis
                ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
                ax3.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
                plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)
            else:
                ax3.text(0.5, 0.5, 'Insufficient data for before/after analysis', ha='center', va='center', transform=ax3.transAxes)
                ax3.set_title('Performance Before/After Fault Injection')
        else:
            ax3.text(0.5, 0.5, 'No fault injection time found', ha='center', va='center', transform=ax3.transAxes)
            ax3.set_title('Performance Before/After Fault Injection')
    elif not has_faults:
        # For baseline, show throughput stability over time using official block-based method
        ax3 = axes[1, 0]
        # Use UpdateTip-based throughput (cleanest, least-biased method)
    block_throughput = updatetip_throughput(run_dir, df_conf) if not df_conf.empty else []
    # Fallback to old method if UpdateTip not available
    if not block_throughput:
        block_throughput = block_based_throughput(run_dir, df_conf) if not df_conf.empty else []
        
        if block_throughput:
            times = [t for (t, _) in block_throughput]
            values = [v for (_, v) in block_throughput]
            
            ax3.plot(times, values, linewidth=2, color='green', alpha=0.8, 
                    label='Throughput (Official: Block-based)', marker='o', markersize=3)
            median_tps = np.median(values)
            ax3.axhline(median_tps, linestyle='--', color='blue', linewidth=2, 
                       label=f'Median: {median_tps:.2f} tx/s')
            
            # Add stability band (±10%)
            ax3.fill_between(times, median_tps * 0.9, median_tps * 1.1, 
                           alpha=0.2, color='green', label='±10% band')
            
            ax3.set_ylabel('Throughput (tx/s)')
            ax3.set_title('Baseline Throughput Stability (Official: Block-based)')
        elif tps_series and len(tps_series) > 0:
            times = [t for (t, _) in tps_series]
            values = [v for (_, v) in tps_series]
            
            # Skip first minute (warmup artifacts)
            if len(times) > 60:
                warmup_cutoff = times[0] + pd.Timedelta(seconds=60)
                stable_data = [(t, v) for (t, v) in zip(times, values) if t >= warmup_cutoff]
                if stable_data:
                    times, values = zip(*stable_data)
                    times, values = list(times), list(values)
            
            ax3.plot(times, values, linewidth=2, color='green', alpha=0.8, 
                    label='Throughput (Rolling Window - Fallback)')
            median_tps = np.median(values)
            ax3.axhline(median_tps, linestyle='--', color='blue', linewidth=2, 
                       label=f'Median: {median_tps:.2f} tx/s')
            
            # Add stability band (±10%)
            ax3.fill_between(times, median_tps * 0.9, median_tps * 1.1, 
                           alpha=0.2, color='green', label='±10% band')
            
            ax3.set_ylabel('Throughput (tx/s)')
            ax3.set_title('Baseline Throughput Stability (Fallback)')
        else:
            ax3.text(0.5, 0.5, 'Insufficient data for throughput analysis', 
                    ha='center', va='center', transform=ax3.transAxes)
            ax3.set_title('Baseline Throughput Stability')
        
        ax3.legend()
        ax3.grid(True, alpha=0.3)
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        ax3.xaxis.set_major_locator(mdates.MinuteLocator(interval=2))
        plt.setp(ax3.xaxis.get_majorticklabels(), rotation=45)
    else:
        axes[1, 0].text(0.5, 0.5, 'No data for before/after analysis', ha='center', va='center', transform=axes[1, 0].transAxes)
        axes[1, 0].set_title('Performance Before/After Fault Injection')
    
    # System recovery analysis (only show if faults exist)
    if has_faults and not df_conf.empty and events and len(df_conf) > 0:
        ax4 = axes[1, 1]
        
        # Find fault injection time
        fault_time = None
        for (ts, evt, _) in events:
            if evt == "after_netem":
                fault_time = ts
                break
        
        if fault_time:
            # Analyze recovery after fault
            recovery_data = df_conf[df_conf['confirm_ts_utc'] >= fault_time].copy()
            if not recovery_data.empty:
                recovery_data['time_since_fault'] = (recovery_data['confirm_ts_utc'] - fault_time).dt.total_seconds()
                
                # Rolling average for recovery analysis
                window_size = min(20, len(recovery_data) // 5)
                if window_size > 1:
                    rolling_latency = recovery_data['latency_seconds'].rolling(window=window_size).mean()
                    ax4.plot(recovery_data['time_since_fault'], rolling_latency, 
                           linewidth=2, color='red', alpha=0.8, label=f'Recovery Latency (window={window_size})')
                else:
                    ax4.plot(recovery_data['time_since_fault'], recovery_data['latency_seconds'], 
                           linewidth=1, color='red', alpha=0.6, label='Recovery Latency')
                
                ax4.set_xlabel('Time Since Fault Injection (s)')
                ax4.set_ylabel('Confirmation Latency (s)')
                ax4.set_title('System Recovery Analysis')
                ax4.legend()
                ax4.grid(True, alpha=0.3)
            else:
                ax4.text(0.5, 0.5, 'No recovery data available', ha='center', va='center', transform=ax4.transAxes)
                ax4.set_title('System Recovery Analysis')
        else:
            ax4.text(0.5, 0.5, 'No fault injection time found', ha='center', va='center', transform=ax4.transAxes)
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
    
    # Availability over time
    if tps_series and df_sub is not None and len(tps_series) > 0:
        ax1 = axes[0, 0]
        
        # Calculate rolling availability
        window_size = 60  # 1 minute windows
        time_windows = []
        availability_windows = []
        
        # For baseline, skip warmup period to avoid false drops
        start_idx = 0
        if not has_faults and events and len(tps_series) > 60:
            # Skip first 60 seconds for baseline
            warmup_start = events[0][0] if events else tps_series[0][0]
            warmup_cutoff = warmup_start + pd.Timedelta(seconds=60)
            start_idx = next((i for i, (t, _) in enumerate(tps_series) if t >= warmup_cutoff), 0)
        
        for i in range(start_idx, len(tps_series), window_size):
            window_data = tps_series[i:i+window_size]
            if len(window_data) >= 5:  # Minimum data points
                window_time = window_data[0][0]
                window_throughput = np.mean([v for (_, v) in window_data if not np.isnan(v)])
                
                # Estimate availability based on throughput
                # For baseline, use more lenient threshold (80% of expected)
                expected_throughput = float(exp_config.get('tx_rate', 10))
                threshold = expected_throughput * (0.8 if not has_faults else 0.5)
                availability = min(1.0, window_throughput / threshold)
                
                time_windows.append(window_time)
                availability_windows.append(availability)
        
        if time_windows:
            color = 'green' if not has_faults else 'orange'
            ax1.plot(time_windows, availability_windows, linewidth=2, color=color, alpha=0.8)
            
            # For baseline, add target line at 100%
            if not has_faults:
                ax1.axhline(1.0, linestyle='--', color='blue', linewidth=1, alpha=0.5, label='Target: 100%')
                avg_availability = np.mean(availability_windows)
                ax1.text(0.02, 0.98, f'Avg: {avg_availability*100:.1f}%', 
                        transform=ax1.transAxes, va='top', ha='left',
                        bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))
            
            ax1.set_ylabel('Estimated Availability')
            ax1.set_title('System Availability Over Time')
            ax1.legend()
            ax1.grid(True, alpha=0.3)
            ax1.set_ylim(0, 1.05)
            
            # Format x-axis
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
            ax1.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
            plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
        else:
            ax1.text(0.5, 0.5, 'No availability data', ha='center', va='center', transform=ax1.transAxes)
            ax1.set_title('System Availability Over Time')
    else:
        axes[0, 0].text(0.5, 0.5, 'No data for availability analysis', ha='center', va='center', transform=axes[0, 0].transAxes)
        axes[0, 0].set_title('System Availability Over Time')
    
    # Transaction success rate
    if not df_conf.empty and df_sub is not None and len(df_conf) > 0:
        ax2 = axes[0, 1]
        
        total_submitted = len(df_sub)
        total_confirmed = len(df_conf)
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
    
    # Performance metrics summary
    ax3 = axes[1, 0]
    
    metrics_text = []
    if tps_series and len(tps_series) > 0:
        throughputs = [v for (_, v) in tps_series if not np.isnan(v)]
        if throughputs:
            metrics_text.append(f"Avg Throughput: {np.mean(throughputs):.2f} tx/s")
            metrics_text.append(f"Max Throughput: {np.max(throughputs):.2f} tx/s")
            metrics_text.append(f"Min Throughput: {np.min(throughputs):.2f} tx/s")
    
    if not df_conf.empty and len(df_conf) > 0:
        latencies = df_conf['latency_seconds'].astype(float)
        latencies_clean = latencies[latencies >= 0]
        if len(latencies_clean) > 0:
            metrics_text.append(f"Avg Latency: {latencies_clean.mean():.2f} s")
            metrics_text.append(f"Median Latency: {latencies_clean.median():.2f} s")
            metrics_text.append(f"P95 Latency: {latencies_clean.quantile(0.95):.2f} s")
    
    if df_sub is not None and not df_conf.empty:
        total_submitted = len(df_sub)
        total_confirmed = len(df_conf)
        success_rate = total_confirmed / total_submitted if total_submitted > 0 else 0
        metrics_text.append(f"Success Rate: {success_rate:.1%}")
    
    if metrics_text:
        ax3.text(0.1, 0.9, '\n'.join(metrics_text), transform=ax3.transAxes, 
                fontsize=12, verticalalignment='top', fontfamily='monospace',
                bbox=dict(boxstyle="round,pad=0.3", facecolor="lightblue", alpha=0.7))
        ax3.set_title('Performance Metrics Summary')
        ax3.axis('off')
    else:
        ax3.text(0.5, 0.5, 'No metrics available', ha='center', va='center', transform=ax3.transAxes)
        ax3.set_title('Performance Metrics Summary')
    
    # Experiment phases timeline
    ax4 = axes[1, 1]
    
    if events:
        event_times = []
        event_labels = []
        event_colors = []
        
        for (ts, evt, _) in events:
            if evt in ("start_warmup", "after_netem", "end_observe"):
                event_times.append(ts)
                # Adjust labels based on experiment type
                if not has_faults and evt == "after_netem":
                    label = "Start Observation"
                else:
                    label = evt.replace('_', ' ').title()
                event_labels.append(label)
                # Color coding: green=start, red=fault, blue=end, yellow=baseline observation
                if not has_faults and evt == "after_netem":
                    event_colors.append('orange')
                else:
                    event_colors.append('green' if evt == "start_warmup" else 'red' if evt == "after_netem" else 'blue')

        
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

def create_throughput_comparison_plot(run_dir, events, df_sub, df_conf):
    """
    Create a detailed throughput comparison plot using multiple methods.
    This addresses the artifact issues in rolling window calculations.
    """
    plots_dir = os.path.join(run_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
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
    
    # Prepare data
    submit_times = sorted(df_sub["submit_ts_utc"].tolist()) if not df_sub.empty else []
    conf_times = sorted(df_conf["confirm_ts_utc"].tolist()) if not df_conf.empty else []
    
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
            if evt == "after_netem":
                ax1.axvline(ts, linestyle=":", alpha=0.7, color='red', 
                          linewidth=2, label='Fault Injection')
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
    df_sub = pd.read_csv(txlog, parse_dates=["submit_ts_utc"]) if os.path.exists(txlog) else pd.DataFrame()
    
    # Load confirmations.csv - support both old and new column names
    df_conf = pd.DataFrame()
    if os.path.exists(conf):
        df_conf = pd.read_csv(conf)
        # Normalize column names: submit_time -> submit_ts_utc, confirm_time -> confirm_ts_utc
        if "submit_time" in df_conf.columns and "submit_ts_utc" not in df_conf.columns:
            df_conf["submit_ts_utc"] = pd.to_datetime(df_conf["submit_time"])
        if "confirm_time" in df_conf.columns and "confirm_ts_utc" not in df_conf.columns:
            df_conf["confirm_ts_utc"] = pd.to_datetime(df_conf["confirm_time"])
        # Parse dates if columns exist
        if "submit_ts_utc" in df_conf.columns:
            df_conf["submit_ts_utc"] = pd.to_datetime(df_conf["submit_ts_utc"])
        if "confirm_ts_utc" in df_conf.columns:
            df_conf["confirm_ts_utc"] = pd.to_datetime(df_conf["confirm_ts_utc"])
    
    df_conf = clean_confirmation_data(df_conf)
    
    conf_times = sorted(df_conf["confirm_ts_utc"].tolist()) if not df_conf.empty else []
    tps_series = rolling_rate(conf_times, 60) if conf_times else []

    if not df_conf.empty:
        cl = df_conf["latency_seconds"].astype(float)
        cl50 = float(np.percentile(cl, 50))
        cl95 = float(np.percentile(cl, 95))
    else:
        cl50 = cl95 = float("nan")
    
    sub_times = sorted(df_sub["submit_ts_utc"].tolist()) if not df_sub.empty else []
    A = compute_availability(sub_times, conf_times)
    
    # Calculate official Bitcoin network throughput
    # Official definition: Total confirmed transactions / Total time (first to last block)
    avg_throughput_official = 0.0
    if not df_conf.empty and len(conf_times) > 1:
        # Method 1: Total transactions / Total time (first to last confirmation)
        total_time = (conf_times[-1] - conf_times[0]).total_seconds()
        if total_time > 0:
            avg_throughput_official = len(df_conf) / total_time
        
        # Alternative: Use mining.csv if available for block-based calculation
        mining_file = os.path.join(run_dir, "mining.csv")
        if os.path.exists(mining_file):
            try:
                df_mining = pd.read_csv(mining_file)
                if "timestamp_utc" in df_mining.columns and len(df_mining) > 1:
                    df_mining["timestamp_utc"] = pd.to_datetime(df_mining["timestamp_utc"])
                    mining_times = sorted(df_mining["timestamp_utc"].tolist())
                    block_time_span = (mining_times[-1] - mining_times[0]).total_seconds()
                    if block_time_span > 0:
                        # Method 2: Average TX per block × Blocks per second
                        tx_per_block = len(df_conf) / len(df_mining)
                        blocks_per_second = len(df_mining) / block_time_span
                        avg_throughput_block_based = tx_per_block * blocks_per_second
                        # Use block-based if it's more accurate (covers full experiment duration)
                        if block_time_span > total_time * 0.8:  # If block span covers most of experiment
                            avg_throughput_official = avg_throughput_block_based
            except Exception as e:
                print(f"⚠️  Could not read mining.csv for throughput calculation: {e}")
    
    # Create enhanced plots
    create_enhanced_plots(run_dir, events, df_sub, df_conf, tps_series)
    
    # Create throughput comparison plot (new - addresses rolling window artifacts)
    create_throughput_comparison_plot(run_dir, events, df_sub, df_conf)
    
    # Calculate metrics
    metrics = {
        "run_dir": run_dir,
        "total_submitted": len(df_sub),
        "total_confirmed": len(df_conf),
        "availability": A,
        "median_latency": cl50,
        "p95_latency": cl95,
        "avg_throughput": avg_throughput_official  # Official Bitcoin network throughput definition
    }
    
    # Prefer events-based recovery (Option 1); fall back to latency-based if missing
    events_recovery = compute_event_recovery_metrics(run_dir, events)
    if events_recovery:
        metrics["recovery_analysis"] = events_recovery
        print(f"\n🔄 RECOVERY (events-based): {events_recovery['recovery_time_seconds']:.0f}s")
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
