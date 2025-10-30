#!/usr/bin/env python3
"""
Complete Thesis Analysis Runner
Bitcoin Performance Under Omission and Crash Faults
"""

import os
import sys
import subprocess
from pathlib import Path

def run_complete_thesis_analysis():
    """Run complete thesis-focused analysis"""
    print("🎯 Running Complete Thesis Analysis")
    print("Focus: Bitcoin Performance Under Omission and Crash Faults")
    print("=" * 60)
    
    # 1. Run parameter analysis
    print("\n�� Step 1: Running Parameter Analysis...")
    try:
        result = subprocess.run(["python3", "analysis/advanced_metrics.py"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Parameter analysis completed")
        else:
            print(f"❌ Parameter analysis failed: {result.stderr}")
    except Exception as e:
        print(f"❌ Parameter analysis error: {e}")
    
    # 2. Run thesis fault analysis
    print("\n🔍 Step 2: Running Thesis Fault Analysis...")
    try:
        result = subprocess.run(["python3", "analysis/thesis_fault_analysis.py"], 
                              capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ Thesis fault analysis completed")
        else:
            print(f"❌ Thesis fault analysis failed: {result.stderr}")
    except Exception as e:
        print(f"❌ Thesis fault analysis error: {e}")
    
    # 3. Generate enhanced plots for recent experiments
    print("\n📈 Step 3: Generating Enhanced Individual Plots...")
    results_dir = Path("results")
    recent_runs = sorted([d for d in results_dir.glob("202509*") if d.is_dir()], 
                        key=lambda x: x.name, reverse=True)[:5]  # Last 5 runs
    
    for run_dir in recent_runs:
        print(f"  Processing {run_dir.name}...")
        try:
            result = subprocess.run(["python3", "analysis/metrics.py", "--run-dir", str(run_dir)], 
                                  capture_output=True, text=True)
            if result.returncode == 0:
                print(f"    ✅ Enhanced plots generated")
            else:
                print(f"    ❌ Failed: {result.stderr}")
        except Exception as e:
            print(f"    ❌ Error: {e}")
    
    # 4. Summary
    print("\n🎉 Thesis Analysis Complete!")
    print("\n�� Generated Analysis Files:")
    print("  • results/parameter_analysis/ - Parameter sweep plots")
    print("  • results/thesis_fault_analysis/ - Thesis-focused analysis")
    print("  • results/*/plots/ - Enhanced individual experiment plots")
    print("\n📊 Key Analysis Types:")
    print("  • Crash Impact Analysis - Performance under node crashes")
    print("  • Network Partition Effects - Consensus under network splits")
    print("  • Recovery Dynamics - Recovery time and patterns")
    print("  • Performance Degradation - System health under faults")
    print("  • Individual Experiment Analysis - Detailed per-run analysis")
    
    print("\n🎯 Thesis Alignment:")
    print("  ✅ Focus on omission and crash faults")
    print("  ✅ Performance degradation analysis")
    print("  ✅ Recovery dynamics investigation")
    print("  ✅ Network partition effects")
    print("  ✅ Publication-ready visualizations")

if __name__ == "__main__":
    run_complete_thesis_analysis()
