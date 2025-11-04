#!/usr/bin/env python3
"""
Compare Aggregated Experiments
Compare aggregated replication results between different experiment configurations
"""

import json
import argparse
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from pathlib import Path
import os

class AggregatedExperimentComparator:
    def __init__(self):
        pass
    
    def load_aggregated_result(self, json_file):
        """Load aggregated experiment result from JSON"""
        with open(json_file, 'r') as f:
            return json.load(f)
    
    def detect_experiment_type(self, data):
        """Detect if experiment is baseline (no crashes) or has crashes"""
        crash_fraction = data.get('crash_fraction', 0)
        if crash_fraction == 0:
            return 'baseline'
        return 'crash'
    
    def create_comparison_plot(self, exp1_data, exp2_data, output_dir, exp1_name="Experiment 1", exp2_name="Experiment 2"):
        """Create comparison plots between two experiments (handles both baseline vs crash and crash vs crash)"""
        os.makedirs(output_dir, exist_ok=True)
        
        # Detect experiment types
        exp1_type = self.detect_experiment_type(exp1_data)
        exp2_type = self.detect_experiment_type(exp2_data)
        
        # Set up the plot style
        plt.style.use('default')
        plt.rcParams.update({'font.size': 11, 'figure.figsize': (16, 12)})
        
        fig, axes = plt.subplots(2, 3, figsize=(18, 12))
        
        # Dynamic title based on experiment types
        if exp1_type == 'baseline' and exp2_type == 'crash':
            fig.suptitle(f'Baseline vs. Crash Impact Comparison', fontsize=16, fontweight='bold')
        elif exp1_type == 'crash' and exp2_type == 'crash':
            fig.suptitle(f'Experiment Comparison: {exp1_name} vs {exp2_name}', fontsize=16, fontweight='bold')
        else:
            fig.suptitle(f'Experiment Comparison', fontsize=16, fontweight='bold')
        
        # Color scheme
        exp1_color = 'steelblue'
        exp2_color = 'coral'
        
        # Plot 1: Median Latency Comparison
        ax1 = axes[0, 0]
        if 'median_latency_mean' in exp1_data and 'median_latency_mean' in exp2_data:
            categories = [exp1_name, exp2_name]
            means = [
                exp1_data['median_latency_mean'],
                exp2_data['median_latency_mean']
            ]
            stds = [
                exp1_data.get('median_latency_std', 0),
                exp2_data.get('median_latency_std', 0)
            ]
            ci_lower = [
                exp1_data.get('median_latency_ci_lower', means[0] - stds[0]),
                exp2_data.get('median_latency_ci_lower', means[1] - stds[1])
            ]
            ci_upper = [
                exp1_data.get('median_latency_ci_upper', means[0] + stds[0]),
                exp2_data.get('median_latency_ci_upper', means[1] + stds[1])
            ]
            
            x = np.arange(len(categories))
            bars = ax1.bar(x, means, yerr=stds, capsize=10, alpha=0.7, 
                          color=[exp1_color, exp2_color], edgecolor='black', linewidth=1.5)
            
            # Add CI error bars
            for i, (mean, lower, upper) in enumerate(zip(means, ci_lower, ci_upper)):
                ax1.errorbar(i, mean, yerr=[[mean - lower], [upper - mean]], 
                           fmt='none', color='black', capsize=8, capthick=1.5, linewidth=1)
            
            ax1.set_ylabel('Median Latency (s)', fontsize=12, fontweight='bold')
            ax1.set_title('Median Confirmation Latency', fontsize=13, fontweight='bold')
            ax1.set_xticks(x)
            ax1.set_xticklabels(categories, fontsize=11)
            ax1.grid(True, alpha=0.3, axis='y')
            
            # Add percentage change annotation
            change = ((means[1] - means[0]) / means[0]) * 100
            ax1.text(0.5, 0.95, f'Change: {change:+.1f}%', 
                    transform=ax1.transAxes, ha='center', va='top',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                    fontsize=10, fontweight='bold')
        
        # Plot 2: P95 Latency Comparison
        ax2 = axes[0, 1]
        if 'p95_latency_mean' in exp1_data and 'p95_latency_mean' in exp2_data:
            categories = [exp1_name, exp2_name]
            means = [
                exp1_data['p95_latency_mean'],
                exp2_data['p95_latency_mean']
            ]
            stds = [
                exp1_data.get('p95_latency_std', 0),
                exp2_data.get('p95_latency_std', 0)
            ]
            
            x = np.arange(len(categories))
            bars = ax2.bar(x, means, yerr=stds, capsize=10, alpha=0.7,
                          color=[exp1_color, exp2_color], edgecolor='black', linewidth=1.5)
            
            ax2.set_ylabel('P95 Latency (s)', fontsize=12, fontweight='bold')
            ax2.set_title('95th Percentile Latency', fontsize=13, fontweight='bold')
            ax2.set_xticks(x)
            ax2.set_xticklabels(categories, fontsize=11)
            ax2.grid(True, alpha=0.3, axis='y')
            
            change = ((means[1] - means[0]) / means[0]) * 100
            ax2.text(0.5, 0.95, f'Change: {change:+.1f}%', 
                    transform=ax2.transAxes, ha='center', va='top',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                    fontsize=10, fontweight='bold')
        
        # Plot 3: Throughput Comparison
        ax3 = axes[0, 2]
        if 'avg_throughput_mean' in exp1_data and 'avg_throughput_mean' in exp2_data:
            categories = [exp1_name, exp2_name]
            means = [
                exp1_data['avg_throughput_mean'],
                exp2_data['avg_throughput_mean']
            ]
            stds = [
                exp1_data.get('avg_throughput_std', 0),
                exp2_data.get('avg_throughput_std', 0)
            ]
            ci_lower = [
                exp1_data.get('avg_throughput_ci_lower', means[0] - stds[0]),
                exp2_data.get('avg_throughput_ci_lower', means[1] - stds[1])
            ]
            ci_upper = [
                exp1_data.get('avg_throughput_ci_upper', means[0] + stds[0]),
                exp2_data.get('avg_throughput_ci_upper', means[1] + stds[1])
            ]
            
            x = np.arange(len(categories))
            bars = ax3.bar(x, means, yerr=stds, capsize=10, alpha=0.7,
                          color=[exp1_color, exp2_color], edgecolor='black', linewidth=1.5)
            
            # Add CI error bars
            for i, (mean, lower, upper) in enumerate(zip(means, ci_lower, ci_upper)):
                ax3.errorbar(i, mean, yerr=[[mean - lower], [upper - mean]], 
                           fmt='none', color='black', capsize=8, capthick=1.5, linewidth=1)
            
            ax3.set_ylabel('Throughput (tx/s)', fontsize=12, fontweight='bold')
            ax3.set_title('Transaction Throughput', fontsize=13, fontweight='bold')
            ax3.set_xticks(x)
            ax3.set_xticklabels(categories, fontsize=11)
            ax3.grid(True, alpha=0.3, axis='y')
            
            change = ((means[1] - means[0]) / means[0]) * 100
            ax3.text(0.5, 0.95, f'Change: {change:+.1f}%', 
                    transform=ax3.transAxes, ha='center', va='top',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                    fontsize=10, fontweight='bold')
        
        # Plot 4: Recovery Time Comparison
        ax4 = axes[1, 0]
        if 'recovery_time_mean' in exp1_data and 'recovery_time_mean' in exp2_data:
            # Both experiments have recovery data - compare them
            exp1_recovery = exp1_data['recovery_time_mean']
            exp1_recovery_std = exp1_data.get('recovery_time_std', 0)
            exp1_recovery_ci_lower = exp1_data.get('recovery_time_ci_lower', exp1_recovery - exp1_recovery_std)
            exp1_recovery_ci_upper = exp1_data.get('recovery_time_ci_upper', exp1_recovery + exp1_recovery_std)
            
            exp2_recovery = exp2_data['recovery_time_mean']
            exp2_recovery_std = exp2_data.get('recovery_time_std', 0)
            exp2_recovery_ci_lower = exp2_data.get('recovery_time_ci_lower', exp2_recovery - exp2_recovery_std)
            exp2_recovery_ci_upper = exp2_data.get('recovery_time_ci_upper', exp2_recovery + exp2_recovery_std)
            
            categories = [exp1_name, exp2_name]
            means = [exp1_recovery, exp2_recovery]
            stds = [exp1_recovery_std, exp2_recovery_std]
            
            x = np.arange(len(categories))
            bars = ax4.bar(x, means, yerr=stds, capsize=10, alpha=0.7,
                          color=[exp1_color, exp2_color], edgecolor='black', linewidth=1.5)
            
            # Add CI error bars
            ax4.errorbar(0, exp1_recovery, yerr=[[exp1_recovery - exp1_recovery_ci_lower], [exp1_recovery_ci_upper - exp1_recovery]], 
                       fmt='none', color='black', capsize=8, capthick=1.5, linewidth=1)
            ax4.errorbar(1, exp2_recovery, yerr=[[exp2_recovery - exp2_recovery_ci_lower], [exp2_recovery_ci_upper - exp2_recovery]], 
                       fmt='none', color='black', capsize=8, capthick=1.5, linewidth=1)
            
            ax4.set_ylabel('Recovery Time (s)', fontsize=12, fontweight='bold')
            ax4.set_title('System Recovery Time Comparison', fontsize=13, fontweight='bold')
            ax4.set_xticks(x)
            ax4.set_xticklabels(categories, fontsize=11)
            ax4.grid(True, alpha=0.3, axis='y')
            
            # Add percentage change annotation
            change = ((exp2_recovery - exp1_recovery) / exp1_recovery) * 100
            ax4.text(0.5, 0.95, f'Change: {change:+.1f}%', 
                    transform=ax4.transAxes, ha='center', va='top',
                    bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5),
                    fontsize=10, fontweight='bold')
        elif 'recovery_time_mean' in exp2_data:
            # Only exp2 has recovery data
            recovery_time = exp2_data['recovery_time_mean']
            recovery_std = exp2_data.get('recovery_time_std', 0)
            recovery_ci_lower = exp2_data.get('recovery_time_ci_lower', recovery_time - recovery_std)
            recovery_ci_upper = exp2_data.get('recovery_time_ci_upper', recovery_time + recovery_std)
            
            categories = [exp2_name]
            means = [recovery_time]
            stds = [recovery_std]
            
            x = np.arange(len(categories))
            bars = ax4.bar(x, means, yerr=stds, capsize=10, alpha=0.7,
                          color=exp2_color, edgecolor='black', linewidth=1.5)
            
            # Add CI error bars
            ax4.errorbar(0, recovery_time, yerr=[[recovery_time - recovery_ci_lower], [recovery_ci_upper - recovery_time]], 
                       fmt='none', color='black', capsize=8, capthick=1.5, linewidth=1)
            
            ax4.set_ylabel('Recovery Time (s)', fontsize=12, fontweight='bold')
            ax4.set_title('System Recovery Time', fontsize=13, fontweight='bold')
            ax4.set_xticks(x)
            ax4.set_xticklabels(categories, fontsize=11)
            ax4.grid(True, alpha=0.3, axis='y')
            
            # Add annotation
            ax4.text(0.5, 0.95, f'{recovery_time:.1f}s ± {recovery_std:.1f}s', 
                    transform=ax4.transAxes, ha='center', va='top',
                    bbox=dict(boxstyle='round', facecolor='lightblue', alpha=0.5),
                    fontsize=11, fontweight='bold')
        else:
            ax4.text(0.5, 0.5, 'No recovery data', ha='center', va='center', 
                    transform=ax4.transAxes, fontsize=12)
        
        # Plot 5: Availability Comparison
        ax5 = axes[1, 1]
        if 'availability_mean' in exp1_data and 'availability_mean' in exp2_data:
            categories = [exp1_name, exp2_name]
            means = [
                exp1_data['availability_mean'] * 100,
                exp2_data['availability_mean'] * 100
            ]
            
            x = np.arange(len(categories))
            bars = ax5.bar(x, means, alpha=0.7,
                          color=[exp1_color, exp2_color], edgecolor='black', linewidth=1.5)
            
            ax5.set_ylabel('Availability (%)', fontsize=12, fontweight='bold')
            ax5.set_title('System Availability', fontsize=13, fontweight='bold')
            ax5.set_xticks(x)
            ax5.set_xticklabels(categories, fontsize=11)
            ax5.set_ylim(99, 101)
            ax5.grid(True, alpha=0.3, axis='y')
            
            # Both should be 100%, so no change annotation needed
            ax5.text(0.5, 0.95, f'Both: {means[0]:.1f}%', 
                    transform=ax5.transAxes, ha='center', va='top',
                    bbox=dict(boxstyle='round', facecolor='lightgreen', alpha=0.5),
                    fontsize=10, fontweight='bold')
        
        # Plot 6: Summary Statistics Table
        ax6 = axes[1, 2]
        ax6.axis('off')
        
        summary_text = "Comparison Summary\n\n"
        
        if 'median_latency_mean' in exp1_data and 'median_latency_mean' in exp2_data:
            lat_change = ((exp2_data['median_latency_mean'] - exp1_data['median_latency_mean']) 
                         / exp1_data['median_latency_mean']) * 100
            summary_text += f"Latency: {lat_change:+.1f}%\n"
        
        if 'avg_throughput_mean' in exp1_data and 'avg_throughput_mean' in exp2_data:
            thr_change = ((exp2_data['avg_throughput_mean'] - exp1_data['avg_throughput_mean']) 
                         / exp1_data['avg_throughput_mean']) * 100
            summary_text += f"Throughput: {thr_change:+.1f}%\n"
        
        if 'p95_latency_mean' in exp1_data and 'p95_latency_mean' in exp2_data:
            p95_change = ((exp2_data['p95_latency_mean'] - exp1_data['p95_latency_mean']) 
                         / exp1_data['p95_latency_mean']) * 100
            summary_text += f"P95 Latency: {p95_change:+.1f}%\n"
        
        if 'recovery_time_mean' in exp1_data and 'recovery_time_mean' in exp2_data:
            rec_change = ((exp2_data['recovery_time_mean'] - exp1_data['recovery_time_mean']) 
                         / exp1_data['recovery_time_mean']) * 100
            summary_text += f"\nRecovery Time: {rec_change:+.1f}%\n"
            summary_text += f"{exp1_name}: {exp1_data['recovery_time_mean']:.1f}s\n"
            summary_text += f"{exp2_name}: {exp2_data['recovery_time_mean']:.1f}s\n"
        elif 'recovery_time_mean' in exp2_data:
            summary_text += f"\nRecovery Time:\n"
            summary_text += f"{exp2_name}: {exp2_data['recovery_time_mean']:.1f}s\n"
        elif 'recovery_time_mean' in exp1_data:
            summary_text += f"\nRecovery Time:\n"
            summary_text += f"{exp1_name}: {exp1_data['recovery_time_mean']:.1f}s\n"
        
        summary_text += f"\nConfigurations:\n"
        summary_text += f"{exp1_name}:\n"
        summary_text += f"  Nodes: {exp1_data.get('node_count', 'N/A')}\n"
        summary_text += f"  Crash: {exp1_data.get('crash_fraction', 0)*100:.0f}% for {exp1_data.get('crash_duration_s', 0):.0f}s\n"
        summary_text += f"\n{exp2_name}:\n"
        summary_text += f"  Nodes: {exp2_data.get('node_count', 'N/A')}\n"
        summary_text += f"  Crash: {exp2_data.get('crash_fraction', 0)*100:.0f}% for {exp2_data.get('crash_duration_s', 0):.0f}s\n"
        
        ax6.text(0.1, 0.5, summary_text, fontsize=11, 
                verticalalignment='center', fontfamily='monospace',
                bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.3))
        
        plt.tight_layout()
        plot_file = Path(output_dir) / 'experiment_comparison.png'
        plt.savefig(plot_file, dpi=300, bbox_inches='tight')
        print(f"✅ Comparison plot saved: {plot_file}")
        plt.close()
    
    def create_detailed_comparison_table(self, exp1_data, exp2_data, output_dir, exp1_name="Experiment 1", exp2_name="Experiment 2"):
        """Create a detailed comparison table with dynamic column names"""
        os.makedirs(output_dir, exist_ok=True)
        
        # Use experiment names for column headers
        exp1_col = f"{exp1_name} (Mean ± Std)"
        exp2_col = f"{exp2_name} (Mean ± Std)"
        ci1_col = f"95% CI {exp1_name}"
        ci2_col = f"95% CI {exp2_name}"
        
        comparison = {
            'Metric': [],
            exp1_col: [],
            exp2_col: [],
            'Change (%)': [],
            ci1_col: [],
            ci2_col: []
        }
        
        # Median Latency
        if 'median_latency_mean' in exp1_data and 'median_latency_mean' in exp2_data:
            comparison['Metric'].append('Median Latency (s)')
            comparison[exp1_col].append(
                f"{exp1_data['median_latency_mean']:.2f} ± {exp1_data.get('median_latency_std', 0):.2f}"
            )
            comparison[exp2_col].append(
                f"{exp2_data['median_latency_mean']:.2f} ± {exp2_data.get('median_latency_std', 0):.2f}"
            )
            change = ((exp2_data['median_latency_mean'] - exp1_data['median_latency_mean']) 
                     / exp1_data['median_latency_mean']) * 100
            comparison['Change (%)'].append(f"{change:+.1f}%")
            comparison[ci1_col].append(
                f"[{exp1_data.get('median_latency_ci_lower', 0):.2f}, {exp1_data.get('median_latency_ci_upper', 0):.2f}]"
            )
            comparison[ci2_col].append(
                f"[{exp2_data.get('median_latency_ci_lower', 0):.2f}, {exp2_data.get('median_latency_ci_upper', 0):.2f}]"
            )
        
        # P95 Latency
        if 'p95_latency_mean' in exp1_data and 'p95_latency_mean' in exp2_data:
            comparison['Metric'].append('P95 Latency (s)')
            comparison[exp1_col].append(
                f"{exp1_data['p95_latency_mean']:.2f} ± {exp1_data.get('p95_latency_std', 0):.2f}"
            )
            comparison[exp2_col].append(
                f"{exp2_data['p95_latency_mean']:.2f} ± {exp2_data.get('p95_latency_std', 0):.2f}"
            )
            change = ((exp2_data['p95_latency_mean'] - exp1_data['p95_latency_mean']) 
                     / exp1_data['p95_latency_mean']) * 100
            comparison['Change (%)'].append(f"{change:+.1f}%")
            comparison[ci1_col].append('N/A')
            comparison[ci2_col].append('N/A')
        
        # Throughput
        if 'avg_throughput_mean' in exp1_data and 'avg_throughput_mean' in exp2_data:
            comparison['Metric'].append('Throughput (tx/s)')
            comparison[exp1_col].append(
                f"{exp1_data['avg_throughput_mean']:.2f} ± {exp1_data.get('avg_throughput_std', 0):.2f}"
            )
            comparison[exp2_col].append(
                f"{exp2_data['avg_throughput_mean']:.2f} ± {exp2_data.get('avg_throughput_std', 0):.2f}"
            )
            change = ((exp2_data['avg_throughput_mean'] - exp1_data['avg_throughput_mean']) 
                     / exp1_data['avg_throughput_mean']) * 100
            comparison['Change (%)'].append(f"{change:+.1f}%")
            comparison[ci1_col].append(
                f"[{exp1_data.get('avg_throughput_ci_lower', 0):.2f}, {exp1_data.get('avg_throughput_ci_upper', 0):.2f}]"
            )
            comparison[ci2_col].append(
                f"[{exp2_data.get('avg_throughput_ci_lower', 0):.2f}, {exp2_data.get('avg_throughput_ci_upper', 0):.2f}]"
            )
        
        # Availability
        if 'availability_mean' in exp1_data and 'availability_mean' in exp2_data:
            comparison['Metric'].append('Availability (%)')
            comparison[exp1_col].append(
                f"{exp1_data['availability_mean']*100:.2f}%"
            )
            comparison[exp2_col].append(
                f"{exp2_data['availability_mean']*100:.2f}%"
            )
            comparison['Change (%)'].append("0.0%")
            comparison[ci1_col].append('N/A')
            comparison[ci2_col].append('N/A')
        
        # Recovery Time
        if 'recovery_time_mean' in exp1_data and 'recovery_time_mean' in exp2_data:
            # Both experiments have recovery data - compare them
            comparison['Metric'].append('Recovery Time (s)')
            comparison[exp1_col].append(
                f"{exp1_data['recovery_time_mean']:.1f} ± {exp1_data.get('recovery_time_std', 0):.1f}"
            )
            comparison[exp2_col].append(
                f"{exp2_data['recovery_time_mean']:.1f} ± {exp2_data.get('recovery_time_std', 0):.1f}"
            )
            change = ((exp2_data['recovery_time_mean'] - exp1_data['recovery_time_mean']) 
                     / exp1_data['recovery_time_mean']) * 100
            comparison['Change (%)'].append(f"{change:+.1f}%")
            comparison[ci1_col].append(
                f"[{exp1_data.get('recovery_time_ci_lower', 0):.1f}, {exp1_data.get('recovery_time_ci_upper', 0):.1f}]"
            )
            comparison[ci2_col].append(
                f"[{exp2_data.get('recovery_time_ci_lower', 0):.1f}, {exp2_data.get('recovery_time_ci_upper', 0):.1f}]"
            )
        elif 'recovery_time_mean' in exp2_data:
            # Only exp2 has recovery data
            comparison['Metric'].append('Recovery Time (s)')
            comparison[exp1_col].append('N/A')
            comparison[exp2_col].append(
                f"{exp2_data['recovery_time_mean']:.1f} ± {exp2_data.get('recovery_time_std', 0):.1f}"
            )
            comparison['Change (%)'].append('N/A')
            comparison[ci1_col].append('N/A')
            comparison[ci2_col].append(
                f"[{exp2_data.get('recovery_time_ci_lower', 0):.1f}, {exp2_data.get('recovery_time_ci_upper', 0):.1f}]"
            )
        elif 'recovery_time_mean' in exp1_data:
            # Only exp1 has recovery data
            comparison['Metric'].append('Recovery Time (s)')
            comparison[exp1_col].append(
                f"{exp1_data['recovery_time_mean']:.1f} ± {exp1_data.get('recovery_time_std', 0):.1f}"
            )
            comparison[exp2_col].append('N/A')
            comparison['Change (%)'].append('N/A')
            comparison[ci1_col].append(
                f"[{exp1_data.get('recovery_time_ci_lower', 0):.1f}, {exp1_data.get('recovery_time_ci_upper', 0):.1f}]"
            )
            comparison[ci2_col].append('N/A')
        
        df = pd.DataFrame(comparison)
        
        # Save as CSV
        csv_file = Path(output_dir) / 'detailed_comparison_table.csv'
        df.to_csv(csv_file, index=False)
        print(f"✅ Comparison table saved: {csv_file}")
        
        # Print table
        print("\n" + "="*100)
        print("DETAILED COMPARISON TABLE")
        print("="*100)
        print(df.to_string(index=False))
        print("="*100)
        
        return df

def main():
    parser = argparse.ArgumentParser(
        description="Compare aggregated experiment results",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compare baseline vs tier_a_011:
  python3 analysis/compare_aggregated_experiments.py \\
    --baseline results/baseline_64nodes_aggregated/aggregated_baseline_64nodes.json \\
    --crash results/tier_a_011_aggregated/aggregated_tier_a_011.json \\
    --output-dir results/comparisons
        """
    )
    # Backward compatibility: accept old argument names first
    parser.add_argument("--baseline", help="[DEPRECATED] Use --exp1 instead")
    parser.add_argument("--crash", help="[DEPRECATED] Use --exp2 instead")
    parser.add_argument("--baseline-name", help="[DEPRECATED] Use --exp1-name instead")
    parser.add_argument("--crash-name", help="[DEPRECATED] Use --exp2-name instead")
    
    parser.add_argument("--exp1", help="Path to first experiment aggregated JSON")
    parser.add_argument("--exp2", help="Path to second experiment aggregated JSON")
    parser.add_argument("--output-dir", default="results/comparisons", help="Output directory")
    parser.add_argument("--exp1-name", help="First experiment name")
    parser.add_argument("--exp2-name", help="Second experiment name")
    
    args = parser.parse_args()
    
    # Handle backward compatibility - prioritize deprecated args if provided
    if args.baseline or args.crash:
        exp1_path = args.baseline
        exp2_path = args.crash
        exp1_name = args.baseline_name if args.baseline_name else "Baseline (No Crashes)"
        exp2_name = args.crash_name if args.crash_name else "With Crashes"
    else:
        exp1_path = args.exp1
        exp2_path = args.exp2
        exp1_name = args.exp1_name if args.exp1_name else "Experiment 1"
        exp2_name = args.exp2_name if args.exp2_name else "Experiment 2"
    
    if not exp1_path or not exp2_path:
        parser.error("Either --exp1/--exp2 or --baseline/--crash must be provided")
    
    comparator = AggregatedExperimentComparator()
    
    print("="*80)
    print("COMPARING AGGREGATED EXPERIMENTS")
    print("="*80)
    print(f"Experiment 1: {exp1_path}")
    print(f"Experiment 2: {exp2_path}")
    print(f"Output: {args.output_dir}")
    print("="*80)
    
    # Load data
    exp1_data = comparator.load_aggregated_result(exp1_path)
    exp2_data = comparator.load_aggregated_result(exp2_path)
    
    print("\n✅ Loaded both experiment results")
    
    # Create comparison plot
    print("\n📊 Generating comparison plots...")
    comparator.create_comparison_plot(exp1_data, exp2_data, args.output_dir, 
                                     exp1_name, exp2_name)
    
    # Create detailed table
    print("\n📋 Generating comparison table...")
    comparator.create_detailed_comparison_table(exp1_data, exp2_data, args.output_dir,
                                                exp1_name, exp2_name)
    
    print("\n" + "="*80)
    print("✅ Comparison complete!")
    print("="*80)

if __name__ == "__main__":
    main()

