import subprocess
import json
import time
import sys
import os
from datetime import datetime

def log_event(run_dir, message):
    ts = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(os.path.join(run_dir, "events.log"), "a") as f:
        f.write(f"{ts} {message}\n")

def get_block_count(node):
    try:
        res = subprocess.run([
            "docker", "exec", node, "bitcoin-cli", "-regtest", "getblockcount"
        ], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return int(res.stdout.strip())
    except:
        pass
    return 0

def get_node_info(node):
    try:
        res = subprocess.run([
            "docker", "exec", node, "bitcoin-cli", "-regtest", "getblockchaininfo"
        ], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return json.loads(res.stdout)
    except:
        pass
    return None

def get_connection_count(node):
    try:
        res = subprocess.run([
            "docker", "exec", node, "bitcoin-cli", "-regtest", "getconnectioncount"
        ], capture_output=True, text=True, timeout=5)
        if res.returncode == 0:
            return int(res.stdout.strip())
    except:
        pass
    return 0

def main():
    run_dir = sys.argv[1]
    crash_nodes = sys.argv[2].split(",")
    ref_nodes = sys.argv[3].split(",")
    
    synced = {node: False for node in crash_nodes}
    started = {node: False for node in crash_nodes}
    
    print(f"Monitoring sync for nodes: {crash_nodes}")
    
    while not all(synced.values()):
        # Get current network height from reference nodes
        net_h = 0
        for ref in ref_nodes:
            h = get_block_count(ref)
            if h > net_h:
                net_h = h
        
        for node in crash_nodes:
            if synced[node]:
                continue
            
            # Check if recovery has even started for this node
            if not started[node]:
                events_path = os.path.join(run_dir, "events.log")
                if os.path.exists(events_path):
                    with open(events_path, "r") as f:
                        if f"recovery_node_start node={node}" in f.read():
                            started[node] = True
                
            if not started[node]:
                continue
                
            # Check actual sync status
            info = get_node_info(node)
            if info:
                node_h = info.get("blocks", 0)
                ibd = info.get("initialblockdownload", True)
                peers = get_connection_count(node)
                
                if not ibd and node_h >= (net_h - 1) and peers > 0:
                    synced[node] = True
                    log_event(run_dir, f"node_sync_complete node={node} height={node_h} peers={peers}")
                    print(f"Node {node} is synced at height {node_h}")
        
        time.sleep(2)

if __name__ == "__main__":
    main()






