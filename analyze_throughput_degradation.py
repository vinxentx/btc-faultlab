#!/usr/bin/env python3
"""
Analyse-Script zur Untersuchung von Throughput-Degradation bei Baseline-Runs
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import sys
from datetime import datetime

def analyze_throughput_degradation(run_dir):
    """Analysiere Throughput-Degradation in einem Baseline-Run"""
    run_path = Path(run_dir)
    
    print(f"🔍 Analysiere: {run_path.name}\n")
    
    # 1. Block-Interval-Analyse
    print("=" * 80)
    print("1. BLOCK-INTERVAL-ANALYSE")
    print("=" * 80)
    
    mining_df = pd.read_csv(run_path / "mining.csv", parse_dates=["timestamp_utc"])
    mining_df = mining_df.sort_values("timestamp_utc")
    
    # Berechne Block-Intervalle
    mining_df['block_interval'] = mining_df['timestamp_utc'].diff().dt.total_seconds()
    mining_df['time_from_start'] = (mining_df['timestamp_utc'] - mining_df['timestamp_utc'].min()).dt.total_seconds()
    
    # Statistiken
    print(f"Total Blöcke: {len(mining_df)}")
    print(f"Block-Interval - Min: {mining_df['block_interval'].min():.2f}s")
    print(f"Block-Interval - Max: {mining_df['block_interval'].max():.2f}s")
    print(f"Block-Interval - Median: {mining_df['block_interval'].median():.2f}s")
    print(f"Block-Interval - Mean: {mining_df['block_interval'].mean():.2f}s")
    print(f"Block-Interval - Std: {mining_df['block_interval'].std():.2f}s")
    
    # Intervalle über Zeit
    first_third = mining_df.iloc[:len(mining_df)//3]
    middle_third = mining_df.iloc[len(mining_df)//3:2*len(mining_df)//3]
    last_third = mining_df.iloc[2*len(mining_df)//3:]
    
    print(f"\nBlock-Intervalle nach Zeitabschnitt:")
    print(f"  Erste 1/3: Mean={first_third['block_interval'].mean():.2f}s, Median={first_third['block_interval'].median():.2f}s")
    print(f"  Mittlere 1/3: Mean={middle_third['block_interval'].mean():.2f}s, Median={middle_third['block_interval'].median():.2f}s")
    print(f"  Letzte 1/3: Mean={last_third['block_interval'].mean():.2f}s, Median={last_third['block_interval'].median():.2f}s")
    
    # 2. Submission vs. Confirmation Rate
    print("\n" + "=" * 80)
    print("2. SUBMISSION vs. CONFIRMATION RATE")
    print("=" * 80)
    
    txlog_df = pd.read_csv(run_path / "txlog.csv", parse_dates=["submit_ts_utc"])
    confirm_df = pd.read_csv(run_path / "confirmations.csv", parse_dates=["submit_ts_utc", "confirm_ts_utc"])
    
    # Submission Rate (10s bins)
    txlog_df['time_from_start'] = (txlog_df['submit_ts_utc'] - txlog_df['submit_ts_utc'].min()).dt.total_seconds()
    txlog_df['bin'] = (txlog_df['time_from_start'] // 10).astype(int)
    submission_rate = txlog_df.groupby('bin').size() / 10.0
    
    # Confirmation Rate (10s bins)
    confirm_df['time_from_start'] = (confirm_df['confirm_ts_utc'] - confirm_df['confirm_ts_utc'].min()).dt.total_seconds()
    confirm_df['bin'] = (confirm_df['time_from_start'] // 10).astype(int)
    confirmation_rate = confirm_df.groupby('bin').size() / 10.0
    
    print(f"Total Submissions: {len(txlog_df)}")
    print(f"Total Confirmations: {len(confirm_df)}")
    print(f"\nSubmission Rate (10s bins):")
    print(f"  Mean: {submission_rate.mean():.2f} tx/s")
    print(f"  Median: {submission_rate.median():.2f} tx/s")
    print(f"  Std: {submission_rate.std():.2f} tx/s")
    print(f"\nConfirmation Rate (10s bins):")
    print(f"  Mean: {confirmation_rate.mean():.2f} tx/s")
    print(f"  Median: {confirmation_rate.median():.2f} tx/s")
    print(f"  Std: {confirmation_rate.std():.2f} tx/s")
    
    # Rate über Zeit
    first_half_sub = submission_rate.iloc[:len(submission_rate)//2]
    second_half_sub = submission_rate.iloc[len(submission_rate)//2:]
    first_half_conf = confirmation_rate.iloc[:len(confirmation_rate)//2]
    second_half_conf = confirmation_rate.iloc[len(confirmation_rate)//2:]
    
    print(f"\nSubmission Rate über Zeit:")
    print(f"  Erste Hälfte: Mean={first_half_sub.mean():.2f} tx/s")
    print(f"  Zweite Hälfte: Mean={second_half_sub.mean():.2f} tx/s")
    print(f"  Änderung: {((second_half_sub.mean() - first_half_sub.mean()) / first_half_sub.mean() * 100):.1f}%")
    
    print(f"\nConfirmation Rate über Zeit:")
    print(f"  Erste Hälfte: Mean={first_half_conf.mean():.2f} tx/s")
    print(f"  Zweite Hälfte: Mean={second_half_conf.mean():.2f} tx/s")
    print(f"  Änderung: {((second_half_conf.mean() - first_half_conf.mean()) / first_half_conf.mean() * 100):.1f}%")
    
    # 3. RPC-Performance-Analyse
    print("\n" + "=" * 80)
    print("3. RPC-PERFORMANCE-ANALYSE")
    print("=" * 80)
    
    perf_df = pd.read_csv(run_path / "txlog_performance.csv", parse_dates=["timestamp_utc"])
    perf_df['time_from_start'] = (perf_df['timestamp_utc'] - perf_df['timestamp_utc'].min()).dt.total_seconds()
    
    # RPC-Zeiten über Zeit
    first_half = perf_df.iloc[:len(perf_df)//2]
    second_half = perf_df.iloc[len(perf_df)//2:]
    
    print(f"getnewaddress_time_ms:")
    print(f"  Erste Hälfte: Mean={first_half['getnewaddress_time_ms'].mean():.2f}ms, P95={first_half['getnewaddress_time_ms'].quantile(0.95):.2f}ms")
    print(f"  Zweite Hälfte: Mean={second_half['getnewaddress_time_ms'].mean():.2f}ms, P95={second_half['getnewaddress_time_ms'].quantile(0.95):.2f}ms")
    
    print(f"\nsendtoaddress_time_ms:")
    print(f"  Erste Hälfte: Mean={first_half['sendtoaddress_time_ms'].mean():.2f}ms, P95={first_half['sendtoaddress_time_ms'].quantile(0.95):.2f}ms")
    print(f"  Zweite Hälfte: Mean={second_half['sendtoaddress_time_ms'].mean():.2f}ms, P95={second_half['sendtoaddress_time_ms'].quantile(0.95):.2f}ms")
    
    print(f"\ntotal_time_ms:")
    print(f"  Erste Hälfte: Mean={first_half['total_time_ms'].mean():.2f}ms, P95={first_half['total_time_ms'].quantile(0.95):.2f}ms")
    print(f"  Zweite Hälfte: Mean={second_half['total_time_ms'].mean():.2f}ms, P95={second_half['total_time_ms'].quantile(0.95):.2f}ms")
    
    # Spitzen identifizieren
    high_getnew = perf_df[perf_df['getnewaddress_time_ms'] > 200]
    high_sendto = perf_df[perf_df['sendtoaddress_time_ms'] > 100]
    high_total = perf_df[perf_df['total_time_ms'] > 200]
    
    print(f"\nRPC-Performance-Spitzen:")
    print(f"  getnewaddress > 200ms: {len(high_getnew)} TXs ({len(high_getnew)/len(perf_df)*100:.1f}%)")
    print(f"  sendtoaddress > 100ms: {len(high_sendto)} TXs ({len(high_sendto)/len(perf_df)*100:.1f}%)")
    print(f"  total_time > 200ms: {len(high_total)} TXs ({len(high_total)/len(perf_df)*100:.1f}%)")
    
    # 4. Block-Auslastung
    print("\n" + "=" * 80)
    print("4. BLOCK-AUSLASTUNG")
    print("=" * 80)
    
    # TXs pro Block
    confirm_df['block_height'] = confirm_df['confirm_block_height']
    txs_per_block = confirm_df.groupby('block_height').size()
    
    print(f"TXs pro Block:")
    print(f"  Mean: {txs_per_block.mean():.2f} TXs/Block")
    print(f"  Median: {txs_per_block.median():.2f} TXs/Block")
    print(f"  Max: {txs_per_block.max()} TXs/Block")
    print(f"  Min: {txs_per_block.min()} TXs/Block")
    
    # Auslastung über Zeit
    first_half_blocks = txs_per_block.iloc[:len(txs_per_block)//2]
    second_half_blocks = txs_per_block.iloc[len(txs_per_block)//2:]
    
    print(f"\nBlock-Auslastung über Zeit:")
    print(f"  Erste Hälfte: Mean={first_half_blocks.mean():.2f} TXs/Block")
    print(f"  Zweite Hälfte: Mean={second_half_blocks.mean():.2f} TXs/Block")
    
    # 5. Bestätigungs-Latenz-Analyse
    print("\n" + "=" * 80)
    print("5. BESTÄTIGUNGS-LATENZ-ANALYSE")
    print("=" * 80)
    
    confirm_df['latency'] = (confirm_df['confirm_ts_utc'] - confirm_df['submit_ts_utc']).dt.total_seconds()
    
    first_half_lat = confirm_df.iloc[:len(confirm_df)//2]
    second_half_lat = confirm_df.iloc[len(confirm_df)//2:]
    
    print(f"Bestätigungs-Latenz:")
    print(f"  Erste Hälfte: Mean={first_half_lat['latency'].mean():.2f}s, Median={first_half_lat['latency'].median():.2f}s")
    print(f"  Zweite Hälfte: Mean={second_half_lat['latency'].mean():.2f}s, Median={second_half_lat['latency'].median():.2f}s")
    print(f"  Änderung: {((second_half_lat['latency'].mean() - first_half_lat['latency'].mean()) / first_half_lat['latency'].mean() * 100):.1f}%")
    
    # 6. UTXO-Konsolidierung Impact
    print("\n" + "=" * 80)
    print("6. UTXO-KONSOLIDIERUNG IMPACT")
    print("=" * 80)
    
    # Finde Konsolidierungs-Punkte (wo utxo_count gemessen wird)
    consolidation_points = perf_df[perf_df['utxo_count'] > 0]
    
    if len(consolidation_points) > 0:
        print(f"Konsolidierungs-Punkte gefunden: {len(consolidation_points)}")
        
        # RPC-Performance um Konsolidierung
        for idx, row in consolidation_points.iterrows():
            tx_num = row['tx_count']
            window = perf_df[(perf_df['tx_count'] >= tx_num - 5) & (perf_df['tx_count'] <= tx_num + 5)]
            if len(window) > 0:
                avg_total = window['total_time_ms'].mean()
                max_total = window['total_time_ms'].max()
                print(f"  TX #{tx_num}: avg_total={avg_total:.1f}ms, max_total={max_total:.1f}ms")
    
    # 7. Rolling Window Artefakte
    print("\n" + "=" * 80)
    print("7. ROLLING WINDOW ARTEFAKTE")
    print("=" * 80)
    
    # Berechne Throughput mit verschiedenen Methoden
    # Rolling 60s
    confirm_times = sorted(confirm_df['confirm_ts_utc'].tolist())
    from collections import deque
    rolling_60s = []
    dq = deque()
    for t in confirm_times:
        dq.append(t)
        while dq and (t - dq[0]).total_seconds() > 60:
            dq.popleft()
        rolling_60s.append((t, len(dq)/60.0))
    
    # Fixed 10s bins
    confirm_df['bin'] = (confirm_df['time_from_start'] // 10).astype(int)
    binned_10s = confirm_df.groupby('bin').size() / 10.0
    
    # Vergleich
    rolling_values = [v for (_, v) in rolling_60s]
    first_third_rolling = rolling_values[:len(rolling_values)//3]
    last_third_rolling = rolling_values[2*len(rolling_values)//3:]
    
    first_third_binned = binned_10s.iloc[:len(binned_10s)//3]
    last_third_binned = binned_10s.iloc[2*len(binned_10s)//3:]
    
    print(f"Rolling Window (60s):")
    print(f"  Erste 1/3: Mean={np.mean(first_third_rolling):.2f} tx/s")
    print(f"  Letzte 1/3: Mean={np.mean(last_third_rolling):.2f} tx/s")
    print(f"  Änderung: {((np.mean(last_third_rolling) - np.mean(first_third_rolling)) / np.mean(first_third_rolling) * 100):.1f}%")
    
    print(f"\nFixed Bins (10s):")
    print(f"  Erste 1/3: Mean={first_third_binned.mean():.2f} tx/s")
    print(f"  Letzte 1/3: Mean={last_third_binned.mean():.2f} tx/s")
    print(f"  Änderung: {((last_third_binned.mean() - first_third_binned.mean()) / first_third_binned.mean() * 100):.1f}%")
    
    # 8. Zusammenfassung
    print("\n" + "=" * 80)
    print("8. ZUSAMMENFASSUNG & DIAGNOSE")
    print("=" * 80)
    
    issues = []
    
    # Block-Interval-Variabilität
    if last_third['block_interval'].mean() > first_third['block_interval'].mean() * 1.1:
        issues.append(f"⚠️  Block-Intervalle werden länger: {first_third['block_interval'].mean():.2f}s → {last_third['block_interval'].mean():.2f}s")
    
    # Submission Rate
    if second_half_sub.mean() < first_half_sub.mean() * 0.9:
        issues.append(f"⚠️  Submission-Rate sinkt: {first_half_sub.mean():.2f} → {second_half_sub.mean():.2f} tx/s")
    
    # Confirmation Rate
    if second_half_conf.mean() < first_half_conf.mean() * 0.9:
        issues.append(f"⚠️  Confirmation-Rate sinkt: {first_half_conf.mean():.2f} → {second_half_conf.mean():.2f} tx/s")
    
    # RPC-Performance
    if second_half['total_time_ms'].mean() > first_half['total_time_ms'].mean() * 1.2:
        issues.append(f"⚠️  RPC-Performance degradiert: {first_half['total_time_ms'].mean():.1f}ms → {second_half['total_time_ms'].mean():.1f}ms")
    
    # Bestätigungs-Latenz
    if second_half_lat['latency'].mean() > first_half_lat['latency'].mean() * 1.2:
        issues.append(f"⚠️  Bestätigungs-Latenz steigt: {first_half_lat['latency'].mean():.2f}s → {second_half_lat['latency'].mean():.2f}s")
    
    # Rolling Window Artefakte
    rolling_change = ((np.mean(last_third_rolling) - np.mean(first_third_rolling)) / np.mean(first_third_rolling) * 100)
    binned_change = ((last_third_binned.mean() - first_third_binned.mean()) / first_third_binned.mean() * 100)
    if abs(rolling_change - binned_change) > 10:
        issues.append(f"⚠️  Rolling Window Artefakte: Rolling zeigt {rolling_change:.1f}% Änderung, Binned zeigt {binned_change:.1f}%")
    
    if issues:
        print("\nGEFUNDENE PROBLEME:")
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ Keine signifikanten Probleme gefunden")
    
    print(f"\n📊 Empfehlung: Verwende Fixed Bins (10s) statt Rolling Window für genauere Throughput-Messung")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_throughput_degradation.py <run_dir>")
        sys.exit(1)
    
    run_dir = sys.argv[1]
    analyze_throughput_degradation(run_dir)



