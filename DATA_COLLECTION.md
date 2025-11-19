# Data Collection Guide

## Overview

The experiment framework automatically collects comprehensive data about Bitcoin network behavior during fault injection experiments. This guide documents all collected data files and their formats.

---

## Collected Data Files

### Core Transaction Data

#### `txlog.csv`
**Description**: Aggregierter Transaktions-Überblick über alle Generator-Shards  
**Source**: Aggregiert aus `txgen_<id>_txlog.csv`  
**Format**:
```csv
tx_index,shard_id,submit_ts_utc,txid
42,a,2025-11-08T12:00:24.717910+00:00,6f3185c6fdc39b354de48aea45c092df28b1084503bebab22fe7b4930ae1f386
```

**Columns**:
- `tx_index`: Laufende Nummer (pro Shard) der übertragenen Transaktion
- `shard_id`: Kennung des Tx-Generator-Shards (`a` … `d`)
- `submit_ts_utc`: ISO 8601 timestamp when transaction was submitted
- `txid`: Transaction ID (hash)

**Usage**: Analyse von Submission-Patterns, Raten, Shard-Vergleichen und Timing

> 💡 Die Rohdaten pro Shard liegen weiterhin unter `txgen_<id>_txlog.csv` im Run-Verzeichnis.

---

#### `block_scheduler.log` ✨ NEW
**Description**: Deterministische Blockproduktion des Schedulers  
**Source**: `block_scheduler` Container  
**Format**: Plaintext Log mit Zeilen wie  
```
2025-11-08T12:00:30.012345+00:00 ⛏️  Block #42 auf node03:18443 (hash 0000000abc...)
2025-11-08T12:00:36.998000+00:00 ❌ Miner node05:18443 Fehler (code=-28): Loading block index...
```

**Usage**: 
- Überwachen des Blocktakts (Intervall, Failover)
- Identifizieren ausgefallener Miner oder RPC-Probleme
- Nachvollziehen der deterministischen Rotation

---

#### `confirmations.csv`
**Description**: Transaction confirmation analysis  
**Source**: Extracted from txlog.csv + blockchain data  
**Format**:
```csv
txid,submit_time,confirm_time,confirmations,block_height
6f3185c6...,2025-10-30T12:00:24+00:00,2025-10-30T12:00:29+00:00,6,102
```

**Columns**:
- `txid`: Transaction ID
- `submit_time`: When tx was submitted
- `confirm_time`: When tx received 6 confirmations
- `confirmations`: Number of confirmations at collection time
- `block_height`: Block height where tx was included

**Usage**: Measure confirmation latency, throughput, and reliability

---

### Node State Data

#### `node_health.csv` ✨ NEW
**Description**: Final health status of all nodes  
**Source**: Collected at experiment end  
**Format**:
```csv
node,status,block_height,peer_count
node01,running,245,7
node02,running,245,8
node03,stopped,N/A,N/A
```

**Columns**:
- `node`: Node identifier
- `status`: `running` or `stopped`
- `block_height`: Current block height (N/A if stopped)
- `peer_count`: Number of connected peers (N/A if stopped)

**Usage**:
- Identify which nodes crashed during experiment
- Detect consensus splits (differing block heights)
- Analyze network partitioning
- Measure recovery success rate

---

#### `nodeXX.log`
**Description**: Complete Bitcoin Core debug logs for each node  
**Source**: Docker logs from each node container  
**Format**: Standard Bitcoin Core log format
```
2025-10-30T12:00:22Z Bitcoin Core version v27.0
2025-10-30T12:00:22Z InitParameterInteraction: parameter interaction: -regtest=1 -> setting -txindex=1
```

**Usage**: Deep debugging, error analysis, consensus investigation

---

### Blockchain State Data

#### `chaintips.json`
**Description**: Chain tip status from node01  
**Source**: `bitcoin-cli getchaintips`  
**Format**: JSON
```json
[
  {
    "height": 245,
    "hash": "00000000abc123...",
    "branchlen": 0,
    "status": "active"
  }
]
```

**Usage**: Detect forks, orphaned blocks, consensus issues

---

#### `mempool.json`
**Description**: Mempool statistics from node01  
**Source**: `bitcoin-cli getmempoolinfo`  
**Format**: JSON
```json
{
  "size": 42,
  "bytes": 123456,
  "usage": 234567,
  "maxmempool": 300000000,
  "mempoolminfee": 0.00001000
}
```

**Usage**: Analyze transaction backlog, mempool pressure

---

### Experiment Metadata

#### `events.log`
**Description**: Experiment phase timeline  
**Format**: Timestamped events
```
2025-10-30T12:00:23Z start_warmup
2025-10-30T12:02:47Z after_netem
2025-10-30T12:02:47Z crash_plan nodes=node05,node12,node18 mode=burst duration=60s
2025-10-30T12:08:01Z end_observe
```

**Events**:
- `start_warmup`: Experiment warmup phase begins
- `after_netem`: Network impairments applied
- `crash_plan`: Which nodes will crash and when
- `end_observe`: Observation period ends

**Usage**: Correlate performance metrics with experiment phases

---

#### `metadata.yml`
**Description**: Experiment configuration and parameters  
**Format**: YAML
```yaml
node_count: 32
tx_rate: 10
crash_fraction: 0.25
crash_duration_s: 60
crash_mode: burst
warmup_s: 120
observe_s: 300
seed: 42
```

**Usage**: Document experiment conditions for reproducibility

---

### Mining Analytics ✨ NEW

#### `mining_summary.json`
**Description**: Mining statistics summary  
**Source**: Generiert aus `block_scheduler.log` (über das Hilfsfile `mining.csv`)  
**Format**: JSON
```json
{
  "total_blocks": 245,
  "miners": {
    "node01:18443": {
      "blocks": 87,
      "percentage": 35.51
    },
    "node02:18443": {
      "blocks": 82,
      "percentage": 33.47
    },
    "node03:18443": {
      "blocks": 76,
      "percentage": 31.02
    }
  },
  "unique_miners": 3
}
```

**Usage**:
- Quick overview of mining distribution
- Verify mining resilience
- Identify mining failures
- Compare with expected 5% mining nodes

---

### Analysis Outputs

#### `metrics.json`
**Description**: Computed performance metrics  
**Source**: `analysis/metrics.py`  
**Format**: JSON (complex structure)

**Includes**:
- Confirmation latency (mean, median, p95, p99)
- Throughput (tx/s)
- Block propagation time
- Fork detection
- Consensus health

**Usage**: Primary output for comparative analysis

---

#### `plots/`
**Description**: Visualization plots  
**Files**:
- `performance_timeline.png`: Latency and throughput over time
- `fault_impact_analysis.png`: Before/during/after crash comparison
- `system_health_dashboard.png`: Multi-metric health overview

---

## Data Collection Workflow

```
Experiment Run
    │
    ├─> Txgen-Shards schreiben individuelle Logs in eigene Volumes
    ├─> Block-Scheduler protokolliert Mining-Events (STDOUT)
    │
    ├─> Nodes run and generate logs
    │
    └─> At experiment end (playbooks/04_collect.yml):
        │
        ├─> Kopiere txgen_*-Artefakte & aggregiere zu txlog*.csv
        ├─> Wandle block_scheduler.log → mining.csv
        ├─> Dump all node logs (nodeXX.log)
        ├─> Query chain tips and mempool (JSON)
        ├─> Collect node health status (node_health.csv)
        ├─> Generate mining summary (mining_summary.json)
        ├─> Extract confirmations (confirmations.csv)
        └─> Run metrics.py to compute analysis
```

---

## Analyzing Results

### Quick Check
```bash
# Latest experiment
cd results/$(ls -t results/ | head -1)

# How many blocks mined?
wc -l mining.csv

# Mining distribution
cut -d, -f3 mining.csv | tail -n +2 | sort | uniq -c

# Which nodes crashed?
grep stopped node_health.csv

# Confirmation latency
python3 -c "import pandas as pd; df=pd.read_csv('confirmations.csv'); \
  df['latency']=(pd.to_datetime(df['confirm_time'])-pd.to_datetime(df['submit_time'])).dt.total_seconds(); \
  print(f'Mean: {df.latency.mean():.2f}s, P95: {df.latency.quantile(0.95):.2f}s')"
```

### Full Analysis
```bash
# Run metrics computation
python3 analysis/metrics.py --run-dir results/20251030T120022Z

# View results
cat results/20251030T120022Z/metrics.json | python3 -m json.tool

# View plots
open results/20251030T120022Z/plots/
```

### Compare Experiments
```bash
# Compare multiple runs
python3 analysis/compare_experiments.py \
  --run1 results/20251030T120022Z \
  --run2 results/20251030T140015Z
```

---

## Mining Analysis Specifics

### Verify Mining Resilience

Check if mining continued during crashes:
```bash
cd results/$(ls -t results/ | head -1)

# Get crash times from events.log
grep crash_plan events.log

# Check mining during crash period
# (Adjust times based on your events.log)
awk -F, 'NR>1 {print $1}' mining.csv | \
  grep "2025-10-30T12:03" | wc -l  # Count blocks during crash
```

### Detect Mining Failures

```python
import pandas as pd

# Load mining data
mining = pd.read_csv('mining.csv', parse_dates=['timestamp_utc'])

# Find gaps in mining (should mine every ~5-10 seconds)
mining['time_diff'] = mining['timestamp_utc'].diff().dt.total_seconds()

# Alert on gaps > 30 seconds (indicates mining failure)
failures = mining[mining['time_diff'] > 30]
if len(failures) > 0:
    print(f"⚠️  {len(failures)} mining delays detected")
    print(failures[['timestamp_utc', 'miner', 'time_diff']])
```

### Mining Distribution Analysis

```python
import pandas as pd
import matplotlib.pyplot as plt

mining = pd.read_csv('mining.csv')

# Expected: 3 miners with roughly equal distribution (33% each)
distribution = mining['miner'].value_counts(normalize=True) * 100

print("Mining Distribution:")
print(distribution)

# Plot
distribution.plot(kind='bar', title='Mining Distribution Across Nodes')
plt.ylabel('Percentage of Blocks')
plt.tight_layout()
plt.savefig('mining_distribution.png')
```

---

## Data Retention

**Location**: `results/YYYYMMDDTHHMMSSZ/`

Each experiment run creates a timestamped directory with all collected data.

**Size**: Typical run ~50-200 MB depending on:
- Number of nodes (more logs)
- Duration (more transactions/blocks)
- Transaction rate

**Cleanup**: Use `cleanup_docker.sh` to remove Docker volumes but keep results.

---

## New Features in This Version

1. ✨ **Distributed Mining Tracking** (`mining.csv`)
   - Tracks which miner produced each block
   - Essential for analyzing mining resilience during crashes

2. ✨ **Node Health Collection** (`node_health.csv`)
   - Captures final state of all nodes
   - Identifies which nodes crashed and didn't recover

3. ✨ **Mining Summary** (`mining_summary.json`)
   - Quick overview of mining statistics
   - Percentage distribution across miners

4. ✨ **Improved txlog Format**
   - Removed empty miner column from transaction log
   - Separate mining.csv for clarity

---

## Troubleshooting

### Missing mining.csv
**Cause**: Experiment ended before any blocks were mined  
**Solution**: Check node startup in logs, increase observe_s

### Empty node_health.csv
**Cause**: Nodes stopped before data collection  
**Solution**: Check Docker status, review cleanup timing

### No confirmations.csv
**Cause**: No transactions or blockchain query failed  
**Solution**: Check txgen logs, verify node01 is reachable

---

**Last Updated**: 2024-10-30  
**Version**: 2.0 (Distributed Mining Update)


