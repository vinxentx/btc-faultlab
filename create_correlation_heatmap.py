#!/usr/bin/env python3
"""
Erstellt eine Korrelations-Heatmap basierend auf den wichtigsten Metriken
aus den Bitcoin Fault Lab Experimenten.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import json
import os
from pathlib import Path

def load_experiment_data():
    """Lädt alle verfügbaren Experiment-Daten"""
    
    # Experiment-Ordner und ihre Konfigurationen
    experiments = {
        'baseline_rep1': {
            'path': 'results/baseline_rep1_GOOD/metrics.json',
            'fault_type': 'none',
            'node_count': 32,
            'network_delay': 0,
            'packet_loss': 0,
            'crash_probability': 0,
            'tx_rate': 10
        },
        'crash-only_rep1': {
            'path': 'results/crash-only_rep1_GOOD/metrics.json',
            'fault_type': 'crash',
            'node_count': 32,
            'network_delay': 0,
            'packet_loss': 0,
            'crash_probability': 0.1,
            'tx_rate': 10
        },
        'network-only_rep2': {
            'path': 'results/network-only_rep2_GOOD/metrics.json',
            'fault_type': 'network',
            'node_count': 32,
            'network_delay': 100,
            'packet_loss': 0.05,
            'crash_probability': 0,
            'tx_rate': 10
        },
        'network-only_rep3': {
            'path': 'results/network-only_rep3_GOOD/metrics.json',
            'fault_type': 'network',
            'node_count': 32,
            'network_delay': 100,
            'packet_loss': 0.05,
            'crash_probability': 0,
            'tx_rate': 10
        },
        'combined_rep2': {
            'path': 'results/combined_rep2_GOOD/metrics.json',
            'fault_type': 'combined',
            'node_count': 32,
            'network_delay': 100,
            'packet_loss': 0.05,
            'crash_probability': 0.1,
            'tx_rate': 10
        },
        'combined_rep3': {
            'path': 'results/combined_rep3_GOOD/metrics.json',
            'fault_type': 'combined',
            'node_count': 32,
            'network_delay': 100,
            'packet_loss': 0.05,
            'crash_probability': 0.1,
            'tx_rate': 10
        }
    }
    
    data = []
    
    for exp_name, config in experiments.items():
        metrics_path = config['path']
        
        if os.path.exists(metrics_path):
            with open(metrics_path, 'r') as f:
                metrics = json.load(f)
            
            # Basis-Metriken
            row = {
                'experiment': exp_name,
                'fault_type': config['fault_type'],
                'node_count': config['node_count'],
                'network_delay_ms': config['network_delay'],
                'packet_loss_pct': config['packet_loss'] * 100,
                'crash_probability': config['crash_probability'],
                'tx_rate': config['tx_rate'],
                'total_submitted': metrics.get('total_submitted', 0),
                'total_confirmed': metrics.get('total_confirmed', 0),
                'availability': metrics.get('availability', 0),
                'median_latency': metrics.get('median_latency', 0),
                'p95_latency': metrics.get('p95_latency', 0),
                'avg_throughput': metrics.get('avg_throughput', 0)
            }
            
            # Recovery-Metriken falls verfügbar
            recovery = metrics.get('recovery_analysis', {})
            if recovery:
                row.update({
                    'recovery_time_seconds': recovery.get('recovery_time_seconds', 0),
                    'latency_degradation_pct': recovery.get('latency_degradation_pct', 0),
                    'baseline_latency': recovery.get('baseline_latency', 0),
                    'final_latency': recovery.get('final_latency', 0)
                })
            else:
                row.update({
                    'recovery_time_seconds': 0,
                    'latency_degradation_pct': 0,
                    'baseline_latency': 0,
                    'final_latency': 0
                })
            
            data.append(row)
    
    return pd.DataFrame(data)

def create_correlation_heatmap():
    """Erstellt die Korrelations-Heatmap"""
    
    # Daten laden
    df = load_experiment_data()
    
    if df.empty:
        print("Keine Experiment-Daten gefunden!")
        return
    
    print("Verfügbare Experimente:")
    print(df[['experiment', 'fault_type', 'node_count', 'network_delay_ms', 'packet_loss_pct', 'crash_probability']].to_string())
    
    # Numerische Spalten für Korrelationsanalyse auswählen
    correlation_columns = [
        'node_count',
        'network_delay_ms', 
        'packet_loss_pct',
        'crash_probability',
        'tx_rate',
        'total_submitted',
        'availability',
        'median_latency',
        'p95_latency',
        'avg_throughput',
        'recovery_time_seconds',
        'latency_degradation_pct'
    ]
    
    # Nur verfügbare Spalten verwenden
    available_columns = [col for col in correlation_columns if col in df.columns]
    correlation_data = df[available_columns]
    
    # Korrelationsmatrix berechnen
    correlation_matrix = correlation_data.corr()
    
    # Plot erstellen
    plt.figure(figsize=(14, 12))
    
    # Heatmap mit Annotations
    mask = np.triu(np.ones_like(correlation_matrix, dtype=bool))
    sns.heatmap(
        correlation_matrix,
        mask=mask,
        annot=True,
        cmap='RdBu_r',
        center=0,
        square=True,
        fmt='.2f',
        cbar_kws={"shrink": .8},
        annot_kws={'size': 10}
    )
    
    plt.title('Bitcoin Fault Lab - Korrelationsmatrix der wichtigsten Parameter\n(Performance unter Fehlerbedingungen)', 
              fontsize=16, fontweight='bold', pad=20)
    plt.xlabel('Parameter', fontsize=12, fontweight='bold')
    plt.ylabel('Parameter', fontsize=12, fontweight='bold')
    
    # Achsenbeschriftungen verbessern
    labels = [
        'Knotenanzahl',
        'Netzwerk-Latenz (ms)',
        'Paketverlust (%)',
        'Crash-Wahrscheinlichkeit',
        'TX-Rate',
        'Gesendete TXs',
        'Verfügbarkeit',
        'Median-Latenz',
        'P95-Latenz',
        'Durchsatz',
        'Recovery-Zeit (s)',
        'Latenz-Degradation (%)'
    ]
    
    # Nur so viele Labels wie Spalten
    if len(labels) >= len(correlation_matrix.columns):
        labels = labels[:len(correlation_matrix.columns)]
    
    plt.xticks(range(len(correlation_matrix.columns)), labels, rotation=45, ha='right')
    plt.yticks(range(len(correlation_matrix.columns)), labels, rotation=0)
    
    plt.tight_layout()
    
    # Speichern
    output_path = 'presentation_demo/correlation_heatmaps/bitcoin_fault_correlation_heatmap.png'
    plt.savefig(output_path, dpi=300, bbox_inches='tight')
    print(f"Korrelations-Heatmap gespeichert: {output_path}")
    
    # Auch als PDF speichern für Präsentationen
    pdf_path = 'presentation_demo/correlation_heatmaps/bitcoin_fault_correlation_heatmap.pdf'
    plt.savefig(pdf_path, format='pdf', bbox_inches='tight')
    print(f"Korrelations-Heatmap (PDF) gespeichert: {pdf_path}")
    
    plt.show()
    
    # Statistiken ausgeben
    print("\n=== Korrelationsstatistiken ===")
    print(f"Anzahl Experimente: {len(df)}")
    print(f"Anzahl Parameter: {len(available_columns)}")
    
    # Stärkste Korrelationen finden
    print("\n=== Stärkste positive Korrelationen ===")
    corr_pairs = []
    for i in range(len(correlation_matrix.columns)):
        for j in range(i+1, len(correlation_matrix.columns)):
            corr_val = correlation_matrix.iloc[i, j]
            if not np.isnan(corr_val):
                corr_pairs.append((correlation_matrix.columns[i], correlation_matrix.columns[j], corr_val))
    
    corr_pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    
    for i, (col1, col2, corr) in enumerate(corr_pairs[:5]):
        print(f"{i+1}. {col1} ↔ {col2}: {corr:.3f}")

if __name__ == "__main__":
    create_correlation_heatmap()
