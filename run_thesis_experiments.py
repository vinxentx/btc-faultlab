#!/usr/bin/env python3
"""
Thesis-Optimized Experiment Runner
Runs comprehensive fault tolerance experiments with statistical rigor
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from run_experiments import ExperimentRunner

class ThesisExperimentRunner:
    def __init__(self, skip_bootstrap=True):
        self.base_runner = ExperimentRunner(skip_bootstrap=skip_bootstrap)
        self.config_file = Path("thesis_experiments.json")
        self.results_dir = Path("results")
        
    def load_thesis_config(self):
        """Load thesis experiment configurations"""
        with open(self.config_file, 'r') as f:
            return json.load(f)
    
    def run_experiment_with_replications(self, name, config, num_runs=3, max_retries=2):
        """Run a single experiment configuration multiple times for statistical significance"""
        print(f"\n{'='*80}")
        print(f"EXPERIMENT: {name}")
        print(f"Replications: {num_runs}")
        print(f"{'='*80}")
        
        results = []
        failed_runs = []
        
        for run_num in range(1, num_runs + 1):
            print(f"\n>>> Replication {run_num}/{num_runs}")
            
            # Vary seed for each replication
            config_copy = config.copy()
            config_copy["seed"] = config.get("seed", 42) + run_num - 1
            
            # Retry logic for robustness
            run_id = None
            for attempt in range(1, max_retries + 1):
                try:
                    run_id = self.base_runner.run_single_experiment(config_copy)
                    if run_id:
                        results.append({
                            "experiment": name,
                            "replication": run_num,
                            "run_id": run_id,
                            "config": config_copy,
                            "timestamp": datetime.now().isoformat(),
                            "attempts": attempt
                        })
                        break
                    else:
                        if attempt < max_retries:
                            print(f"⚠️  Attempt {attempt} failed, retrying...")
                            # Clean up any leftover containers
                            self._cleanup_failed_run()
                        else:
                            print(f"❌ Replication {run_num} failed after {max_retries} attempts")
                            failed_runs.append((name, run_num))
                except Exception as e:
                    if attempt < max_retries:
                        print(f"⚠️  Attempt {attempt} failed with error: {e}")
                        print(f"   Retrying...")
                        self._cleanup_failed_run()
                    else:
                        print(f"❌ Replication {run_num} failed after {max_retries} attempts")
                        print(f"   Error: {e}")
                        failed_runs.append((name, run_num))
            
            # Save progress checkpoint after each replication
            self._save_checkpoint(results, failed_runs)
        
        if failed_runs:
            print(f"\n⚠️  {len(failed_runs)} replications failed:")
            for exp, run in failed_runs:
                print(f"   - {exp} replication {run}")
        
        return results
    
    def _cleanup_failed_run(self):
        """Clean up after a failed run"""
        import subprocess
        import time
        
        print("   Cleaning up Docker containers...")
        try:
            # Check if Docker is running
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print("   Docker not running, skipping cleanup")
                return
            
            # Get list of node containers
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=node", "--format", "{{.Names}}"],
                capture_output=True, text=True
            )
            
            if result.returncode == 0 and result.stdout.strip():
                containers = result.stdout.strip().split('\n')
                print(f"   Found {len(containers)} containers to clean up")
                
                # Stop containers
                for container in containers:
                    subprocess.run(
                        ["docker", "stop", container],
                        capture_output=True, text=True
                    )
                
                # Remove containers
                for container in containers:
                    subprocess.run(
                        ["docker", "rm", container],
                        capture_output=True, text=True
                    )
                
                time.sleep(2)  # Wait for cleanup to complete
                print("   Cleanup completed")
            else:
                print("   No containers to clean up")
        except Exception as e:
            print(f"   Warning: Cleanup encountered issue: {e}")
    
    def _save_checkpoint(self, results, failed_runs):
        """Save progress checkpoint"""
        checkpoint_file = self.results_dir / "thesis_progress_checkpoint.json"
        checkpoint_data = {
            "timestamp": datetime.now().isoformat(),
            "completed_runs": results,
            "failed_runs": failed_runs,
            "total_completed": len(results),
            "total_failed": len(failed_runs)
        }
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)
    
    def run_core_suite(self, num_runs=3):
        """Run core thesis experiments: baseline, crash-only, network-only, combined"""
        print("\n" + "="*80)
        print("🎯 RUNNING CORE THESIS EXPERIMENT SUITE")
        print("="*80)
        print(f"Experiments: Baseline, Crash-only, Network-only, Combined")
        print(f"Replications per experiment: {num_runs}")
        print(f"Estimated duration: ~{4 * num_runs * 35} minutes ({4 * num_runs * 35 / 60:.1f} hours)")
        print("="*80)
        
        thesis_config = self.load_thesis_config()
        core_experiments = ["baseline", "crash_only", "network_only", "combined"]
        
        all_results = []
        for exp_name in core_experiments:
            if exp_name in thesis_config["experiments"]:
                exp_config = thesis_config["experiments"][exp_name]
                print(f"\n📊 Running: {exp_config['name']}")
                print(f"   {exp_config['description']}")
                
                results = self.run_experiment_with_replications(
                    exp_name, 
                    exp_config["config"], 
                    num_runs
                )
                all_results.extend(results)
        
        # Save results summary
        summary_file = self.results_dir / f"thesis_core_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(summary_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        
        print(f"\n✅ Core suite complete! Results saved to: {summary_file}")
        return all_results
    
    def run_extended_suite(self, num_runs=3):
        """Run extended thesis experiments including edge cases"""
        print("\n" + "="*80)
        print("🎯 RUNNING EXTENDED THESIS EXPERIMENT SUITE")
        print("="*80)
        
        thesis_config = self.load_thesis_config()
        all_experiments = list(thesis_config["experiments"].keys())
        
        print(f"Experiments: {', '.join(all_experiments)}")
        print(f"Replications per experiment: {num_runs}")
        print(f"Estimated duration: ~{len(all_experiments) * num_runs * 35} minutes")
        print("="*80)
        
        all_results = []
        for exp_name in all_experiments:
            exp_config = thesis_config["experiments"][exp_name]
            print(f"\n📊 Running: {exp_config['name']}")
            print(f"   {exp_config['description']}")
            
            results = self.run_experiment_with_replications(
                exp_name, 
                exp_config["config"], 
                num_runs
            )
            all_results.extend(results)
        
        # Save results summary
        summary_file = self.results_dir / f"thesis_extended_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(summary_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        
        print(f"\n✅ Extended suite complete! Results saved to: {summary_file}")
        return all_results
    
    def run_single_thesis_experiment(self, experiment_name, num_runs=1):
        """Run a specific thesis experiment by name"""
        thesis_config = self.load_thesis_config()
        
        if experiment_name not in thesis_config["experiments"]:
            print(f"❌ Unknown experiment: {experiment_name}")
            print(f"Available experiments: {', '.join(thesis_config['experiments'].keys())}")
            return None
        
        exp_config = thesis_config["experiments"][experiment_name]
        print(f"\n📊 Running: {exp_config['name']}")
        print(f"   {exp_config['description']}")
        
        return self.run_experiment_with_replications(
            experiment_name,
            exp_config["config"],
            num_runs
        )
    
    def list_experiments(self):
        """List all available thesis experiments"""
        thesis_config = self.load_thesis_config()
        
        print("\n" + "="*80)
        print("AVAILABLE THESIS EXPERIMENTS")
        print("="*80)
        
        for exp_name, exp_data in thesis_config["experiments"].items():
            print(f"\n📊 {exp_name}")
            print(f"   Name: {exp_data['name']}")
            print(f"   Description: {exp_data['description']}")
            
            config = exp_data['config']
            print(f"   Configuration:")
            print(f"     - Nodes: {config['node_count']}")
            print(f"     - Crash: {config.get('crash_fraction', 0)*100:.0f}% for {config.get('crash_duration_s', 0)}s")
            print(f"     - Network: {config.get('loss_pct', 0)}% loss, {config.get('latency_ms', 0)}ms latency")
            print(f"     - Duration: {config['warmup_s'] + config['observe_s'] + config['cooldown_s']}s total")
        
        print("\n" + "="*80)
        print("ANALYSIS COMPARISONS")
        print("="*80)
        
        for comparison in thesis_config.get("analysis_comparisons", []):
            print(f"\n🔬 {comparison['name']}")
            print(f"   Compare: {', '.join(comparison['compare'])}")
            print(f"   Metrics: {', '.join(comparison['metrics'])}")
            print(f"   Hypothesis: {comparison['hypothesis']}")

def main():
    parser = argparse.ArgumentParser(
        description="Thesis-Optimized Bitcoin Fault Tolerance Experiment Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run core thesis suite (baseline + 3 fault types) with 3 replications each
  python3 run_thesis_experiments.py --core --runs 3
  
  # Run extended suite (all experiments) with 3 replications each
  python3 run_thesis_experiments.py --extended --runs 3
  
  # Run specific experiment with 5 replications
  python3 run_thesis_experiments.py --experiment crash_only --runs 5
  
  # List all available experiments
  python3 run_thesis_experiments.py --list
        """
    )
    
    parser.add_argument("--core", action="store_true",
                       help="Run core thesis suite (baseline, crash-only, network-only, combined)")
    parser.add_argument("--extended", action="store_true",
                       help="Run extended suite (all experiments)")
    parser.add_argument("--experiment", type=str,
                       help="Run specific experiment by name")
    parser.add_argument("--list", action="store_true",
                       help="List all available thesis experiments")
    parser.add_argument("--runs", type=int, default=3,
                       help="Number of replications per experiment (default: 3)")
    parser.add_argument("--with-bootstrap", action="store_true",
                       help="Run bootstrap step before experiments (default: skip)")
    
    args = parser.parse_args()
    
    runner = ThesisExperimentRunner(skip_bootstrap=not args.with_bootstrap)
    
    if args.list:
        runner.list_experiments()
    elif args.core:
        runner.run_core_suite(num_runs=args.runs)
    elif args.extended:
        runner.run_extended_suite(num_runs=args.runs)
    elif args.experiment:
        runner.run_single_thesis_experiment(args.experiment, num_runs=args.runs)
    else:
        parser.print_help()
        print("\n💡 Tip: Start with --core to run the essential thesis experiments")

if __name__ == "__main__":
    main()

