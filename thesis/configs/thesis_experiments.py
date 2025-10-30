#!/usr/bin/env python3
"""
Thesis-Focused Experiment Configurations
Bitcoin Fault Tolerance Research - Recovery Dynamics Analysis
"""

def generate_recovery_dynamics_sweep():
    """Core recovery behavior under different conditions"""
    base_config = {
        "node_count": 16,
        "tx_rate": 10,
        "latency_ms": 100,
        "loss_pct": 2,
        "seed": 42,
        "warmup_s": 120,
        "observe_s": 1800,  # 30 minutes to observe recovery
        "cooldown_s": 60
    }
    
    configs = []
    
    # Recovery timing scenarios
    for crash_fraction in [0.1, 0.25, 0.5]:  # 1-2, 4, 8 nodes
        for crash_duration in [60, 300, 900]:  # 1min, 5min, 15min
            for recovery_mode in ["cold", "fast"]:
                config = base_config.copy()
                config.update({
                    "crash_fraction": crash_fraction,
                    "crash_duration_s": crash_duration,
                    "recovery_mode": recovery_mode,
                    "crash_mode": "burst"
                })
                configs.append(config)
    
    return configs

def generate_network_partition_sweep():
    """Test network split scenarios"""
    base_config = {
        "node_count": 16,
        "tx_rate": 10,
        "crash_fraction": 0.0,  # No crashes, just partitions
        "seed": 42,
        "warmup_s": 60,
        "observe_s": 1200,  # 20 minutes
        "cooldown_s": 30
    }
    
    configs = []
    
    # Network partition scenarios
    for latency in [0, 100, 500, 1000]:  # Increasing isolation
        for loss in [0, 5, 10, 20]:  # Packet loss simulating partitions
            config = base_config.copy()
            config.update({
                "latency_ms": latency,
                "loss_pct": loss
            })
            configs.append(config)
    
    return configs

def generate_load_during_recovery_sweep():
    """Test recovery under different transaction loads"""
    base_config = {
        "node_count": 16,
        "crash_fraction": 0.25,  # 4 nodes crash
        "crash_duration_s": 300,  # 5 minutes
        "latency_ms": 100,
        "loss_pct": 2,
        "seed": 42,
        "warmup_s": 120,
        "observe_s": 1800,  # 30 minutes
        "cooldown_s": 60
    }
    
    configs = []
    
    # Different loads during recovery
    for tx_rate in [1, 5, 10, 20, 50]:  # Varying transaction rates
        config = base_config.copy()
        config.update({
            "tx_rate": tx_rate
        })
        configs.append(config)
    
    return configs

def generate_cascading_failure_sweep():
    """Test cascading failure scenarios"""
    base_config = {
        "node_count": 16,
        "tx_rate": 10,
        "latency_ms": 200,  # Higher latency to stress the system
        "loss_pct": 5,
        "seed": 42,
        "warmup_s": 180,
        "observe_s": 2400,  # 40 minutes to observe cascades
        "cooldown_s": 120
    }
    
    configs = []
    
    # Cascading failure patterns
    for crash_fraction in [0.1, 0.2, 0.3, 0.4]:  # Increasing failure rates
        for crash_mode in ["burst", "staggered"]:
            config = base_config.copy()
            config.update({
                "crash_fraction": crash_fraction,
                "crash_mode": crash_mode,
                "crash_duration_s": 600,  # 10 minutes
                "recovery_mode": "cold"  # Force full recovery
            })
            configs.append(config)
    
    return configs

def generate_blockchain_size_impact_sweep():
    """Test recovery with different blockchain sizes"""
    base_config = {
        "node_count": 16,
        "tx_rate": 10,
        "crash_fraction": 0.25,
        "crash_duration_s": 300,
        "latency_ms": 100,
        "loss_pct": 2,
        "seed": 42,
        "recovery_mode": "cold"
    }
    
    configs = []
    
    # Different blockchain sizes (simulated by different experiment durations)
    for blockchain_age_hours in [1, 6, 12, 24]:  # Different blockchain sizes
        config = base_config.copy()
        config.update({
            "warmup_s": 300,  # 5 minutes warmup
            "observe_s": blockchain_age_hours * 3600,  # Hours to seconds
            "cooldown_s": 300
        })
        configs.append(config)
    
    return configs

def generate_all_thesis_experiments():
    """Generate all experiment configurations for thesis"""
    all_configs = []
    
    # Core recovery dynamics
    all_configs.extend(generate_recovery_dynamics_sweep())
    
    # Network partition scenarios
    all_configs.extend(generate_network_partition_sweep())
    
    # Load during recovery
    all_configs.extend(generate_load_during_recovery_sweep())
    
    # Cascading failures
    all_configs.extend(generate_cascading_failure_sweep())
    
    # Blockchain size impact
    all_configs.extend(generate_blockchain_size_impact_sweep())
    
    return all_configs

def generate_quick_validation_experiments():
    """Generate quick experiments for validation"""
    configs = []
    
    # Quick recovery test
    configs.append({
        "node_count": 8,
        "tx_rate": 5,
        "crash_fraction": 0.25,  # 2 nodes
        "crash_duration_s": 60,
        "latency_ms": 50,
        "loss_pct": 1,
        "seed": 42,
        "warmup_s": 30,
        "observe_s": 300,  # 5 minutes
        "cooldown_s": 30,
        "recovery_mode": "cold",
        "crash_mode": "burst"
    })
    
    # Quick partition test
    configs.append({
        "node_count": 8,
        "tx_rate": 5,
        "crash_fraction": 0.0,
        "latency_ms": 200,
        "loss_pct": 10,
        "seed": 42,
        "warmup_s": 30,
        "observe_s": 300,
        "cooldown_s": 30
    })
    
    return configs

if __name__ == "__main__":
    configs = generate_all_thesis_experiments()
    print(f"Generated {len(configs)} thesis experiment configurations")
    
    quick_configs = generate_quick_validation_experiments()
    print(f"Generated {len(quick_configs)} quick validation experiments")
    
    # Print sample configurations
    print("\nSample Recovery Dynamics Config:")
    print(json.dumps(configs[0], indent=2))
    
    print("\nSample Quick Validation Config:")
    print(json.dumps(quick_configs[0], indent=2))
