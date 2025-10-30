#!/usr/bin/env python3
"""
Bitcoin Fault Lab - Automated Experiment Runner
Runs systematic parameter sweeps for thesis-worthy research
"""

import os
import sys
import json
import time
import subprocess
import argparse
import itertools
from datetime import datetime, timezone
from pathlib import Path
import yaml

class ExperimentRunner:
    def __init__(self, base_dir=None, skip_bootstrap=True):
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.results_dir = self.base_dir / "results"
        self.experiment_log = self.results_dir / "experiment_log.json"
        self.skip_bootstrap = skip_bootstrap
        self.ensure_directories()
        
    def ensure_directories(self):
        """Create necessary directories"""
        self.results_dir.mkdir(exist_ok=True)
        
    def load_base_config(self):
        """Load base configuration from group_vars/all.yml"""
        config_file = self.base_dir / "group_vars" / "all.yml"
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    
    def log_experiment(self, config, status, run_id=None, error=None):
        """Log experiment details to JSON file"""
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": config,
            "status": status,
            "run_id": run_id,
            "error": str(error) if error else None
        }
        
        # Load existing log
        log_data = []
        if self.experiment_log.exists():
            with open(self.experiment_log, 'r') as f:
                log_data = json.load(f)
        
        log_data.append(log_entry)
        
        # Save updated log
        with open(self.experiment_log, 'w') as f:
            json.dump(log_data, f, indent=2)
    
    def create_config_override(self, params):
        """Create a temporary config file with parameter overrides"""
        override_file = self.base_dir / "temp_override.yml"
        with open(override_file, 'w') as f:
            yaml.dump(params, f)
        return override_file
    
    def run_playbook(self, playbook_name, extra_vars=None):
        """Run an Ansible playbook with optional extra variables"""
        cmd = ["ansible-playbook", f"playbooks/{playbook_name}", "-i", "inventory.ini"]
        
        if extra_vars:
            override_file = self.create_config_override(extra_vars)
            cmd.extend(["-e", f"@{override_file}"])
        
        print(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, cwd=self.base_dir, capture_output=True, text=True)
        
        # Clean up temporary file
        if extra_vars:
            override_file.unlink(missing_ok=True)
        
        return result
    
    def run_single_experiment(self, config_params):
        """Run a single experiment with given parameters"""
        print(f"\n{'='*80}")
        print(f"EXPERIMENT: {config_params}")
        print(f"{'='*80}")
        
        start_time = time.time()
        
        try:
            # Step 1: Bootstrap (if needed)
            if not self.skip_bootstrap:
                print("Step 1: Bootstrap...")
                # Use macOS bootstrap for now
                result = self.run_playbook("01_bootstrap_macos.yml", config_params)
                if result.returncode != 0:
                    raise Exception(f"Bootstrap failed: {result.stderr}")
            else:
                print("Step 1: Bootstrap... SKIPPED (use --with-bootstrap to enable)")
            
            # Step 2: Deploy
            print("Step 2: Deploy...")
            result = self.run_playbook("02_deploy.yml", config_params)
            if result.returncode != 0:
                raise Exception(f"Deploy failed: {result.stderr}")
            
            # Step 3: Run Experiment
            print("Step 3: Run Experiment...")
            result = self.run_playbook("03_run_experiment.yml", config_params)
            if result.returncode != 0:
                raise Exception(f"Experiment failed: {result.stderr}")
            
            # Step 4: Collect Data
            print("Step 4: Collect Data...")
            result = self.run_playbook("04_collect.yml", config_params)
            if result.returncode != 0:
                raise Exception(f"Data collection failed: {result.stderr}")
            
            # Step 5: Cleanup Docker resources (after analysis is complete)
            # self.cleanup_docker_resources()  # Temporarily disabled
            
            # Get the run ID from the latest results directory
            latest_run = self.get_latest_run_id()
            
            duration = time.time() - start_time
            print(f"✅ Experiment completed successfully in {duration:.1f}s")
            print(f"   Results: {latest_run}")
            
            self.log_experiment(config_params, "SUCCESS", latest_run)
            return latest_run
            
        except Exception as e:
            duration = time.time() - start_time
            print(f"❌ Experiment failed after {duration:.1f}s: {e}")
            self.log_experiment(config_params, "FAILED", error=e)
            return None
    
    def cleanup_docker_resources(self):
        """Clean up Docker containers and volumes after experiment"""
        try:
            print("Step 5: Cleanup...")
            # Stop and remove containers
            result = subprocess.run(
                ["docker", "compose", "-f", f"{self.base_dir}/docker-compose.yml", "down", "-v"],
                cwd=self.base_dir, capture_output=True, text=True
            )
            if result.returncode != 0:
                print(f"Warning: Docker cleanup had issues: {result.stderr}")
            
            # Remove any remaining containers with node names
            result = subprocess.run(
                ["docker", "ps", "-a", "--filter", "name=node", "--format", "{{.Names}}"],
                capture_output=True, text=True
            )
            if result.stdout.strip():
                node_containers = result.stdout.strip().split('\n')
                for container in node_containers:
                    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            
            print("✅ Cleanup completed")
        except Exception as e:
            print(f"Warning: Cleanup failed: {e}")
    
    def get_latest_run_id(self):
        if not self.results_dir.exists():
            return None
        
        run_dirs = [d for d in self.results_dir.iterdir() 
                   if d.is_dir() and d.name.startswith("202")]
        
        if not run_dirs:
            return None
        
        return max(run_dirs, key=lambda x: x.name).name
    
    def run_parameter_sweep(self, sweep_config):
        """Run a parameter sweep experiment"""
        print(f"Starting parameter sweep with {len(sweep_config)} configurations...")
        
        successful_runs = []
        failed_runs = []
        
        for i, params in enumerate(sweep_config, 1):
            print(f"\n[{i}/{len(sweep_config)}] Running configuration...")
            
            run_id = self.run_single_experiment(params)
            if run_id:
                successful_runs.append((params, run_id))
            else:
                failed_runs.append(params)
        
        # Summary
        print(f"\n{'='*80}")
        print(f"PARAMETER SWEEP COMPLETE")
        print(f"{'='*80}")
        print(f"Successful runs: {len(successful_runs)}")
        print(f"Failed runs: {len(failed_runs)}")
        
        if failed_runs:
            print(f"\nFailed configurations:")
            for params in failed_runs:
                print(f"  - {params}")
        
        return successful_runs, failed_runs

def generate_node_count_sweep():
    """Generate configurations for node count sweep"""
    base_config = {
        "tx_rate": 10,
        "crash_fraction": 0.25,
        "crash_duration_s": 300,
        "latency_ms": 100,
        "loss_pct": 2,
        "seed": 42
    }
    
    configs = []
    for node_count in [8, 16, 24, 32]:
        config = base_config.copy()
        config["node_count"] = node_count
        configs.append(config)
    
    return configs

def generate_fault_tolerance_sweep():
    """Generate configurations for fault tolerance sweep"""
    base_config = {
        "node_count": 32,
        "tx_rate": 10,
        "latency_ms": 100,
        "loss_pct": 2,
        "seed": 42
    }
    
    configs = []
    for crash_fraction in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]:
        for crash_duration in [60, 300, 600]:
            config = base_config.copy()
            config["crash_fraction"] = crash_fraction
            config["crash_duration_s"] = crash_duration
            configs.append(config)
    
    return configs

def generate_network_conditions_sweep():
    """Generate configurations for network conditions sweep"""
    base_config = {
        "node_count": 32,
        "tx_rate": 10,
        "crash_fraction": 0.25,
        "crash_duration_s": 300,
        "seed": 42
    }
    
    configs = []
    for latency in [0, 50, 100, 200, 500]:
        for loss in [0, 1, 2, 5, 10]:
            config = base_config.copy()
            config["latency_ms"] = latency
            config["loss_pct"] = loss
            configs.append(config)
    
    return configs

def generate_transaction_load_sweep():
    """Generate configurations for transaction load sweep"""
    base_config = {
        "node_count": 32,
        "crash_fraction": 0.25,
        "crash_duration_s": 300,
        "latency_ms": 100,
        "loss_pct": 2,
        "seed": 42
    }
    
    configs = []
    for tx_rate in [1, 5, 10, 20, 50, 100]:
        config = base_config.copy()
        config["tx_rate"] = tx_rate
        configs.append(config)
    
    return configs

def main():
    parser = argparse.ArgumentParser(description="Bitcoin Fault Lab Experiment Runner")
    parser.add_argument("--sweep", choices=["nodes", "faults", "network", "load", "full"], 
                       help="Run parameter sweep")
    parser.add_argument("--single", action="store_true", 
                       help="Run single experiment with current config")
    parser.add_argument("--config", type=str, 
                       help="JSON file with custom configuration parameters")
    parser.add_argument("--runs", type=int, default=1,
                       help="Number of runs per configuration (for statistical significance)")
    parser.add_argument("--with-bootstrap", action="store_true",
                       help="Run bootstrap step before experiments (default: skip)")
    
    args = parser.parse_args()
    
    runner = ExperimentRunner(skip_bootstrap=not args.with_bootstrap)
    
    if args.single:
        # Run single experiment with current configuration or custom config
        config = {}
        if args.config:
            with open(args.config, 'r') as f:
                config = json.load(f)
            
            # If config is a dict with experiment names, extract the first experiment's config
            if isinstance(config, dict) and len(config) == 1:
                first_key = list(config.keys())[0]
                if isinstance(config[first_key], dict):
                    config = config[first_key]
        
        runner.run_single_experiment(config)
    
    elif args.config:
        # Run with custom configuration
        with open(args.config, 'r') as f:
            config = json.load(f)
        
        # If config is a dict with experiment names, extract the first experiment's config
        if isinstance(config, dict) and len(config) == 1:
            first_key = list(config.keys())[0]
            if isinstance(config[first_key], dict):
                config = config[first_key]
        
        runner.run_single_experiment(config)
    
    elif args.sweep:
        # Generate sweep configurations
        if args.sweep == "nodes":
            configs = generate_node_count_sweep()
        elif args.sweep == "faults":
            configs = generate_fault_tolerance_sweep()
        elif args.sweep == "network":
            configs = generate_network_conditions_sweep()
        elif args.sweep == "load":
            configs = generate_transaction_load_sweep()
        elif args.sweep == "full":
            # Combine all sweeps (warning: this will take a LONG time)
            configs = (generate_node_count_sweep() + 
                      generate_fault_tolerance_sweep() + 
                      generate_network_conditions_sweep() + 
                      generate_transaction_load_sweep())
        
        # Replicate each configuration for statistical significance
        if args.runs > 1:
            replicated_configs = []
            for config in configs:
                for run_num in range(args.runs):
                    config_copy = config.copy()
                    config_copy["seed"] = config["seed"] + run_num  # Different seed per run
                    replicated_configs.append(config_copy)
            configs = replicated_configs
        
        runner.run_parameter_sweep(configs)
    
    else:
        print("Please specify --single, --sweep, or --config")
        parser.print_help()

if __name__ == "__main__":
    main()
