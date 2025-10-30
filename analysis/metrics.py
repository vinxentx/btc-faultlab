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
    
    if crash_frac == 0 and loss_pct == 0 and latency_ms == 0:
        exp_type = "Baseline (No Faults)"
        has_faults = False
    elif crash_frac > 0 and loss_pct == 0 and latency_ms == 0:
        exp_type = f"Crash-Only ({crash_frac*100:.0f}% Node Failures)"
        has_faults = True
    elif crash_frac == 0 and (loss_pct > 0 or latency_ms > 0):
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
    
    # Throughput over time
    if tps_series and len(tps_series) > 0:
        x = [t for (t, _) in tps_series]
        y = [v for (_, v) in tps_series]
        
        # Plot throughput data
        ax1.plot(x, y, linewidth=2, color='blue', alpha=0.8, label='Throughput')
        
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
        ax1.set_title('Transaction Throughput Over Time')
        ax1.grid(True, alpha=0.3)
        ax1.legend()
        
        # Format x-axis
        ax1.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
        ax1.xaxis.set_major_locator(mdates.MinuteLocator(interval=5))
        plt.setp(ax1.xaxis.get_majorticklabels(), rotation=45)
    else:
        ax1.text(0.5, 0.5, 'No throughput data available', ha='center', va='center', transform=ax1.transAxes)
        ax1.set_title('Transaction Throughput Over Time')
    
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
        # For baseline, show steady-state throughput instead
        axes[1, 0].text(0.5, 0.5, f'Baseline Run - No Fault Injection\n\nSteady-state performance measurement', 
                       ha='center', va='center', transform=axes[1, 0].transAxes, 
                       fontsize=12, bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.3))
        axes[1, 0].set_title('Baseline Performance (No Faults)')
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
        # For baseline, show latency stability instead
        axes[1, 1].text(0.5, 0.5, f'Baseline Run - No Recovery Needed\n\nStable latency throughout observation', 
                       ha='center', va='center', transform=axes[1, 1].transAxes,
                       fontsize=12, bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.3))
        axes[1, 1].set_title('Baseline Stability (No Faults)')
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
        
        for i in range(0, len(tps_series), window_size):
            window_data = tps_series[i:i+window_size]
            if len(window_data) >= 5:  # Minimum data points
                window_time = window_data[0][0]
                window_throughput = np.mean([v for (_, v) in window_data if not np.isnan(v)])
                
                # Estimate availability based on throughput
                expected_throughput = 10  # Based on tx_rate
                availability = min(1.0, window_throughput / expected_throughput)
                
                time_windows.append(window_time)
                availability_windows.append(availability)
        
        if time_windows:
            ax1.plot(time_windows, availability_windows, linewidth=2, color='green', alpha=0.8)
            ax1.set_ylabel('Estimated Availability')
            ax1.set_title('System Availability Over Time')
            ax1.grid(True, alpha=0.3)
            ax1.set_ylim(0, 1)
            
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
    
    # Fault events timeline
    ax4 = axes[1, 1]
    
    if events:
        event_times = []
        event_labels = []
        event_colors = []
        
        for (ts, evt, _) in events:
            if evt in ("start_warmup", "after_netem", "end_observe"):
                event_times.append(ts)
                event_labels.append(evt.replace('_', ' ').title())
                event_colors.append('green' if evt == "start_warmup" else 'red' if evt == "after_netem" else 'blue')
        
        if event_times:
            y_pos = range(len(event_times))
            ax4.barh(y_pos, [1] * len(event_times), color=event_colors, alpha=0.7)
            ax4.set_yticks(y_pos)
            ax4.set_yticklabels(event_labels)
            ax4.set_xlabel('Event Timeline')
            ax4.set_title('Fault Events Timeline')
            ax4.grid(True, alpha=0.3)
            
            # Add time labels
            for i, (time, label) in enumerate(zip(event_times, event_labels)):
                ax4.text(0.5, i, time.strftime('%H:%M:%S'), ha='center', va='center', 
                        transform=ax4.get_yaxis_transform(), fontsize=8)
        else:
            ax4.text(0.5, 0.5, 'No fault events found', ha='center', va='center', transform=ax4.transAxes)
            ax4.set_title('Fault Events Timeline')
    else:
        ax4.text(0.5, 0.5, 'No events data', ha='center', va='center', transform=ax4.transAxes)
        ax4.set_title('Fault Events Timeline')
    
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "system_health_dashboard.png"), dpi=300, bbox_inches='tight')
    plt.close()

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
    df_conf = pd.read_csv(conf, parse_dates=["submit_ts_utc", "confirm_ts_utc"]) if os.path.exists(conf) else pd.DataFrame()
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
    
    # Create enhanced plots
    create_enhanced_plots(run_dir, events, df_sub, df_conf, tps_series)
    
    # Calculate metrics
    metrics = {
        "run_dir": run_dir,
        "total_submitted": len(df_sub),
        "total_confirmed": len(df_conf),
        "availability": A,
        "median_latency": cl50,
        "p95_latency": cl95,
        "avg_throughput": np.mean([v for (_, v) in tps_series]) if tps_series else 0
    }
    
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
