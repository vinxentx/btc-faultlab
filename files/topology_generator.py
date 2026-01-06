#!/usr/bin/env python3
"""
Topology Generator for Bitcoin FaultLab

Generates deterministic peer topologies for Bitcoin regtest networks.
Supports two modes:
  - hierarchical: Original 3-tier (Core mesh + Secondary + Outer round-robin)
  - hybrid: Core mesh + non-core k-regular random graph (more realistic)

Usage:
  python3 topology_generator.py --node-count 128 --seed 42 --mode hybrid
"""

import json
import random
import sys
import argparse
from typing import Dict, List, Set


def generate_hierarchical_topology(node_count: int) -> Dict[int, List[int]]:
    """
    Original 3-tier hierarchical topology for backward compatibility.

    - Core (8 nodes): Full mesh
    - Secondary (8 nodes): All core + ring neighbor
    - Outer (rest): 2 secondary nodes via round-robin
    """
    core_size = 8 if node_count >= 8 else node_count
    secondary_size = min(node_count - core_size, 8) if node_count > core_size else 0

    topology: Dict[int, List[int]] = {}

    # Core: full mesh (connect to higher-numbered core nodes only)
    for i in range(1, core_size + 1):
        topology[i] = [j for j in range(i + 1, core_size + 1)]

    # Secondary: all core + ring neighbor
    for i in range(core_size + 1, core_size + secondary_size + 1):
        peers = list(range(1, core_size + 1))  # All core nodes
        if i + 1 <= core_size + secondary_size:
            peers.append(i + 1)  # Ring neighbor
        topology[i] = peers

    # Outer: 2 secondary nodes (round-robin)
    for i in range(core_size + secondary_size + 1, node_count + 1):
        if secondary_size > 0:
            base = i - core_size - secondary_size - 1
            sec1 = core_size + (base % secondary_size) + 1
            sec2 = core_size + ((base + 4) % secondary_size) + 1
            if sec1 == sec2:
                sec2 = core_size + ((sec1 - core_size) % secondary_size) + 1
            topology[i] = [sec1, sec2]
        else:
            # No secondary tier - connect to first 2 core nodes
            topology[i] = [1, 2] if core_size >= 2 else [1]

    return topology


def generate_hybrid_topology(node_count: int, seed: int, k: int = 4) -> Dict[int, List[int]]:
    """
    Hybrid topology: Core full-mesh + non-core k-regular random graph.

    More realistic than hierarchical:
    - Core (8 nodes): Full mesh simulating mining pool clique
    - Non-core: Each has 1 core peer + (k-1) random non-core peers

    Args:
        node_count: Total number of nodes
        seed: Random seed for reproducibility
        k: Total degree for non-core nodes (default: 4)

    Returns:
        Dict mapping node_id -> list of peer node_ids to connect to
    """
    rng = random.Random(seed)

    core_size = 8 if node_count >= 8 else node_count
    core_nodes = list(range(1, core_size + 1))
    non_core_nodes = list(range(core_size + 1, node_count + 1))

    topology: Dict[int, List[int]] = {}

    # --- Core Tier: Full mesh (unchanged) ---
    for i in core_nodes:
        topology[i] = [j for j in core_nodes if j > i]

    if not non_core_nodes:
        return topology

    # --- Non-Core Tier: k-regular random graph ---
    non_core_peers_needed = k - 1  # k-1 non-core peers per node (1 slot for core)

    # Step 1: Assign each non-core node to a random core node
    for node in non_core_nodes:
        core_peer = rng.choice(core_nodes)
        topology[node] = [core_peer]

    # Step 2: Generate random non-core edges using configuration model
    if len(non_core_nodes) < 2:
        return topology

    # Create stub list: each node contributes (k-1) stubs
    stubs = []
    for node in non_core_nodes:
        stubs.extend([node] * non_core_peers_needed)

    edges: Set[tuple] = set()

    max_attempts = 100
    for attempt in range(max_attempts):
        rng.shuffle(stubs)
        edges.clear()
        valid = True

        # Pair adjacent stubs
        for i in range(0, len(stubs) - 1, 2):
            u, v = stubs[i], stubs[i + 1]

            # Skip self-loops
            if u == v:
                valid = False
                break

            # Normalize edge (smaller node first)
            edge = (min(u, v), max(u, v))

            # Skip multi-edges
            if edge in edges:
                valid = False
                break

            edges.add(edge)

        if valid:
            break
    else:
        # Fallback: use greedy approach if configuration model fails
        edges = _fallback_regular_edges(non_core_nodes, non_core_peers_needed, rng)

    # Step 3: Add edges to topology (lower-numbered node connects to higher)
    for u, v in edges:
        topology[u].append(v)

    return topology


def _fallback_regular_edges(nodes: List[int], degree: int, rng: random.Random) -> Set[tuple]:
    """Fallback for when configuration model fails - uses greedy approach."""
    edges: Set[tuple] = set()
    remaining = {node: degree for node in nodes}

    shuffled_nodes = nodes.copy()
    rng.shuffle(shuffled_nodes)

    for u in shuffled_nodes:
        candidates = [
            v for v in nodes
            if v != u and remaining[v] > 0 and (min(u, v), max(u, v)) not in edges
        ]
        rng.shuffle(candidates)

        while remaining[u] > 0 and candidates:
            v = candidates.pop()
            if remaining[v] > 0:
                edge = (min(u, v), max(u, v))
                edges.add(edge)
                remaining[u] -= 1
                remaining[v] -= 1

    return edges


def validate_topology(topology: Dict[int, List[int]], node_count: int, k: int = 4, mode: str = "hybrid") -> bool:
    """Validate the generated topology meets constraints."""
    core_size = 8 if node_count >= 8 else node_count
    errors = []

    # Check core nodes have full mesh (connect to higher-numbered only)
    for i in range(1, core_size + 1):
        expected_peers = list(range(i + 1, core_size + 1))
        actual_peers = sorted(topology.get(i, []))
        if actual_peers != expected_peers:
            errors.append(f"Core node {i}: expected peers {expected_peers}, got {actual_peers}")

    # Check non-core nodes
    if mode == "hybrid":
        for i in range(core_size + 1, node_count + 1):
            peers = topology.get(i, [])
            core_peers = [p for p in peers if p <= core_size]

            if len(core_peers) != 1:
                errors.append(f"Node {i}: expected 1 core peer, got {len(core_peers)} ({core_peers})")

    if errors:
        for err in errors:
            print(f"VALIDATION ERROR: {err}", file=sys.stderr)
        return False

    return True


def get_topology_stats(topology: Dict[int, List[int]], node_count: int) -> dict:
    """Calculate statistics about the topology."""
    core_size = 8 if node_count >= 8 else node_count

    total_connections = sum(len(peers) for peers in topology.values())
    core_connections = sum(len(topology.get(i, [])) for i in range(1, core_size + 1))
    non_core_to_core = sum(
        len([p for p in topology.get(i, []) if p <= core_size])
        for i in range(core_size + 1, node_count + 1)
    )
    non_core_to_non_core = total_connections - core_connections - non_core_to_core

    return {
        "node_count": node_count,
        "core_size": core_size,
        "non_core_size": node_count - core_size,
        "total_connections": total_connections,
        "core_mesh_connections": core_connections,
        "non_core_to_core": non_core_to_core,
        "non_core_to_non_core": non_core_to_non_core,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Generate Bitcoin FaultLab network topology",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate hybrid topology for 128 nodes
  python3 topology_generator.py --node-count 128 --seed 42 --mode hybrid

  # Generate hierarchical (original) topology
  python3 topology_generator.py --node-count 128 --mode hierarchical

  # Validate and show stats
  python3 topology_generator.py --node-count 64 --seed 123 --validate --stats
        """
    )
    parser.add_argument("--node-count", type=int, required=True,
                       help="Total number of nodes")
    parser.add_argument("--seed", type=int, default=42,
                       help="Random seed for reproducibility (default: 42)")
    parser.add_argument("--mode", choices=["hierarchical", "hybrid"], default="hybrid",
                       help="Topology mode (default: hybrid)")
    parser.add_argument("--k", type=int, default=4,
                       help="Degree for non-core nodes in hybrid mode (default: 4)")
    parser.add_argument("--output", type=str, default="-",
                       help="Output file (- for stdout)")
    parser.add_argument("--validate", action="store_true",
                       help="Validate topology after generation")
    parser.add_argument("--stats", action="store_true",
                       help="Print topology statistics to stderr")

    args = parser.parse_args()

    # Generate topology
    if args.mode == "hierarchical":
        topology = generate_hierarchical_topology(args.node_count)
    else:
        topology = generate_hybrid_topology(args.node_count, args.seed, args.k)

    # Validate if requested
    if args.validate:
        if not validate_topology(topology, args.node_count, args.k, args.mode):
            print("Topology validation FAILED", file=sys.stderr)
            sys.exit(1)
        print("Topology validation PASSED", file=sys.stderr)

    # Print stats if requested
    if args.stats:
        stats = get_topology_stats(topology, args.node_count)
        print(f"Topology stats: {json.dumps(stats)}", file=sys.stderr)

    # Output as JSON (node IDs as strings for JSON compatibility)
    output_topology = {str(k): v for k, v in topology.items()}

    if args.output == "-":
        json.dump(output_topology, sys.stdout)
    else:
        with open(args.output, 'w') as f:
            json.dump(output_topology, f, indent=2)


if __name__ == "__main__":
    main()
