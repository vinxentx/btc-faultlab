#!/usr/bin/env python3
"""
Thesis-Focused Experiment Runner
Streamlined interface for Bitcoin fault tolerance research
"""

import os
import sys
import json
import subprocess
from typing import Dict, List, Tuple, Optional
import argparse
from pathlib import Path
from datetime import datetime
import yaml

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

class ThesisExperimentRunner:
    """Streamlined experiment runner for thesis research"""
    
    def __init__(self, base_dir: str = "."):
        self.base_dir = Path(base_dir)
        self.results_dir = self.base_dir / "results"
        self.configs_dir = self.base_dir / "thesis" / "configs"
        
    def run_single_experiment(self, config: dict) -> str:
        """Run a single experiment with given configuration"""
        print(f"Running experiment: {config.get('name', 'Unnamed')}")
        
        # Create config override
        config_file = self.base_dir / "temp_thesis_config.yml"
        with open(config_file, 'w') as f:
            yaml.dump(config, f)
        
        try:
            # Run experiment using existing infrastructure
            cmd = [
                "python3", "run_experiments.py", 
                "--single", 
                "--config", json.dumps(config)
            ]
            
            result = subprocess.run(cmd, cwd=self.base_dir, capture_output=True, text=True)
            
            if result.returncode == 0:
                # Extract run_id from output
                lines = result.stdout.split('\n')
                for line in lines:
                    if 'Results:' in line:
                        run_id = line.split('Results:')[-1].strip()
                        print(f"✅ Experiment completed: {run_id}")
                        return run_id
            else:
                print(f"❌ Experiment failed: {result.stderr}")
                return None
                
        finally:
            # Clean up temp file
            if config_file.exists():
                config_file.unlink()
    
    def run_recovery_dynamics_sweep(self, runs_per_config: int = 3) -> List[str]:
        """Run recovery dynamics parameter sweep"""
        from configs.thesis_experiments import generate_recovery_dynamics_sweep
        
        configs = generate_recovery_dynamics_sweep()
        completed_runs = []
        
        print(f"Running recovery dynamics sweep: {len(configs)} configurations, {runs_per_config} runs each")
        
        for i, base_config in enumerate(configs):
            print(f"\n--- Configuration {i+1}/{len(configs)} ---")
            
            for run in range(runs_per_config):
                config = base_config.copy()
                config['seed'] = 42 + run  # Different seed for each run
                config['name'] = f"recovery_dynamics_{i+1}_run_{run+1}"
                
                run_id = self.run_single_experiment(config)
                if run_id:
                    completed_runs.append(run_id)
        
        return completed_runs
    
    def run_network_partition_sweep(self, runs_per_config: int = 2) -> List[str]:
        """Run network partition parameter sweep"""
        from configs.thesis_experiments import generate_network_partition_sweep
        
        configs = generate_network_partition_sweep()
        completed_runs = []
        
        print(f"Running network partition sweep: {len(configs)} configurations, {runs_per_config} runs each")
        
        for i, base_config in enumerate(configs):
            print(f"\n--- Configuration {i+1}/{len(configs)} ---")
            
            for run in range(runs_per_config):
                config = base_config.copy()
                config['seed'] = 42 + run
                config['name'] = f"network_partition_{i+1}_run_{run+1}"
                
                run_id = self.run_single_experiment(config)
                if run_id:
                    completed_runs.append(run_id)
        
        return completed_runs
    
    def run_quick_validation(self, runs: int = 1) -> List[str]:
        """Run quick validation experiments"""
        from configs.thesis_experiments import generate_quick_validation_experiments
        
        configs = generate_quick_validation_experiments()
        completed_runs = []
        
        print(f"Running quick validation: {len(configs)} experiments")
        
        for i, config in enumerate(configs):
            config['name'] = f"quick_validation_{i+1}"
            run_id = self.run_single_experiment(config)
            if run_id:
                completed_runs.append(run_id)
        
        return completed_runs
    
    def run_full_thesis_sweep(self, runs_per_config: int = 2) -> List[str]:
        """Run complete thesis parameter sweep"""
        from configs.thesis_experiments import generate_all_thesis_experiments
        
        configs = generate_all_thesis_experiments()
        completed_runs = []
        
        print(f"Running full thesis sweep: {len(configs)} configurations, {runs_per_config} runs each")
        print("⚠️  WARNING: This will take many hours to complete!")
        
        for i, base_config in enumerate(configs):
            print(f"\n--- Configuration {i+1}/{len(configs)} ---")
            
            for run in range(runs_per_config):
                config = base_config.copy()
                config['seed'] = 42 + run
                config['name'] = f"thesis_full_{i+1}_run_{run+1}"
                
                run_id = self.run_single_experiment(config)
                if run_id:
                    completed_runs.append(run_id)
        
        return completed_runs

def main():
    parser = argparse.ArgumentParser(description="Thesis Experiment Runner")
    parser.add_argument("command", choices=[
        "single", "recovery", "partition", "quick", "full"
    ], help="Experiment command to run")
    parser.add_argument("--runs", type=int, default=2, help="Number of runs per configuration")
    parser.add_argument("--config", type=str, help="JSON config for single experiment")
    
    args = parser.parse_args()
    
    runner = ThesisExperimentRunner()
    
    if args.command == "single":
        if not args.config:
            print("❌ --config required for single experiment")
            return
        
        config = json.loads(args.config)
        run_id = runner.run_single_experiment(config)
        if run_id:
            print(f"✅ Single experiment completed: {run_id}")
    
    elif args.command == "recovery":
        runs = runner.run_recovery_dynamics_sweep(args.runs)
        print(f"✅ Recovery dynamics sweep completed: {len(runs)} runs")
    
    elif args.command == "partition":
        runs = runner.run_network_partition_sweep(args.runs)
        print(f"✅ Network partition sweep completed: {len(runs)} runs")
    
    elif args.command == "quick":
        runs = runner.run_quick_validation(args.runs)
        print(f"✅ Quick validation completed: {len(runs)} runs")
    
    elif args.command == "full":
        runs = runner.run_full_thesis_sweep(args.runs)
        print(f"✅ Full thesis sweep completed: {len(runs)} runs")

if __name__ == "__main__":
    main()
