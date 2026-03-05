#!/usr/bin/env python3
"""
Crash Manager - Async crash injection and recovery for Bitcoin fault experiments

This script handles:
- Deterministic node selection (seeded)
- Burst mode: All nodes crash simultaneously
- Staggered mode: Nodes crash with time delays
- Recovery with configurable sync modes
- Event logging for scientific analysis

Designed to run in background while observation timer runs independently.
"""

import argparse
import subprocess
import time
import random
import os
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from timestamp_utils import now_unix_epoch, parse_unix_or_iso8601


def log_event(run_dir: str, event: str, details: str = ""):
    """Log an event with unix timestamp to events.log."""
    ts = now_unix_epoch()
    line = f"{ts} {event}"
    if details:
        line += f" {details}"
    with open(os.path.join(run_dir, "events.log"), "a") as f:
        f.write(line + "\n")
    print(f"[EVENT] {line}")


# Path to the crash signal file (read by block_scheduler to avoid targeting crashed nodes)
CRASH_SIGNAL_FILE = "/Users/vincenttietze/btc-faultlab/state/crashed_nodes.txt"

# Lock for thread-safe file operations during staggered recovery
import threading
_crash_signal_lock = threading.Lock()


def write_crash_signal(crash_nodes: list):
    """Write crash signal file BEFORE stopping containers.

    The block_scheduler reads this file to immediately exclude crashed nodes
    from mining attempts, avoiding connection errors entirely.
    """
    with _crash_signal_lock:
        try:
            with open(CRASH_SIGNAL_FILE, "w") as f:
                for node in crash_nodes:
                    f.write(f"{node}\n")
            print(f"[SIGNAL] Wrote {len(crash_nodes)} nodes to crash signal file")
        except Exception as e:
            print(f"[WARN] Could not write crash signal file: {e}")


def add_to_crash_signal(node: str):
    """Add a single node to the crash signal file (for staggered crashes).

    Thread-safe, appends to existing file.
    """
    with _crash_signal_lock:
        try:
            with open(CRASH_SIGNAL_FILE, "a") as f:
                f.write(f"{node}\n")
        except Exception as e:
            print(f"[WARN] Could not update crash signal file: {e}")


def remove_from_crash_signal(node: str):
    """Remove a single node from the crash signal file (called when node recovers).

    Thread-safe for concurrent recovery threads.
    """
    with _crash_signal_lock:
        try:
            current_nodes = set()
            if os.path.exists(CRASH_SIGNAL_FILE):
                with open(CRASH_SIGNAL_FILE, "r") as f:
                    current_nodes = {line.strip() for line in f if line.strip()}

            current_nodes.discard(node)

            if current_nodes:
                with open(CRASH_SIGNAL_FILE, "w") as f:
                    for n in current_nodes:
                        f.write(f"{n}\n")
            else:
                # Last node recovered - remove file entirely
                if os.path.exists(CRASH_SIGNAL_FILE):
                    os.remove(CRASH_SIGNAL_FILE)
        except Exception as e:
            print(f"[WARN] Could not update crash signal file: {e}")


def select_crash_nodes(node_count: int, crash_fraction: float, seed: int) -> list:
    """Deterministically select nodes to crash based on seed"""
    crash_count = int(node_count * crash_fraction)
    if crash_count == 0:
        return []

    all_nodes = list(range(1, node_count + 1))
    random.seed(seed)
    random.shuffle(all_nodes)
    crash_indices = all_nodes[:crash_count]

    # Format as node names (node01, node02, etc.)
    return [f"node{idx:02d}" for idx in crash_indices]


def docker_stop(node: str) -> bool:
    """Stop a Docker container"""
    try:
        result = subprocess.run(
            ["docker", "stop", node],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error stopping {node}: {e}")
        return False


def docker_start(node: str) -> bool:
    """Start a Docker container"""
    try:
        result = subprocess.run(
            ["docker", "start", node],
            capture_output=True,
            text=True,
            timeout=30
        )
        return result.returncode == 0
    except Exception as e:
        print(f"Error starting {node}: {e}")
        return False


def get_block_height(node: str) -> int:
    """Get current block height from a node"""
    try:
        result = subprocess.run(
            ["docker", "exec", node, "bitcoin-cli", "-regtest", "getblockcount"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except:
        pass
    return -1


def get_peer_count(node: str) -> int:
    """Get peer count from a node"""
    try:
        result = subprocess.run(
            ["docker", "exec", node, "bitcoin-cli", "-regtest", "getconnectioncount"],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except:
        pass
    return -1


def wait_for_sync(crash_nodes: list, reference_nodes: list, run_dir: str, timeout: int = 600) -> bool:
    """Wait for all crashed nodes to sync with the network"""
    start_time = time.time()

    while time.time() - start_time < timeout:
        # Get reference height from healthy nodes
        ref_heights = []
        for node in reference_nodes:
            h = get_block_height(node)
            if h > 0:
                ref_heights.append(h)

        if not ref_heights:
            time.sleep(2)
            continue

        target_height = max(ref_heights)

        # Check all crashed nodes
        all_synced = True
        for node in crash_nodes:
            height = get_block_height(node)
            peers = get_peer_count(node)

            if height < target_height or peers < 1:
                all_synced = False
                break

        if all_synced:
            print(f"All nodes synced to height {target_height}")
            return True

        time.sleep(2)

    print(f"Sync timeout after {timeout}s")
    return False


def inject_crash_burst(crash_nodes: list, run_dir: str, crash_duration_s: float) -> dict:
    """Burst mode: Stop all nodes simultaneously. Returns actual crash times."""
    # Signal scheduler BEFORE stopping containers (eliminates connection errors)
    write_crash_signal(crash_nodes)

    log_event(run_dir, "crash_start", f"mode=burst duration={crash_duration_s:.0f}s nodes={','.join(crash_nodes)}")
    actual_crash_times = {}

    # Stop all nodes in parallel
    with ThreadPoolExecutor(max_workers=len(crash_nodes)) as executor:
        futures = {}
        for node in crash_nodes:
            futures[executor.submit(docker_stop, node)] = node

        for i, future in enumerate(as_completed(futures)):
            node = futures[future]
            success = future.result()
            actual_crash_times[node] = time.time()  # Record ACTUAL crash time
            log_event(run_dir, "node_crash", f"node={node} index={i}")

    log_event(run_dir, "crash_complete")
    return actual_crash_times


def inject_crash_staggered(crash_nodes: list, run_dir: str, stagger_period_s: float) -> dict:
    """Staggered mode: Stop nodes with time delays. Returns actual crash times."""
    interval = stagger_period_s / len(crash_nodes) if crash_nodes else 0
    actual_crash_times = {}

    log_event(run_dir, "crash_start", f"mode=staggered interval={interval:.2f}s nodes={','.join(crash_nodes)}")

    for i, node in enumerate(crash_nodes):
        if i > 0:
            time.sleep(interval)

        # Signal scheduler JUST BEFORE stopping this specific node
        add_to_crash_signal(node)

        docker_stop(node)
        actual_crash_times[node] = time.time()  # Record ACTUAL crash time
        log_event(run_dir, "node_crash", f"node={node} index={i}")

    log_event(run_dir, "crash_complete")
    return actual_crash_times


def start_recovery_async(crash_nodes: list, run_dir: str, crash_duration_s: float,
                         reference_nodes: list, skip_sync: bool = False):
    """Start recovery threads that wait independently for each node's crash_duration.

    This runs IN PARALLEL with crash injection. Each thread:
    1. Waits for its node's crash event to appear in the log
    2. Calculates remaining wait time based on crash_duration
    3. Starts the container after crash_duration has elapsed
    """
    recovery_start_logged = [False]  # Use list to allow mutation in nested function

    def recover_node(node: str, index: int):
        # Wait for this node's crash event to appear in the log
        events_log = os.path.join(run_dir, "events.log")
        crash_time = None

        while crash_time is None:
            time.sleep(0.2)
            try:
                with open(events_log, 'r') as f:
                    for line in f:
                        if f"node_crash node={node}" in line:
                            # Parse timestamp from event
                            ts_str = line.split()[0]
                            crash_time = parse_unix_or_iso8601(ts_str).timestamp()
                            break
            except:
                pass

        # Calculate remaining wait time
        now = time.time()
        elapsed = now - crash_time
        remaining = crash_duration_s - elapsed

        if remaining > 0:
            time.sleep(remaining)

        # Start container
        docker_start(node)

        # Remove node from crash signal file so scheduler can target it again
        remove_from_crash_signal(node)

        log_event(run_dir, "recovery_node_start", f"node={node} index={index}")

        # Log recovery_start only once (for first node that finishes)
        if not recovery_start_logged[0]:
            recovery_start_logged[0] = True
            log_event(run_dir, "recovery_start", f"nodes={','.join(crash_nodes)}")

    # Start all recovery threads immediately (they will wait for their crash events)
    executor = ThreadPoolExecutor(max_workers=len(crash_nodes))
    futures = []
    for i, node in enumerate(crash_nodes):
        futures.append(executor.submit(recover_node, node, i))

    return executor, futures, reference_nodes, skip_sync


def wait_for_recovery_complete(executor, futures, crash_nodes: list, run_dir: str,
                                reference_nodes: list, skip_sync: bool):
    """Wait for all recovery threads to complete and log final event."""
    # Wait for all recovery threads
    for future in as_completed(futures):
        future.result()

    executor.shutdown(wait=True)

    # Log when all containers are running (before sync wait)
    # This enables accurate block_catchup_time measurement in metrics.py
    log_event(run_dir, "all_containers_running", f"nodes={','.join(crash_nodes)}")

    # Wait for sync (unless skipped for local testing)
    if skip_sync:
        print("Skipping sync wait (--skip-sync)")
        sync_success = True
    else:
        print("Waiting for nodes to sync...")
        sync_success = wait_for_sync(crash_nodes, reference_nodes, run_dir)

    log_event(run_dir, "recovery_complete", f"nodes={','.join(crash_nodes)} sync={'ok' if sync_success else 'timeout'}")

    # Note: crash signal file is already cleared incrementally as each node recovers
    # (see remove_from_crash_signal() in recover_node)


def main():
    parser = argparse.ArgumentParser(description="Crash Manager for Bitcoin fault experiments")
    parser.add_argument("--run-dir", required=True, help="Path to run directory")
    parser.add_argument("--node-count", type=int, required=True, help="Total number of nodes")
    parser.add_argument("--crash-fraction", type=float, required=True, help="Fraction of nodes to crash")
    parser.add_argument("--crash-duration", type=float, required=True, help="Crash duration in seconds")
    parser.add_argument("--crash-mode", choices=["burst", "staggered"], default="burst")
    parser.add_argument("--stagger-period", type=float, default=None, help="Stagger period (default: crash_duration)")
    parser.add_argument("--seed", type=int, required=True, help="Random seed for node selection")
    parser.add_argument("--pre-crash-delay", type=float, default=30, help="Delay before crash injection (for baseline)")
    parser.add_argument("--skip-sync", action="store_true", help="Skip sync waiting (for local testing)")
    args = parser.parse_args()

    run_dir = args.run_dir

    # Select nodes to crash
    crash_nodes = select_crash_nodes(args.node_count, args.crash_fraction, args.seed)

    if not crash_nodes:
        print("No nodes to crash (crash_fraction=0 or too few nodes)")
        return

    print(f"Selected {len(crash_nodes)} nodes to crash: {crash_nodes}")

    # Select reference nodes (non-crashed nodes for sync verification)
    all_nodes = [f"node{i:02d}" for i in range(1, args.node_count + 1)]
    reference_nodes = [n for n in all_nodes if n not in crash_nodes][:3]

    # Pre-crash delay (allows baseline measurement)
    if args.pre_crash_delay > 0:
        print(f"Pre-crash baseline period: {args.pre_crash_delay}s")
        time.sleep(args.pre_crash_delay)

    # Start recovery threads FIRST (they will wait for crash events)
    # This ensures each node's recovery timer starts when IT crashes, not when all crash
    executor, futures, ref_nodes, skip = start_recovery_async(
        crash_nodes, run_dir, args.crash_duration, reference_nodes, args.skip_sync
    )

    # Inject crashes (recovery threads are watching for events)
    if args.crash_mode == "burst":
        inject_crash_burst(crash_nodes, run_dir, args.crash_duration)
    else:
        stagger_period = args.stagger_period if args.stagger_period else args.crash_duration
        inject_crash_staggered(crash_nodes, run_dir, stagger_period)

    # Wait for all recoveries to complete
    wait_for_recovery_complete(executor, futures, crash_nodes, run_dir, ref_nodes, skip)

    print("Crash manager completed")


if __name__ == "__main__":
    main()
