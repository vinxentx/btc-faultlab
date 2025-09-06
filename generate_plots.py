#!/usr/bin/env python3
import pandas as pd
import matplotlib.pyplot as plt
import os
from datetime import datetime
import numpy as np

def generate_plots(run_dir):
    """Generate basic plots from available data"""
    
    # Read transaction data
    txlog_path = os.path.join(run_dir, "txlog.csv")
    events_path = os.path.join(run_dir, "events.log")
    
    if not os.path.exists(txlog_path):
        print(f"No txlog.csv found in {run_dir}")
        return
    
    df_tx = pd.read_csv(txlog_path, parse_dates=["submit_ts_utc"])
    
    # Create plots directory
    plots_dir = os.path.join(run_dir, "plots")
    os.makedirs(plots_dir, exist_ok=True)
    
    # Plot 1: Transaction submission rate over time
    plt.figure(figsize=(12, 6))
    
    # Convert timestamps to relative time in minutes
    start_time = df_tx["submit_ts_utc"].min()
    df_tx["relative_minutes"] = (df_tx["submit_ts_utc"] - start_time).dt.total_seconds() / 60
    
    # Calculate rolling submission rate (transactions per minute)
    window_minutes = 1
    df_tx = df_tx.sort_values("relative_minutes")
    df_tx["tx_rate"] = df_tx["relative_minutes"].rolling(window=window_minutes*60, min_periods=1).count() / window_minutes
    
    plt.plot(df_tx["relative_minutes"], df_tx["tx_rate"], linewidth=2)
    plt.title("Transaction Submission Rate Over Time")
    plt.xlabel("Time (minutes from start)")
    plt.ylabel("Transactions per minute")
    plt.grid(True, alpha=0.3)
    
    # Add event markers if available
    if os.path.exists(events_path):
        with open(events_path, 'r') as f:
            for line in f:
                parts = line.strip().split(' ', 2)
                if len(parts) >= 2:
                    try:
                        event_time = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
                        event_name = parts[1]
                        relative_minutes = (event_time - start_time).total_seconds() / 60
                        plt.axvline(relative_minutes, color='red', linestyle='--', alpha=0.7, 
                                  label=f'{event_name}' if event_name in ['start_warmup', 'after_netem', 'end_observe'] else None)
                    except:
                        continue
    
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "tx_submission_rate.png"), dpi=150, bbox_inches='tight')
    plt.close()
    
    # Plot 2: Transaction submission histogram
    plt.figure(figsize=(10, 6))
    plt.hist(df_tx["relative_minutes"], bins=50, alpha=0.7, edgecolor='black')
    plt.title("Transaction Submission Distribution")
    plt.xlabel("Time (minutes from start)")
    plt.ylabel("Number of transactions")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "tx_submission_histogram.png"), dpi=150, bbox_inches='tight')
    plt.close()
    
    # Plot 3: Cumulative transactions over time
    plt.figure(figsize=(12, 6))
    df_tx["cumulative_tx"] = range(1, len(df_tx) + 1)
    plt.plot(df_tx["relative_minutes"], df_tx["cumulative_tx"], linewidth=2, color='green')
    plt.title("Cumulative Transactions Over Time")
    plt.xlabel("Time (minutes from start)")
    plt.ylabel("Cumulative transaction count")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(os.path.join(plots_dir, "cumulative_transactions.png"), dpi=150, bbox_inches='tight')
    plt.close()
    
    print(f"Generated plots in {plots_dir}:")
    print("- tx_submission_rate.png")
    print("- tx_submission_histogram.png") 
    print("- cumulative_transactions.png")

if __name__ == "__main__":
    import sys
    run_dir = sys.argv[1] if len(sys.argv) > 1 else "results/20250904T135124Z"
    generate_plots(run_dir)

