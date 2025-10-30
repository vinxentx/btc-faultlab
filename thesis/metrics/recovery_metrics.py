#!/usr/bin/env python3
"""
Enhanced Recovery Metrics for Bitcoin Fault Tolerance Thesis
Focuses on crash recovery dynamics and performance degradation
"""

import os
import json
import time
import subprocess
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

class RecoveryMetricsCollector:
    """Collects comprehensive recovery metrics for thesis analysis"""
    
    def __init__(self, workdir: str):
        self.workdir = workdir
        self.metrics = {}
        
    def collect_node_recovery_metrics(self, node_name: str, crash_time: datetime) -> Dict:
        """Collect detailed recovery metrics for a specific node"""
        metrics = {
            'node_name': node_name,
            'crash_time': crash_time.isoformat(),
            'recovery_start_time': None,
            'recovery_complete_time': None,
            'sync_time_s': None,
            'blocks_downloaded': 0,
            'bandwidth_usage_mbps': 0.0,
            'memory_peak_mb': 0.0,
            'recovery_success': False,
            'error_messages': []
        }
        
        try:
            # Check if node is running
            result = subprocess.run(
                ['docker', 'inspect', '-f', '{{.State.Status}}', node_name],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode == 0 and 'running' in result.stdout:
                metrics['recovery_start_time'] = datetime.now(timezone.utc).isoformat()
                
                # Monitor recovery progress
                recovery_metrics = self._monitor_recovery_progress(node_name)
                metrics.update(recovery_metrics)
                
        except Exception as e:
            metrics['error_messages'].append(f"Recovery monitoring failed: {str(e)}")
            
        return metrics
    
    def _monitor_recovery_progress(self, node_name: str) -> Dict:
        """Monitor the recovery progress of a node"""
        start_time = time.time()
        max_wait_time = 1800  # 30 minutes max
        check_interval = 10   # Check every 10 seconds
        
        last_block_height = 0
        peak_memory = 0.0
        total_bandwidth = 0.0
        
        while time.time() - start_time < max_wait_time:
            try:
                # Check if node is fully synced
                result = subprocess.run([
                    'docker', 'exec', node_name, 
                    'bitcoin-cli', '-regtest', 'getblockchaininfo'
                ], capture_output=True, text=True, timeout=5)
                
                if result.returncode == 0:
                    blockchain_info = json.loads(result.stdout)
                    current_height = blockchain_info.get('blocks', 0)
                    
                    # Check if synced
                    if blockchain_info.get('verificationprogress', 0) >= 0.999:
                        return {
                            'recovery_complete_time': datetime.now(timezone.utc).isoformat(),
                            'sync_time_s': time.time() - start_time,
                            'blocks_downloaded': current_height - last_block_height,
                            'memory_peak_mb': peak_memory,
                            'bandwidth_usage_mbps': total_bandwidth / (time.time() - start_time),
                            'recovery_success': True
                        }
                    
                    # Track progress
                    if current_height > last_block_height:
                        blocks_downloaded = current_height - last_block_height
                        last_block_height = current_height
                        
                        # Estimate bandwidth (rough calculation)
                        total_bandwidth += blocks_downloaded * 0.001  # MB per block estimate
                
                # Check memory usage
                memory_result = subprocess.run([
                    'docker', 'stats', '--no-stream', '--format', 
                    '{{.MemUsage}}', node_name
                ], capture_output=True, text=True, timeout=5)
                
                if memory_result.returncode == 0:
                    memory_str = memory_result.stdout.strip()
                    if 'MiB' in memory_str:
                        memory_mb = float(memory_str.replace('MiB', '').strip())
                        peak_memory = max(peak_memory, memory_mb)
                
                time.sleep(check_interval)
                
            except Exception as e:
                print(f"Error monitoring recovery: {e}")
                time.sleep(check_interval)
        
        # Timeout
        return {
            'recovery_complete_time': None,
            'sync_time_s': time.time() - start_time,
            'blocks_downloaded': last_block_height,
            'memory_peak_mb': peak_memory,
            'bandwidth_usage_mbps': total_bandwidth / (time.time() - start_time),
            'recovery_success': False,
            'error_messages': ['Recovery timeout after 30 minutes']
        }
    
    def collect_consensus_metrics(self, run_dir: str) -> Dict:
        """Collect consensus quality metrics"""
        metrics = {
            'reorg_events': 0,
            'orphan_blocks': 0,
            'fork_depth_max': 0,
            'consensus_delay_s': 0.0,
            'chain_tips_count': 0
        }
        
        try:
            # Check chain tips
            chaintips_file = os.path.join(run_dir, 'chaintips.json')
            if os.path.exists(chaintips_file):
                with open(chaintips_file, 'r') as f:
                    chaintips = json.load(f)
                    metrics['chain_tips_count'] = len(chaintips)
                    
                    # Calculate fork depth
                    if chaintips:
                        heights = [tip.get('height', 0) for tip in chaintips]
                        if heights:
                            max_height = max(heights)
                            min_height = min(heights)
                            metrics['fork_depth_max'] = max_height - min_height
            
            # Analyze node logs for reorg events
            reorg_count = 0
            for i in range(1, 33):  # Check up to 32 nodes
                log_file = os.path.join(run_dir, f'node{i:02d}.log')
                if os.path.exists(log_file):
                    with open(log_file, 'r') as f:
                        content = f.read()
                        reorg_count += content.count('reorganize')
            
            metrics['reorg_events'] = reorg_count
            
        except Exception as e:
            print(f"Error collecting consensus metrics: {e}")
            
        return metrics
    
    def collect_resource_metrics(self, run_dir: str) -> Dict:
        """Collect resource usage metrics during experiment"""
        metrics = {
            'cpu_usage_avg': 0.0,
            'memory_usage_avg': 0.0,
            'network_io_total_mb': 0.0,
            'disk_io_total_mb': 0.0
        }
        
        try:
            # This would require more sophisticated monitoring
            # For now, we'll estimate based on log file sizes
            total_log_size = 0
            log_count = 0
            
            for i in range(1, 33):
                log_file = os.path.join(run_dir, f'node{i:02d}.log')
                if os.path.exists(log_file):
                    size = os.path.getsize(log_file)
                    total_log_size += size
                    log_count += 1
            
            if log_count > 0:
                metrics['network_io_total_mb'] = total_log_size / (1024 * 1024)
                
        except Exception as e:
            print(f"Error collecting resource metrics: {e}")
            
        return metrics
    
    def generate_recovery_report(self, run_dir: str) -> Dict:
        """Generate comprehensive recovery report for a single experiment"""
        report = {
            'experiment_id': os.path.basename(run_dir),
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'recovery_metrics': {},
            'consensus_metrics': {},
            'resource_metrics': {},
            'summary': {}
        }
        
        # Collect all metrics
        report['consensus_metrics'] = self.collect_consensus_metrics(run_dir)
        report['resource_metrics'] = self.collect_resource_metrics(run_dir)
        
        # Generate summary
        report['summary'] = {
            'total_reorg_events': report['consensus_metrics']['reorg_events'],
            'max_fork_depth': report['consensus_metrics']['fork_depth_max'],
            'chain_tips': report['consensus_metrics']['chain_tips_count'],
            'network_io_mb': report['resource_metrics']['network_io_total_mb']
        }
        
        return report

if __name__ == "__main__":
    collector = RecoveryMetricsCollector(".")
    report = collector.generate_recovery_report("results/20250909T140338Z")
    print(json.dumps(report, indent=2))
