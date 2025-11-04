#!/usr/bin/env python3
"""
Replication Aggregation and Comparison Tool
Aggregates multiple replications of the same experiment and enables cross-experiment comparison
"""

import json
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
from collections import defaultdict
import os
import sys

# Try to import yaml, fallback to simple parsing
try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

class ReplicationAnalyzer:
    def __init__(self, results_dir="results"):
        self.results_dir = Path(results_dir)
        
    def parse_metadata(self, metadata_file):
        """Parse metadata file (YAML or simple key:value)"""
        metadata = {}
        with open(metadata_file, 'r') as f:
            content = f.read()
        
        if HAS_YAML:
            try:
                metadata = yaml.safe_load(content)
                if metadata is None:
                    metadata = {}
            except:
                # Fallback to simple parsing
                pass
        
        # Simple key:value parsing as fallback
        if not metadata:
            for line in content.split('\n'):
                line = line.strip()
                if ':' in line and not line.startswith('#'):
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    # Try to convert numeric values
                    try:
                        if '.' in value:
                            value = float(value)
                        else:
                            value = int(value)
                    except ValueError:
                        pass
                    metadata[key] = value
        
        return metadata
    
    def load_experiment_group(self, run_ids):
        """Load multiple replications of the same experiment"""
        all_data = []
        
        for idx, run_id in enumerate(run_ids):
            run_dir = self.results_dir / run_id
            if not run_dir.exists():
                print(f"⚠️  Warning: Run directory not found: {run_dir}")
                continue
                
            # Load metrics
            metrics_file = run_dir / "metrics.json"
            metadata_file = run_dir / "metadata.yml"
            
            if not metrics_file.exists():
                print(f"⚠️  Warning: metrics.json not found: {metrics_file}")
                continue
            
            if not metadata_file.exists():
                print(f"⚠️  Warning: metadata.yml not found: {metadata_file}")
                continue
                
            try:
                with open(metrics_file, 'r') as f:
                    metrics = json.load(f)
                
                # Parse metadata
                metadata = self.parse_metadata(metadata_file)
                
                # Combine data
                data = {
                    "run_id": run_id,
                    "replication": idx + 1,
                    **metadata,
                    **metrics
                }
                all_data.append(data)
            except Exception as e:
                print(f"❌ Error loading {run_id}: {e}")
                continue
        
        return pd.DataFrame(all_data)
    
    def aggregate_replications(self, df):
        """Calculate statistics across replications"""
        if df.empty:
            return {}
        
        metrics = {
            'num_replications': len(df),
            'experiment_name': f"{len(df)}_replications",
        }
        
        # Extract configuration (should be same across replications)
        if 'node_count' in df.columns:
            metrics['node_count'] = int(df['node_count'].iloc[0]) if len(df) > 0 else None
        if 'crash_fraction' in df.columns:
            metrics['crash_fraction'] = float(df['crash_fraction'].iloc[0]) if len(df) > 0 else None
        if 'crash_duration_s' in df.columns:
            metrics['crash_duration_s'] = float(df['crash_duration_s'].iloc[0]) if len(df) > 0 else None
        if 'crash_mode' in df.columns:
            metrics['crash_mode'] = df['crash_mode'].iloc[0] if len(df) > 0 else None
        if 'recovery_mode' in df.columns:
            metrics['recovery_mode'] = df['recovery_mode'].iloc[0] if len(df) > 0 else None
        if 'tx_rate' in df.columns:
            metrics['tx_rate'] = float(df['tx_rate'].iloc[0]) if len(df) > 0 else None
        
        # Aggregate numeric metrics
        numeric_cols = ['median_latency', 'p95_latency', 'avg_throughput', 'availability', 'total_submitted', 'total_confirmed']
        for col in numeric_cols:
            if col in df.columns:
                values = pd.to_numeric(df[col], errors='coerce').dropna()
                if len(values) > 0:
                    metrics[f"{col}_mean"] = float(values.mean())
                    metrics[f"{col}_std"] = float(values.std())
                    metrics[f"{col}_min"] = float(values.min())
                    metrics[f"{col}_max"] = float(values.max())
                    if len(values) > 1:
                        # Calculate 95% confidence interval (t-distribution)
                        try:
                            from scipy import stats
                            ci = stats.t.interval(0.95, len(values)-1, loc=values.mean(), scale=stats.sem(values))
                            metrics[f"{col}_ci_lower"] = float(ci[0])
                            metrics[f"{col}_ci_upper"] = float(ci[1])
                        except ImportError:
                            # Fallback if scipy not available (use normal approximation)
                            sem = values.std() / np.sqrt(len(values))
                            metrics[f"{col}_ci_lower"] = float(values.mean() - 1.96 * sem)
                            metrics[f"{col}_ci_upper"] = float(values.mean() + 1.96 * sem)
                        except Exception:
                            # Fallback for any other error
                            sem = values.std() / np.sqrt(len(values))
                            metrics[f"{col}_ci_lower"] = float(values.mean() - 1.96 * sem)
                            metrics[f"{col}_ci_upper"] = float(values.mean() + 1.96 * sem)
        
        # Aggregate recovery times
        recovery_times = []
        for _, row in df.iterrows():
            if isinstance(row.get('recovery_analysis'), dict):
                rec = row['recovery_analysis']
                if rec.get('recovery_detected'):
                    recovery_times.append(rec.get('recovery_time_seconds', 0))
        
        if recovery_times:
            recovery_times = np.array(recovery_times)
            metrics['recovery_time_mean'] = float(np.mean(recovery_times))
            metrics['recovery_time_std'] = float(np.std(recovery_times))
            metrics['recovery_time_min'] = float(np.min(recovery_times))
            metrics['recovery_time_max'] = float(np.max(recovery_times))
            metrics['recovery_success_rate'] = float(len(recovery_times) / len(df))
            
            # Confidence interval for recovery time
            if len(recovery_times) > 1:
                try:
                    from scipy import stats
                    ci = stats.t.interval(0.95, len(recovery_times)-1, 
                                         loc=np.mean(recovery_times), 
                                         scale=stats.sem(recovery_times))
                    metrics['recovery_time_ci_lower'] = float(ci[0])
                    metrics['recovery_time_ci_upper'] = float(ci[1])
                except ImportError:
                    # Fallback if scipy not available
                    sem = np.std(recovery_times) / np.sqrt(len(recovery_times))
                    metrics['recovery_time_ci_lower'] = float(np.mean(recovery_times) - 1.96 * sem)
                    metrics['recovery_time_ci_upper'] = float(np.mean(recovery_times) + 1.96 * sem)
                except Exception:
                    # Fallback for any other error
                    sem = np.std(recovery_times) / np.sqrt(len(recovery_times))
                    metrics['recovery_time_ci_lower'] = float(np.mean(recovery_times) - 1.96 * sem)
                    metrics['recovery_time_ci_upper'] = float(np.mean(recovery_times) + 1.96 * sem)
        
        return metrics
    
    def create_replication_comparison_plot(self, df, output_dir, experiment_name="Experiment"):
        """Create plots comparing individual replications"""
        if df.empty or len(df) < 2:
            print("⚠️  Need at least 2 replications for comparison plot")
            return
        
        os.makedirs(output_dir, exist_ok=True)
        
        fig, axes = plt.subplots(2, 2, figsize=(14, 10))
        fig.suptitle(f'{experiment_name} - Replication Comparison', fontsize=16, fontweight='bold')
        
        # Plot 1: Recovery Time
        ax1 = axes[0, 0]
        recovery_times = []
        for _, row in df.iterrows():
            if isinstance(row.get('recovery_analysis'), dict):
                rec = row['recovery_analysis']
                if rec.get('recovery_detected'):
                    recovery_times.append(rec.get('recovery_time_seconds', 0))
        
        if recovery_times:
            x = range(1, len(recovery_times) + 1)
            bars = ax1.bar(x, recovery_times, alpha=0.7, color='steelblue', edgecolor='black')
            mean_val = np.mean(recovery_times)
            std_val = np.std(recovery_times)
            ax1.axhline(mean_val, color='red', linestyle='--', linewidth=2,
                      label=f'Mean: {mean_val:.1f}s')
            ax1.axhline(mean_val + std_val, color='orange', linestyle=':', alpha=0.7, label=f'±1σ')
            ax1.axhline(mean_val - std_val, color='orange', linestyle=':', alpha=0.7)
            ax1.set_xlabel('Replication Number', fontsize=11)
            ax1.set_ylabel('Recovery Time (s)', fontsize=11)
            ax1.set_title('Recovery Time by Replication', fontsize=12, fontweight='bold')
            ax1.legend(fontsize=9)
            ax1.grid(True, alpha=0.3)
            ax1.set_xticks(x)
        else:
            ax1.text(0.5, 0.5, 'No recovery data', ha='center', va='center', 
                    transform=ax1.transAxes, fontsize=12)
        
        # Plot 2: Median Latency
        ax2 = axes[0, 1]
        if 'median_latency' in df.columns:
            x = range(1, len(df) + 1)
            latencies = pd.to_numeric(df['median_latency'], errors='coerce').dropna()
            if len(latencies) > 0:
                bars = ax2.bar(x, latencies, alpha=0.7, color='lightgreen', edgecolor='black')
                mean_val = latencies.mean()
                std_val = latencies.std()
                ax2.axhline(mean_val, color='red', linestyle='--', linewidth=2,
                          label=f'Mean: {mean_val:.2f}s')
                ax2.axhline(mean_val + std_val, color='orange', linestyle=':', alpha=0.7, label=f'±1σ')
                ax2.axhline(mean_val - std_val, color='orange', linestyle=':', alpha=0.7)
                ax2.set_xlabel('Replication Number', fontsize=11)
                ax2.set_ylabel('Median Latency (s)', fontsize=11)
                ax2.set_title('Median Latency by Replication', fontsize=12, fontweight='bold')
                ax2.legend(fontsize=9)
                ax2.grid(True, alpha=0.3)
                ax2.set_xticks(x)
        else:
            ax2.text(0.5, 0.5, 'No latency data', ha='center', va='center', 
                    transform=ax2.transAxes, fontsize=12)
        
        # Plot 3: Throughput
        ax3 = axes[1, 0]
        if 'avg_throughput' in df.columns:
            x = range(1, len(df) + 1)
            throughputs = pd.to_numeric(df['avg_throughput'], errors='coerce').dropna()
            if len(throughputs) > 0:
                bars = ax3.bar(x, throughputs, alpha=0.7, color='orange', edgecolor='black')
                mean_val = throughputs.mean()
                std_val = throughputs.std()
                ax3.axhline(mean_val, color='red', linestyle='--', linewidth=2,
                          label=f'Mean: {mean_val:.2f} tx/s')
                ax3.axhline(mean_val + std_val, color='orange', linestyle=':', alpha=0.7, label=f'±1σ')
                ax3.axhline(mean_val - std_val, color='orange', linestyle=':', alpha=0.7)
                ax3.set_xlabel('Replication Number', fontsize=11)
                ax3.set_ylabel('Throughput (tx/s)', fontsize=11)
                ax3.set_title('Throughput by Replication', fontsize=12, fontweight='bold')
                ax3.legend(fontsize=9)
                ax3.grid(True, alpha=0.3)
                ax3.set_xticks(x)
        else:
            ax3.text(0.5, 0.5, 'No throughput data', ha='center', va='center', 
                    transform=ax3.transAxes, fontsize=12)
        
        # Plot 4: Summary Statistics
        ax4 = axes[1, 1]
        ax4.axis('off')
        
        summary_text = "Summary Statistics\n\n"
        summary_text += f"Replications: {len(df)}\n\n"
        
        if recovery_times:
            summary_text += f"Recovery Time:\n"
            summary_text += f"  Mean: {np.mean(recovery_times):.1f}s\n"
            summary_text += f"  Std: {np.std(recovery_times):.1f}s\n"
            summary_text += f"  Range: {np.min(recovery_times):.1f}s - {np.max(recovery_times):.1f}s\n\n"
        
        if 'median_latency' in df.columns:
            latencies = pd.to_numeric(df['median_latency'], errors='coerce').dropna()
            if len(latencies) > 0:
                summary_text += f"Median Latency:\n"
                summary_text += f"  Mean: {latencies.mean():.2f}s\n"
                summary_text += f"  Std: {latencies.std():.2f}s\n"
                summary_text += f"  CV: {latencies.std()/latencies.mean()*100:.1f}%\n\n"
        
        if 'avg_throughput' in df.columns:
            throughputs = pd.to_numeric(df['avg_throughput'], errors='coerce').dropna()
            if len(throughputs) > 0:
                summary_text += f"Throughput:\n"
                summary_text += f"  Mean: {throughputs.mean():.2f} tx/s\n"
                summary_text += f"  Std: {throughputs.std():.2f} tx/s\n"
                summary_text += f"  CV: {throughputs.std()/throughputs.mean()*100:.1f}%\n"
        
        ax4.text(0.1, 0.5, summary_text, fontsize=11, 
                verticalalignment='center', fontfamily='monospace',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))
        
        plt.tight_layout()
        plot_file = Path(output_dir) / 'replication_comparison.png'
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        print(f"✅ Replication comparison plot saved: {plot_file}")
        plt.close()
    
    def create_timeline_overlay(self, df, output_dir, experiment_name="Experiment"):
        """Create overlay timeline plots for all replications"""
        if df.empty:
            return
        
        os.makedirs(output_dir, exist_ok=True)
        
        fig, axes = plt.subplots(2, 1, figsize=(16, 10))
        fig.suptitle(f'{experiment_name} - Timeline Overlay (All Replications)', 
                     fontsize=16, fontweight='bold')
        
        # Load confirmation data for each replication
        colors = plt.cm.tab10(np.linspace(0, 1, len(df)))
        
        for idx, (_, row) in enumerate(df.iterrows()):
            run_dir = self.results_dir / row['run_id']
            confirmations_file = run_dir / "confirmations.csv"
            
            if not confirmations_file.exists():
                continue
            
            try:
                conf_df = pd.read_csv(confirmations_file)
                
                # Parse timestamps
                conf_df['submit_ts'] = pd.to_datetime(conf_df['submit_ts_utc'])
                conf_df['confirm_ts'] = pd.to_datetime(conf_df['confirm_ts_utc'])
                conf_df['latency'] = (conf_df['confirm_ts'] - conf_df['submit_ts']).dt.total_seconds()
                
                # Calculate rolling throughput (10s bins)
                conf_df = conf_df.sort_values('submit_ts')
                conf_df['time_bin'] = (conf_df['submit_ts'] - conf_df['submit_ts'].min()).dt.total_seconds()
                conf_df['bin'] = (conf_df['time_bin'] // 10).astype(int)
                
                throughput_by_bin = conf_df.groupby('bin').size() / 10.0
                time_bins = throughput_by_bin.index * 10
                
                # Plot throughput
                axes[0].plot(time_bins, throughput_by_bin, 
                           alpha=0.6, linewidth=1.5, 
                           label=f"Rep {idx+1}", color=colors[idx])
                
                # Plot latency (rolling median)
                conf_df['latency_rolling'] = conf_df['latency'].rolling(window=50, center=True).median()
                axes[1].plot(conf_df['time_bin'], conf_df['latency_rolling'], 
                           alpha=0.6, linewidth=1.5,
                           label=f"Rep {idx+1}", color=colors[idx])
                
            except Exception as e:
                print(f"⚠️  Error loading timeline for {row['run_id']}: {e}")
                continue
        
        # Configure throughput plot
        axes[0].set_xlabel('Time from Start (s)', fontsize=11)
        axes[0].set_ylabel('Throughput (tx/s)', fontsize=11)
        axes[0].set_title('Throughput Timeline (10s bins)', fontsize=12, fontweight='bold')
        axes[0].legend(loc='best', fontsize=9)
        axes[0].grid(True, alpha=0.3)
        
        # Configure latency plot
        axes[1].set_xlabel('Time from Start (s)', fontsize=11)
        axes[1].set_ylabel('Latency (s)', fontsize=11)
        axes[1].set_title('Latency Timeline (rolling median)', fontsize=12, fontweight='bold')
        axes[1].legend(loc='best', fontsize=9)
        axes[1].grid(True, alpha=0.3)
        
        plt.tight_layout()
        plot_file = Path(output_dir) / 'timeline_overlay.png'
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        print(f"✅ Timeline overlay plot saved: {plot_file}")
        plt.close()

def main():
    parser = argparse.ArgumentParser(
        description="Analyze and aggregate experiment replications",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Aggregate 3 replications of tier_a_011:
  python3 analysis/aggregate_replications.py \\
    --run-ids 20251031T172429Z 20251031T174703Z 20251031T180933Z \\
    --experiment-name "tier_a_011" \\
    --output-dir results/tier_a_011_aggregated
        """
    )
    parser.add_argument("--run-ids", nargs='+', required=True, 
                       help="Run IDs to aggregate (e.g., 20251031T172429Z 20251031T174703Z)")
    parser.add_argument("--results-dir", default="results", help="Results directory")
    parser.add_argument("--output-dir", default="results/aggregated", help="Output directory")
    parser.add_argument("--experiment-name", default="Experiment", 
                       help="Name for the experiment (used in plots)")
    parser.add_argument("--no-timeline", action='store_true',
                       help="Skip timeline overlay plot generation")
    args = parser.parse_args()
    
    analyzer = ReplicationAnalyzer(args.results_dir)
    
    print("="*80)
    print(f"REPLICATION ANALYSIS: {args.experiment_name}")
    print("="*80)
    print(f"Run IDs: {', '.join(args.run_ids)}")
    print(f"Results directory: {args.results_dir}")
    print(f"Output directory: {args.output_dir}")
    print("="*80)
    
    # Load and aggregate
    df = analyzer.load_experiment_group(args.run_ids)
    
    if df.empty:
        print("❌ No valid replications found!")
        return
    
    print(f"\n✅ Loaded {len(df)} replications")
    
    # Print per-replication summary
    print("\n" + "-"*80)
    print("PER-REPLICATION SUMMARY")
    print("-"*80)
    for _, row in df.iterrows():
        print(f"\nReplication {row['replication']} ({row['run_id']}):")
        if 'median_latency' in row:
            print(f"  Median Latency: {row['median_latency']:.2f}s")
        if 'avg_throughput' in row:
            print(f"  Throughput: {row['avg_throughput']:.2f} tx/s")
        if isinstance(row.get('recovery_analysis'), dict):
            rec = row['recovery_analysis']
            if rec.get('recovery_detected'):
                print(f"  Recovery Time: {rec.get('recovery_time_seconds', 0):.1f}s")
    
    # Aggregate metrics
    aggregated = analyzer.aggregate_replications(df)
    
    # Save aggregated results
    os.makedirs(args.output_dir, exist_ok=True)
    output_file = Path(args.output_dir) / f"aggregated_{args.experiment_name}.json"
    with open(output_file, 'w') as f:
        json.dump(aggregated, f, indent=2)
    print(f"\n✅ Aggregated metrics saved: {output_file}")
    
    # Create comparison plot
    print("\n📊 Generating comparison plots...")
    analyzer.create_replication_comparison_plot(df, args.output_dir, args.experiment_name)
    
    # Create timeline overlay
    if not args.no_timeline:
        print("📈 Generating timeline overlay...")
        analyzer.create_timeline_overlay(df, args.output_dir, args.experiment_name)
    
    # Print summary
    print("\n" + "="*80)
    print("AGGREGATED METRICS")
    print("="*80)
    print(f"\nConfiguration:")
    print(f"  Replications: {aggregated.get('num_replications', 'N/A')}")
    print(f"  Nodes: {aggregated.get('node_count', 'N/A')}")
    print(f"  Crash Fraction: {aggregated.get('crash_fraction', 'N/A')}")
    print(f"  Crash Duration: {aggregated.get('crash_duration_s', 'N/A')}s")
    
    print(f"\nLatency Metrics:")
    if 'median_latency_mean' in aggregated:
        print(f"  Median Latency: {aggregated['median_latency_mean']:.2f}s ± {aggregated['median_latency_std']:.2f}s")
        if 'median_latency_ci_lower' in aggregated:
            print(f"   95% CI: [{aggregated['median_latency_ci_lower']:.2f}s, {aggregated['median_latency_ci_upper']:.2f}s]")
    
    if 'p95_latency_mean' in aggregated:
        print(f"  P95 Latency: {aggregated['p95_latency_mean']:.2f}s ± {aggregated['p95_latency_std']:.2f}s")
    
    print(f"\nThroughput Metrics:")
    if 'avg_throughput_mean' in aggregated:
        print(f"  Throughput: {aggregated['avg_throughput_mean']:.2f} ± {aggregated['avg_throughput_std']:.2f} tx/s")
        if 'avg_throughput_ci_lower' in aggregated:
            print(f"   95% CI: [{aggregated['avg_throughput_ci_lower']:.2f}, {aggregated['avg_throughput_ci_upper']:.2f}] tx/s")
    
    print(f"\nRecovery Metrics:")
    if 'recovery_time_mean' in aggregated:
        print(f"  Recovery Time: {aggregated['recovery_time_mean']:.1f}s ± {aggregated['recovery_time_std']:.1f}s")
        print(f"  Range: {aggregated['recovery_time_min']:.1f}s - {aggregated['recovery_time_max']:.1f}s")
        if 'recovery_time_ci_lower' in aggregated:
            print(f"   95% CI: [{aggregated['recovery_time_ci_lower']:.1f}s, {aggregated['recovery_time_ci_upper']:.1f}s]")
        print(f"  Success Rate: {aggregated['recovery_success_rate']*100:.1f}%")
    
    print(f"\nAvailability:")
    if 'availability_mean' in aggregated:
        print(f"  Availability: {aggregated['availability_mean']*100:.2f}%")
    
    print("\n" + "="*80)
    print("✅ Analysis complete!")
    print("="*80)

if __name__ == "__main__":
    main()

