#!/usr/bin/env python3
"""
Tier-Based Experiment Runner
Runs Tier A/B/C fault tolerance experiments with statistical rigor
"""

import os
import sys
import json
import argparse
import random
from pathlib import Path
from datetime import datetime
from run_experiments import ExperimentRunner

class TierExperimentRunner:
    def __init__(self, skip_bootstrap=True, config_file="tier_experiments.json", snapshot_dir=None):
        self.base_runner = ExperimentRunner(skip_bootstrap=skip_bootstrap, snapshot_dir=snapshot_dir)
        self.config_file = Path(config_file)
        self.results_dir = Path("results")
        self.snapshot_dir = snapshot_dir
        
    def load_tier_config(self):
        """Load tier experiment configurations"""
        with open(self.config_file, 'r') as f:
            return json.load(f)
    
    def run_experiment_with_replications(self, name, config, num_runs=4, max_retries=2, node_count_override=None):
        """Run a single experiment configuration multiple times for statistical significance"""
        print(f"\n{'='*80}")
        print(f"EXPERIMENT: {name}")
        print(f"Replications: {num_runs}")
        print(f"{'='*80}")
        
        results = []
        failed_runs = []
        
        for run_num in range(1, num_runs + 1):
            # Generate random seed for each replication (ensures statistical independence)
            random_seed = random.randint(1, 999999)
            print(f"\n>>> Replication {run_num}/{num_runs} (seed: {random_seed})")

            # Use random seed for each replication
            config_copy = config.copy()
            config_copy["seed"] = random_seed
            
            # Override node count if specified
            if node_count_override is not None:
                original_count = config_copy.get("node_count", "unknown")
                config_copy["node_count"] = node_count_override
                print(f"   🔧 Node count override: {original_count} → {node_count_override}")
            
            # Add experiment name to config for run_id naming
            config_copy["experiment_name"] = name
            
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
            
            # Clean up between replications to prevent data contamination
            # This ensures each replication starts with a clean slate
            # Also cleanup after last replication to prepare for next experiment config
            if run_id:
                print(f"\n🧹 Cleaning up after replication {run_num}...")
                self._cleanup_between_runs()
            
            # Save progress checkpoint after each replication
            self._save_checkpoint(results, failed_runs)
        
        if failed_runs:
            print(f"\n⚠️  {len(failed_runs)} replications failed:")
            for exp, run in failed_runs:
                print(f"   - {exp} replication {run}")
        
        return results
    
    def _cleanup_between_runs(self):
        """Clean up Docker resources between replications to prevent data contamination"""
        import subprocess
        import time
        
        try:
            # Check if Docker is running
            result = subprocess.run(
                ["docker", "info"],
                capture_output=True, text=True
            )
            if result.returncode != 0:
                print("   ⚠️  Docker not running, skipping cleanup")
                return
            
            # Step 1: Stop and remove containers using docker compose
            result = subprocess.run(
                ["docker", "compose", "down", "-v"],
                capture_output=True, text=True,
                cwd=self.base_runner.base_dir
            )
            if result.returncode != 0:
                print(f"   ⚠️  Warning: docker compose down had issues: {result.stderr}")
            
            # Step 2: Force remove any lingering containers (nodes, wallet, txgen shards, scheduler)
            containers = []
            
            # Collect all Bitcoin-related containers
            for name_filter in ["node", "wallet", "txgen", "block_scheduler"]:
                result = subprocess.run(
                    ["docker", "ps", "-a", "--filter", f"name={name_filter}", "--format", "{{.Names}}"],
                    capture_output=True, text=True
                )
                if result.returncode == 0 and result.stdout.strip():
                    containers.extend(result.stdout.strip().split('\n'))
            
            if containers:
                unique_containers = list(set(containers))
                print(f"   Removing {len(unique_containers)} lingering containers...")
                for container in unique_containers:
                    subprocess.run(
                        ["docker", "rm", "-f", container],
                        capture_output=True, text=True
                    )
            
            # Step 3: Remove orphaned volumes (critical for preventing data contamination)
            print("   Removing Docker volumes to prevent data contamination...")
            result = subprocess.run(
                ["docker", "volume", "ls", "--format", "{{.Name}}"],
                capture_output=True, text=True
            )
            
            if result.returncode == 0 and result.stdout.strip():
                all_volumes = result.stdout.strip().split('\n')
                # Match volumes for nodes, wallet, txlogs, and network-specific volumes
                # Pattern: {network_name}_node{XX}_data, {network_name}_wallet_data, {network_name}_txlogs
                # Be specific to avoid removing unrelated Docker volumes
                node_volumes = [
                    v for v in all_volumes 
                    if any(keyword in v.lower() for keyword in ['node', 'txlogs', 'wallet', 'btcnet'])
                    # Also match patterns like "btcnet_node01_data" or "{network}_wallet_data"
                    or ('_node' in v.lower() and '_data' in v.lower())  # node volumes
                    or ('_wallet' in v.lower() and '_data' in v.lower())  # wallet volumes
                ]
                
                if node_volumes:
                    print(f"   Removing {len(node_volumes)} volumes...")
                    for volume in node_volumes:
                        subprocess.run(
                            ["docker", "volume", "rm", "-f", volume],
                            capture_output=True, text=True
                        )
            
            time.sleep(2)  # Wait for cleanup to complete
            print("   ✅ Cleanup completed - ready for next replication")
        except Exception as e:
            print(f"   ⚠️  Warning: Cleanup encountered issue: {e}")
    
    def _cleanup_failed_run(self):
        """Clean up after a failed run - removes containers AND volumes"""
        # Reuse the same cleanup logic
        self._cleanup_between_runs()
    
    def _save_checkpoint(self, results, failed_runs):
        """Save progress checkpoint"""
        checkpoint_file = self.results_dir / "tier_progress_checkpoint.json"
        checkpoint_data = {
            "timestamp": datetime.now().isoformat(),
            "completed_runs": results,
            "failed_runs": failed_runs,
            "total_completed": len(results),
            "total_failed": len(failed_runs)
        }
        with open(checkpoint_file, 'w') as f:
            json.dump(checkpoint_data, f, indent=2)
    
    def run_baseline(self, num_runs=4, node_count_override=None):
        """Run baseline experiment only"""
        print("\n" + "="*80)
        print("🎯 RUNNING BASELINE EXPERIMENT")
        print("="*80)
        
        tier_config = self.load_tier_config()
        
        if "baseline" in tier_config["experiments"]:
            exp_config = tier_config["experiments"]["baseline"]
            print(f"\n📊 Running: {exp_config['name']}")
            print(f"   {exp_config['description']}")
            
            results = self.run_experiment_with_replications(
                "baseline",
                exp_config["config"], 
                num_runs,
                node_count_override=node_count_override
            )
            
            # Save results summary
            summary_file = self.results_dir / f"tier_baseline_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(summary_file, 'w') as f:
                json.dump(results, f, indent=2)
            
            print(f"\n✅ Baseline complete! Results saved to: {summary_file}")
            return results
        else:
            print("❌ Baseline experiment not found in configuration")
            return []
    
    def run_tier_a(self, num_runs=4, node_count_override=None):
        """Run all Tier A experiments (crash impact)"""
        print("\n" + "="*80)
        print("🎯 RUNNING TIER A EXPERIMENTS (Crash Impact Analysis)")
        print("="*80)
        print(f"12 experiments × {num_runs} replications = {12 * num_runs} runs")
        print(f"Estimated duration: ~{12 * num_runs * 35 / 60:.1f} hours")
        print("="*80)

        return self._run_tier("A", num_runs, node_count_override)
    
    def run_tier_b(self, num_runs=4, node_count_override=None):
        """Run all Tier B experiments (stress environment)"""
        print("\n" + "="*80)
        print("🎯 RUNNING TIER B EXPERIMENTS (Stress Environment)")
        print("="*80)
        print(f"14 experiments × {num_runs} replications = {14 * num_runs} runs")
        print(f"Estimated duration: ~{14 * num_runs * 35 / 60:.1f} hours")
        print("="*80)

        return self._run_tier("B", num_runs, node_count_override)

    def run_tier_c(self, num_runs=4, node_count_override=None):
        """Run all Tier C experiments (block interval sensitivity)"""
        print("\n" + "="*80)
        print("🎯 RUNNING TIER C EXPERIMENTS (Block Interval Sensitivity)")
        print("="*80)
        print(f"6 experiments × {num_runs} replications = {6 * num_runs} runs")
        print(f"Estimated duration: ~{6 * num_runs * 35 / 60:.1f} hours")
        print("="*80)

        return self._run_tier("C", num_runs, node_count_override)

    def run_tier_d(self, num_runs=4, node_count_override=None):
        """Run all Tier D experiments (64-node network size comparison)"""
        print("\n" + "="*80)
        print("🎯 RUNNING TIER D EXPERIMENTS (64-Node Network Size Comparison)")
        print("="*80)
        print(f"10 experiments × {num_runs} replications = {10 * num_runs} runs")
        print(f"Estimated duration: ~{10 * num_runs * 35 / 60:.1f} hours")
        print("="*80)

        return self._run_tier("D", num_runs, node_count_override)

    def _run_tier(self, tier_name, num_runs, node_count_override=None):
        """Run all experiments for a specific tier"""
        tier_config = self.load_tier_config()
        all_results = []
        
        for exp_name, exp_data in tier_config["experiments"].items():
            if exp_data.get("tier") == tier_name:
                print(f"\n📊 Running: {exp_data['name']}")
                print(f"   {exp_data['description']}")
                
                results = self.run_experiment_with_replications(
                    exp_name,
                    exp_data["config"],
                    num_runs,
                    node_count_override=node_count_override
                )
                all_results.extend(results)
        
        # Save results summary
        summary_file = self.results_dir / f"tier_{tier_name.lower()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(summary_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        
        print(f"\n✅ Tier {tier_name} complete! Results saved to: {summary_file}")
        return all_results
    
    def run_extended_suite(self, num_runs=4, node_count_override=None):
        """Run all tier experiments (baseline + all tiers)"""
        print("\n" + "="*80)
        print("🎯 RUNNING FULL TIER EXPERIMENT SUITE")
        print("="*80)
        
        tier_config = self.load_tier_config()
        all_experiments = list(tier_config["experiments"].keys())
        
        print(f"Total experiments: {len(all_experiments)}")
        print(f"Replications per experiment: {num_runs}")
        print(f"Total runs: {len(all_experiments) * num_runs}")
        print(f"Estimated duration: ~{len(all_experiments) * num_runs * 35 / 60:.1f} hours")
        print("="*80)
        
        all_results = []
        for exp_name in all_experiments:
            exp_data = tier_config["experiments"][exp_name]
            print(f"\n📊 Running: {exp_data['name']}")
            print(f"   Tier: {exp_data.get('tier', 'N/A')}")
            print(f"   {exp_data['description']}")
            
            results = self.run_experiment_with_replications(
                exp_name,
                exp_data["config"],
                num_runs,
                node_count_override=node_count_override
            )
            all_results.extend(results)
        
        # Save results summary
        summary_file = self.results_dir / f"tier_full_suite_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(summary_file, 'w') as f:
            json.dump(all_results, f, indent=2)
        
        print(f"\n✅ Full suite complete! Results saved to: {summary_file}")
        return all_results
    
    def run_single_experiment(self, experiment_name, num_runs=1, node_count_override=None):
        """Run a specific tier experiment by name"""
        tier_config = self.load_tier_config()
        
        if experiment_name not in tier_config["experiments"]:
            print(f"❌ Unknown experiment: {experiment_name}")
            print(f"Available experiments: {', '.join(tier_config['experiments'].keys())}")
            return None
        
        exp_data = tier_config["experiments"][experiment_name]
        print(f"\n📊 Running: {exp_data['name']}")
        print(f"   Tier: {exp_data.get('tier', 'N/A')}")
        print(f"   {exp_data['description']}")
        
        return self.run_experiment_with_replications(
            experiment_name,
            exp_data["config"],
            num_runs,
            node_count_override=node_count_override
        )
    
    def list_experiments(self):
        """List all available tier experiments"""
        tier_config = self.load_tier_config()
        
        print("\n" + "="*80)
        print("AVAILABLE TIER EXPERIMENTS")
        print("="*80)
        
        # Group by tier
        tiers = {}
        for exp_name, exp_data in tier_config["experiments"].items():
            tier = exp_data.get("tier", "baseline")
            if tier not in tiers:
                tiers[tier] = []
            tiers[tier].append((exp_name, exp_data))
        
        for tier in sorted(tiers.keys()):
            print(f"\n{'='*80}")
            print(f"TIER {tier.upper()}")
            print(f"{'='*80}")
            
            for exp_name, exp_data in tiers[tier]:
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
        print(f"Total: {len(tier_config['experiments'])} experiments")
        print("="*80)

def main():
    parser = argparse.ArgumentParser(
        description="Tier-Based Bitcoin Fault Tolerance Experiment Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run full suite (baseline + all tiers) with 3 replications
  python3 run_tier_experiments.py --extended --runs 3

  # Run only baseline
  python3 run_tier_experiments.py --baseline --runs 3

  # Run specific tier
  python3 run_tier_experiments.py --tier A --runs 3
  python3 run_tier_experiments.py --tier B --runs 3
  python3 run_tier_experiments.py --tier C --runs 3

  # Run specific experiment with custom node count
  python3 run_tier_experiments.py --experiment baseline --runs 1 --node-count 64
  python3 run_tier_experiments.py --experiment tier_a_001 --runs 5 --node-count 32

  # Run tier with reduced node count for faster testing
  python3 run_tier_experiments.py --tier A --runs 1 --node-count 8

  # Run from custom config file (e.g., tier_a_suite.json)
  python3 run_tier_experiments.py --config tier_a_suite.json --extended --runs 3

  # List all available experiments
  python3 run_tier_experiments.py --list
        """
    )
    
    parser.add_argument("--baseline", action="store_true",
                       help="Run baseline experiment only")
    parser.add_argument("--tier", type=str, choices=["A", "B", "C", "D", "E"],
                       help="Run specific tier (A, B, C, D, or E). Note: Tier E (20 tx/s) requires different snapshot.")
    parser.add_argument("--extended", action="store_true",
                       help="Run full suite (all experiments)")
    parser.add_argument("--experiment", type=str,
                       help="Run specific experiment by name")
    parser.add_argument("--list", action="store_true",
                       help="List all available tier experiments")
    parser.add_argument("--runs", type=int, default=4,
                       help="Number of replications per experiment (default: 4)")
    parser.add_argument("--node-count", type=int, default=None,
                       help="Override node count from config (e.g., --node-count 64)")
    parser.add_argument("--with-bootstrap", action="store_true",
                       help="Run bootstrap step before experiments (default: skip)")
    parser.add_argument("--config", type=str, default="tier_experiments.json",
                       help="Path to experiment config JSON (default: tier_experiments.json)")
    parser.add_argument("--snapshot-dir", type=str,
                       help="Path to snapshot directory (enables snapshot restore, skips funding)")

    args = parser.parse_args()

    runner = TierExperimentRunner(skip_bootstrap=not args.with_bootstrap, config_file=args.config, snapshot_dir=args.snapshot_dir)
    
    # Display node count override if specified
    if args.node_count:
        print(f"\n🔧 Node Count Override: Using {args.node_count} nodes for all experiments")
        print(f"   (Original configs specify 128 nodes)\n")
    
    if args.list:
        runner.list_experiments()
    elif args.baseline:
        runner.run_baseline(num_runs=args.runs, node_count_override=args.node_count)
    elif args.tier:
        if args.tier == "A":
            runner.run_tier_a(num_runs=args.runs, node_count_override=args.node_count)
        elif args.tier == "B":
            runner.run_tier_b(num_runs=args.runs, node_count_override=args.node_count)
        elif args.tier == "C":
            runner.run_tier_c(num_runs=args.runs, node_count_override=args.node_count)
        elif args.tier == "D":
            runner.run_tier_d(num_runs=args.runs, node_count_override=args.node_count)
    elif args.extended:
        runner.run_extended_suite(num_runs=args.runs, node_count_override=args.node_count)
    elif args.experiment:
        runner.run_single_experiment(args.experiment, num_runs=args.runs, node_count_override=args.node_count)
    else:
        parser.print_help()
        print("\n💡 Tip: Start with --baseline to verify your setup")

if __name__ == "__main__":
    main()

