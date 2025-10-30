#!/usr/bin/env python3
"""
Thesis Experiment Comparison Tool
Compare metrics across different experiment configurations
"""

import os
import sys
import json
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from datetime import datetime

class ExperimentComparator:
    def __init__(self, results_dir="results"):
        self.results_dir = Path(results_dir)
        
    def load_all_experiment_results(self):
        """Load metrics from all experiment runs"""
        all_results = []
        
        for run_dir in self.results_dir.glob("202*"):
            if not run_dir.is_dir():
                continue
            
            metrics_file = run_dir / "metrics.json"
            metadata_file = run_dir / "metadata.yml"
            
            if not metrics_file.exists() or not metadata_file.exists():
                continue
            
            try:
                with open(metrics_file, 'r') as f:
                    metrics = json.load(f)
                
                # Parse metadata (simple YAML parsing)
                metadata = {}
                with open(metadata_file, 'r') as f:
                    for line in f:
                        if ':' in line:
                            key, value = line.strip().split(':', 1)
                            metadata[key.strip()] = value.strip()
                
                # Combine metrics and metadata
                result = {
                    "run_id": run_dir.name,
                    "run_dir": str(run_dir),
                    **metadata,
                    **metrics
                }
                
                all_results.append(result)
            except Exception as e:
                print(f"Warning: Could not load {run_dir}: {e}")
        
        return pd.DataFrame(all_results)
    
    def identify_experiment_type(self, row):
        """Classify experiment based on parameters"""
        crash_frac = float(row.get('crash_fraction', 0))
        loss_pct = float(row.get('loss_pct', 0))
        latency_ms = float(row.get('latency_ms', 0))
        
        if crash_frac == 0 and loss_pct == 0 and latency_ms == 0:
            return "baseline"
        elif crash_frac > 0 and loss_pct == 0 and latency_ms == 0:
            return "crash_only"
        elif crash_frac == 0 and (loss_pct > 0 or latency_ms > 0):
            return "network_only"
        elif crash_frac > 0 and (loss_pct > 0 or latency_ms > 0):
            return "combined"
        else:
            return "other"
    
    def compare_by_experiment_type(self, df):
        """Compare performance across experiment types"""
        if df.empty:
            print("No experiment results found")
            return
        
        # Classify experiments
        df['experiment_type'] = df.apply(self.identify_experiment_type, axis=1)
        
        # Group by experiment type
        grouped = df.groupby('experiment_type')
        
        print("\n" + "="*80)
        print("EXPERIMENT TYPE COMPARISON")
        print("="*80)
        
        metrics_to_compare = [
            ('median_latency', 'Median Latency (s)'),
            ('p95_latency', 'P95 Latency (s)'),
            ('avg_throughput', 'Avg Throughput (tx/s)'),
            ('availability', 'Availability')
        ]
        
        comparison_data = []
        
        for exp_type, group in grouped:
            print(f"\n📊 {exp_type.upper()} ({len(group)} runs)")
            
            row_data = {'experiment': exp_type, 'runs': len(group)}
            
            for metric, label in metrics_to_compare:
                if metric in group.columns:
                    values = pd.to_numeric(group[metric], errors='coerce').dropna()
                    if len(values) > 0:
                        mean_val = values.mean()
                        std_val = values.std()
                        
                        print(f"   {label}: {mean_val:.3f} ± {std_val:.3f}")
                        row_data[f"{metric}_mean"] = mean_val
                        row_data[f"{metric}_std"] = std_val
            
            # Recovery metrics
            recovery_times = []
            recovery_detected = 0
            for idx, row in group.iterrows():
                if isinstance(row.get('recovery_analysis'), dict):
                    rec = row['recovery_analysis']
                    if rec.get('recovery_detected'):
                        recovery_detected += 1
                        recovery_times.append(rec.get('recovery_time_seconds', 0))
            
            if recovery_times:
                print(f"   Recovery Time: {np.mean(recovery_times):.1f}s ± {np.std(recovery_times):.1f}s")
                print(f"   Recovery Success Rate: {recovery_detected}/{len(group)}")
                row_data['recovery_time_mean'] = np.mean(recovery_times)
                row_data['recovery_success_rate'] = recovery_detected / len(group)
            
            comparison_data.append(row_data)
        
        return pd.DataFrame(comparison_data)
    
    def plot_comparison(self, comparison_df, output_dir="results/comparisons"):
        """Generate comparison plots"""
        if comparison_df.empty:
            return
        
        os.makedirs(output_dir, exist_ok=True)
        
        # Set plot style
        plt.style.use('default')
        plt.rcParams.update({'font.size': 10, 'figure.figsize': (14, 10)})
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle('Thesis Experiment Comparison', fontsize=16, fontweight='bold')
        
        # Plot 1: Median Latency
        ax1 = axes[0, 0]
        if 'median_latency_mean' in comparison_df.columns:
            x = range(len(comparison_df))
            y = comparison_df['median_latency_mean']
            yerr = comparison_df['median_latency_std']
            
            ax1.bar(x, y, yerr=yerr, capsize=5, alpha=0.7, color='skyblue', edgecolor='black')
            ax1.set_xticks(x)
            ax1.set_xticklabels(comparison_df['experiment'], rotation=45, ha='right')
            ax1.set_ylabel('Median Latency (s)')
            ax1.set_title('Confirmation Latency by Experiment Type')
            ax1.grid(True, alpha=0.3)
        
        # Plot 2: Throughput
        ax2 = axes[0, 1]
        if 'avg_throughput_mean' in comparison_df.columns:
            x = range(len(comparison_df))
            y = comparison_df['avg_throughput_mean']
            yerr = comparison_df['avg_throughput_std']
            
            ax2.bar(x, y, yerr=yerr, capsize=5, alpha=0.7, color='lightgreen', edgecolor='black')
            ax2.set_xticks(x)
            ax2.set_xticklabels(comparison_df['experiment'], rotation=45, ha='right')
            ax2.set_ylabel('Throughput (tx/s)')
            ax2.set_title('Transaction Throughput by Experiment Type')
            ax2.grid(True, alpha=0.3)
        
        # Plot 3: Availability
        ax3 = axes[1, 0]
        if 'availability_mean' in comparison_df.columns:
            x = range(len(comparison_df))
            y = comparison_df['availability_mean'] * 100  # Convert to percentage
            
            ax3.bar(x, y, alpha=0.7, color='orange', edgecolor='black')
            ax3.set_xticks(x)
            ax3.set_xticklabels(comparison_df['experiment'], rotation=45, ha='right')
            ax3.set_ylabel('Availability (%)')
            ax3.set_title('System Availability by Experiment Type')
            ax3.set_ylim(0, 105)
            ax3.grid(True, alpha=0.3)
        
        # Plot 4: Recovery Time
        ax4 = axes[1, 1]
        if 'recovery_time_mean' in comparison_df.columns:
            recovery_df = comparison_df[comparison_df['recovery_time_mean'].notna()]
            if not recovery_df.empty:
                x = range(len(recovery_df))
                y = recovery_df['recovery_time_mean'] / 60  # Convert to minutes
                
                ax4.bar(x, y, alpha=0.7, color='salmon', edgecolor='black')
                ax4.set_xticks(x)
                ax4.set_xticklabels(recovery_df['experiment'], rotation=45, ha='right')
                ax4.set_ylabel('Recovery Time (minutes)')
                ax4.set_title('Recovery Time by Experiment Type')
                ax4.grid(True, alpha=0.3)
            else:
                ax4.text(0.5, 0.5, 'No recovery data', ha='center', va='center', 
                        transform=ax4.transAxes)
        
        plt.tight_layout()
        plot_file = os.path.join(output_dir, 'thesis_experiment_comparison.png')
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        print(f"\n✅ Comparison plot saved to: {plot_file}")
        plt.close()
    
    def generate_thesis_table(self, comparison_df, output_file="results/thesis_comparison_table.csv"):
        """Generate LaTeX-ready comparison table"""
        if comparison_df.empty:
            return
        
        # Create thesis-ready table
        table_df = comparison_df[['experiment', 'runs']].copy()
        
        if 'median_latency_mean' in comparison_df.columns:
            table_df['Median Latency (s)'] = comparison_df.apply(
                lambda row: f"{row['median_latency_mean']:.2f} ± {row['median_latency_std']:.2f}", 
                axis=1
            )
        
        if 'avg_throughput_mean' in comparison_df.columns:
            table_df['Throughput (tx/s)'] = comparison_df.apply(
                lambda row: f"{row['avg_throughput_mean']:.2f} ± {row['avg_throughput_std']:.2f}", 
                axis=1
            )
        
        if 'availability_mean' in comparison_df.columns:
            table_df['Availability (%)'] = comparison_df.apply(
                lambda row: f"{row['availability_mean']*100:.1f}", 
                axis=1
            )
        
        if 'recovery_time_mean' in comparison_df.columns:
            table_df['Recovery Time (min)'] = comparison_df.apply(
                lambda row: f"{row['recovery_time_mean']/60:.1f}" if pd.notna(row['recovery_time_mean']) else 'N/A',
                axis=1
            )
        
        table_df.to_csv(output_file, index=False)
        print(f"✅ Thesis table saved to: {output_file}")
        
        # Print LaTeX-ready version
        print("\n" + "="*80)
        print("LATEX TABLE (copy to thesis)")
        print("="*80)
        print("\\begin{table}[h]")
        print("\\centering")
        print("\\caption{Bitcoin Network Performance Under Different Fault Scenarios}")
        print("\\label{tab:fault-comparison}")
        print("\\begin{tabular}{lcccc}")
        print("\\hline")
        print("Experiment Type & Median Latency (s) & Throughput (tx/s) & Availability (\\%) & Recovery Time (min) \\\\")
        print("\\hline")
        
        for _, row in table_df.iterrows():
            exp = row['experiment'].replace('_', ' ').title()
            lat = row.get('Median Latency (s)', 'N/A')
            thr = row.get('Throughput (tx/s)', 'N/A')
            avl = row.get('Availability (%)', 'N/A')
            rec = row.get('Recovery Time (min)', 'N/A')
            print(f"{exp} & {lat} & {thr} & {avl} & {rec} \\\\")
        
        print("\\hline")
        print("\\end{tabular}")
        print("\\end{table}")

def main():
    parser = argparse.ArgumentParser(description="Compare thesis experiment results")
    parser.add_argument("--results-dir", default="results", help="Results directory")
    parser.add_argument("--output-dir", default="results/comparisons", help="Output directory for plots")
    args = parser.parse_args()
    
    comparator = ExperimentComparator(args.results_dir)
    
    print("Loading experiment results...")
    df = comparator.load_all_experiment_results()
    
    if df.empty:
        print("No experiment results found")
        return
    
    print(f"Loaded {len(df)} experiment runs")
    
    # Compare experiments
    comparison_df = comparator.compare_by_experiment_type(df)
    
    # Generate plots
    comparator.plot_comparison(comparison_df, args.output_dir)
    
    # Generate thesis table
    comparator.generate_thesis_table(comparison_df)

if __name__ == "__main__":
    main()



