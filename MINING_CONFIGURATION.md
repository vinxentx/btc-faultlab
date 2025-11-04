# Distributed Mining Configuration

## Overview

This project implements **distributed mining** across 5% of the network nodes to ensure resilience during fault injection experiments. This approach eliminates single points of failure in block production.

## Configuration

### Mining Node Selection

- **Mining Percentage**: 5% of total nodes (configurable via `--mining-percentage`)
- **Minimum Miners**: 2 nodes (ensures basic resilience)
- **Selection Strategy**: First N core nodes (node01, node02, ..., nodeN)

### Example Configurations

| Total Nodes | Mining Nodes (5%) | Node IDs |
|-------------|-------------------|----------|
| 4           | 2                 | node01-node02 |
| 32          | 2                 | node01-node02 |
| 64          | 3                 | node01-node03 |
| 128         | 6-7               | node01-node07 |

## How It Works

### 1. Initial Block Generation

During startup, the initial 201 blocks for wallet funding are distributed across all mining nodes:

```
Mining Configuration:
  Total nodes: 128
  Mining percentage: 5.0%
  Active miners: 7
  Mining nodes: node01:18443, node02:18443, ..., node07:18443
```

### 2. Runtime Block Production

For each block during the experiment:

1. **Health Check**: Query each mining node to find available miners
2. **Random Selection**: Randomly select one healthy miner
3. **Block Generation**: Call `generatetoaddress` on selected miner
4. **Failover**: If miner fails, automatically try next available miner

```python
# Pseudo-code for mining cycle
if time_to_mine():
    healthy_miner = get_healthy_miner(mining_nodes, auth)
    mine_block(healthy_miner, wallet_address)
```

### 3. Statistics & Monitoring

Mining statistics are logged every 10 blocks:

```
📊 Mining stats after 50 blocks:
   node01:18443: 8 blocks (16.0%)
   node02:18443: 6 blocks (12.0%)
   node03:18443: 9 blocks (18.0%)
   node04:18443: 7 blocks (14.0%)
   node05:18443: 8 blocks (16.0%)
   node06:18443: 6 blocks (12.0%)
   node07:18443: 6 blocks (12.0%)
```

## Resilience Benefits

### Without Distributed Mining (Old Setup)
- ❌ Single miner (node01) could be crashed
- ❌ 50% crash rate → 50% chance of mining failure
- ❌ No block production during miner downtime
- ❌ Impossible to measure network recovery

### With Distributed Mining (New Setup)
- ✅ 7 miners with 50% crash rate → 3-4 miners still available
- ✅ Automatic failover to healthy miners
- ✅ Continuous block production during crashes
- ✅ Realistic representation of Bitcoin network
- ✅ Valid measurement of network resilience

## Scientific Justification

### Why 5%?

1. **Realistic Modeling**: In real Bitcoin networks, a small percentage of nodes actively mine blocks
   - ~15,000-20,000 full nodes globally
   - ~10-20 major mining pools
   - Effective mining ratio: <1%

2. **Experimental Balance**:
   - Enough diversity for statistical significance
   - Not so many that it becomes unrealistic
   - Maintains clear distinction between mining and relay nodes

3. **Fault Tolerance Testing**:
   - With 128 nodes and 50% crash: 64 nodes crash
   - 7 miners → ~3-4 miners affected
   - Remaining 3-4 miners keep network operational
   - Tests critical resilience boundaries

## Usage

### Automatic (via Ansible)

The mining configuration is automatically set in `docker-compose.yml.j2`:

```yaml
command: >
  python -u /app/txgen.py 
    --rate {{ tx_rate }}
    --rpc http://{{ rpc_user }}:{{ rpc_pass }}@node01:18443
    --log /results/txlog.csv
    --node-count {{ node_count }}
    --mining-percentage 0.05
```

### Manual Testing

```bash
# For a 128-node network
python files/txgen.py \
  --rate 10 \
  --rpc http://user:pass@node01:18443 \
  --log /tmp/txlog.csv \
  --node-count 128 \
  --mining-percentage 0.05

# Output:
# 📊 Mining Configuration:
#    Total nodes: 128
#    Mining percentage: 5.0%
#    Active miners: 7
#    Mining nodes: node01:18443, ..., node07:18443
```

### Custom Mining Percentage

To adjust the mining percentage (e.g., for sensitivity analysis):

```yaml
# In docker-compose template
--mining-percentage 0.10  # 10% miners
--mining-percentage 0.03  # 3% miners
```

## Transaction Log Format

The transaction log now includes which miner produced each block:

```csv
submit_ts_utc,txid,miner
2024-01-15T10:30:45.123456+00:00,abc123...,
2024-01-15T10:30:50.234567+00:00,def456...,
```

## Troubleshooting

### All Miners Down

If all mining nodes are crashed:

```
❌ No mining nodes available! Network cannot produce blocks.
```

**Solution**: This is expected behavior during extreme fault scenarios (>50% crash affecting all miners). The experiment should capture this as a critical failure state.

### Mining Imbalance

If one miner produces significantly more blocks:

```
📊 Mining stats:
   node01:18443: 35 blocks (70%)  # Imbalanced
   node02:18443: 8 blocks (16%)
   ...
```

**Cause**: Other miners may be experiencing network issues or crashes.
**Impact**: Minimal - the network continues functioning, just with reduced miner diversity.

## References

- Implementation: `files/txgen.py`
- Configuration: `roles/bitcoin/templates/docker-compose.yml.j2`
- Experiment Design: `tier_experiments.json`

---

**Last Updated**: 2024-10-30
**Author**: btc-faultlab


