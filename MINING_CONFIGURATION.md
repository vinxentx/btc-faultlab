# Deterministisches Mining-Setup

> Stand: November 2025 – gültig für das Multi-Wallet-Sharding mit Block-Scheduler.

## Überblick

Die Blockproduktion im FaultLab erfolgt mittlerweile vollständig **deterministisch**:

- **Funding-Wallet** (`wallet_funding`) sorgt für reife Coinbase-Auszahlungen.
- **Shard-Wallets** (`wallet_shard_*`) stellen dedizierte UTXO-Pools für jeden Tx-Generator bereit.
- **Block-Scheduler** (`block_scheduler`) erzeugt Blöcke im festen Intervall und rotiert Miner anhand eines Seeds.

Damit entfallen spontane „Emergency“-Mines, der Wallet-RPC-Druck sinkt, und Runs lassen sich über dutzende Replikationen hinweg exakt vergleichen.

## Komponenten

### 1. Funding & Verteilung (`files/funding_setup.py`)

- wartet auf RPC-Erreichbarkeit aller Wallet-Container,
- erzeugt 201 + 100 Blöcke (Coinbase-Reife),
- teilt `wallet_descriptor_pool_size` Outputs à `wallet_descriptor_amount_btc` auf jede Shard-Wallet auf,
- schreibt `state/funding_snapshot.json`,
- erstellt Scheduler-Config `state/mining_targets.json`.

### 2. Block-Scheduler (`files/block_scheduler.py`)

- liest `mining_targets.json` (Miner-Liste, Seed, Intervall, Mining-Adresse),
- wartet geduldig auf die Config (`--grace-period`, Standard 600 s),
- ruft im festen Abstand (`block_scheduler_interval_s`) `generatetoaddress` auf dem nächsten Miner auf,
- protokolliert sowohl menschenlesbare Zeilen als auch strukturierte `BLOCK_EVENT`-Einträge.

### 3. Tx-Generator-Shards (`files/txgen.py`)

- je Shard ein Container + eigenes Log-Volume,
- validieren beim Start das verfügbare UTXO-Budget (`--utxo-target`),
- erzeugen Transaktionen ohne Mining-Fallbacks,
- greifen auf adressbasierte Pools statt auf `getnewaddress` pro TX zu.

## Miner-Auswahl

- **Standardanteil**: `mining_percentage = 1.0` (alle Knoten als Miner)
- **Untergrenze**: Bei percentage < 1.0 mindestens 2 Miner
- **Strategie**: Alle Knoten werden als Miner verwendet (node01 … nodeN)
- **Hinweis**: Mit allen Nodes als Minern wird der Crash-Effekt auf die Blockzeit realistischer simuliert

| Netzgröße | Miner (100%) | Miner-IDs           |
|-----------|--------------|---------------------|
| 32        | 32           | node01 – node32     |
| 64        | 64           | node01 – node64     |
| 128       | 128          | node01 – node128    |

Die Auswahl erfolgt identisch in `funding_setup.py` und `block_scheduler.py`, sodass Funding und Laufzeit denselben Miner-Satz nutzen.

## Lifecycle

1. **02_deploy.yml** rendert Docker-Compose (Wallets, Txgen-Shards, Block-Scheduler).
2. **02_prepare_wallets.yml** ruft `files/funding_setup.py` auf (Ports: Funding `127.0.0.1:21043`, Shards `127.0.0.1:21053` …).
3. **block_scheduler** startet, wartet auf `mining_targets.json`, beginnt anschließend mit der Blockrotation.
4. **03_run_experiment.yml** validiert `funding_snapshot.json` (Fail-fast, falls UTXOs fehlen).
5. **04_collect.yml** sammelt Logs & erzeugt:
   - `txgen_<id>_txlog*.csv` → Aggregation zu `txlog.csv`, `txlog_performance.csv`, `txlog_errors.csv`
   - `block_scheduler.log` → `mining.csv` → `mining_summary.json`
   - Funding-/Scheduler-Snapshots im Run-Verzeichnis

## Automatisches Deployment (Auszug)

```yaml
block_scheduler:
  image: python:3.12-slim
  volumes:
    - "{{ workdir }}/files:/app:ro"
    - "{{ workdir }}/state:/state:ro"
  command: >
    python -u /app/block_scheduler.py
    --interval {{ block_scheduler_interval_s }}
    --node-count {{ node_count }}
    --mining-percentage {{ mining_percentage }}
    --rpc-user {{ rpc_user }}
    --rpc-pass {{ rpc_pass }}
    --seed {{ block_scheduler_seed }}
    --config /state/mining_targets.json
    --grace-period 600
```

Tx-Generator-Shards erhalten ihr individuelles Wallet via `--wallet`, `--rpc` und schreiben in `txlogs_<suffix>` Volumes.

## Artefakte

- `block_scheduler.log` – Textlog + `BLOCK_EVENT`-Zeilen
- `mining.csv` – aus `BLOCK_EVENT` aggregiert (Spalten: `timestamp_utc`, `block_number`, `miner`, `block_hash`)
- `mining_summary.json` – Histogramm pro Miner
- `funding_snapshot.json` – bestätigte UTXOs pro Shard
- `txgen_*_txlog*.csv` – Rohdaten pro Shard (werden zu Gesamtsichten zusammengeführt)

## Manuale Checks

```bash
# Funding-Snapshot prüfen
cat state/funding_snapshot.json | jq '.shards[] | {id, confirmed}'

# Scheduler-Konfig ansehen
cat state/mining_targets.json | jq '{interval_s, seed, miners: .miner_hosts}'

# BLOCK_EVENTs inspizieren
grep "^BLOCK_EVENT" results/<run>/block_scheduler.log
```

## Troubleshooting

- **Scheduler startet mehrfach neu**  
  → Prüfen, ob `mining_targets.json` vom Funding-Step erzeugt wurde; `02_prepare_wallets.yml` ggf. erneut ausführen.

- **Txgen meldet `Wallet … hat nur … bestätigte UTXOs`**  
  → Funding-Skript lief nicht durch; Container vorher stoppen (`docker compose down`) und Vorbereitung erneut ausführen.

- **Unebene Blockverteilung**  
  → `mining_summary.json` analysieren; bei Netzfehlern Rotations-Seed oder Miner-Satz prüfen.

## Referenzen

- Wallet Funding: `files/funding_setup.py`
- Scheduler: `files/block_scheduler.py`
- Tx-Generator: `files/txgen.py`
- Playbooks: `02_prepare_wallets.yml`, `03_run_experiment.yml`, `04_collect.yml`
- Docker Template: `roles/bitcoin/templates/docker-compose.yml.j2`
# Distributed Mining Configuration

## Overview

This project implements **distributed mining** across all network nodes (100%) to ensure resilience during fault injection experiments and enable realistic simulation of crash effects on block time. This approach eliminates single points of failure in block production.

## Configuration

### Mining Node Selection

- **Mining Percentage**: 100% of total nodes (all nodes are miners, configurable via `--mining-percentage`)
- **Minimum Miners**: When percentage < 1.0, minimum 2 nodes (ensures basic resilience)
- **Selection Strategy**: All nodes are used as miners (node01, node02, ..., nodeN)
- **Note**: Using all nodes as miners enables realistic simulation of crash effects on block time

### Example Configurations

| Total Nodes | Mining Nodes (100%) | Node IDs |
|-------------|---------------------|----------|
| 4           | 4                   | node01-node04 |
| 32          | 32                  | node01-node32 |
| 64          | 64                  | node01-node64 |
| 128         | 128                 | node01-node128 |

## How It Works

### 1. Initial Block Generation

During startup, the initial 201 blocks for wallet funding are distributed across all mining nodes:

```
Mining Configuration:
  Total nodes: 128
  Mining percentage: 100.0%
  Active miners: 128
  Mining nodes: node01:18443, node02:18443, ..., node128:18443
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
- ✅ All nodes as miners (128 miners) with 50% crash rate → 64 miners still available
- ✅ Automatic failover to healthy miners
- ✅ Continuous block production during crashes
- ✅ Realistic block time increase proportional to hash power loss
- ✅ Valid measurement of network resilience with accurate crash effects

## Scientific Justification

### Why 100% (All Nodes as Miners)?

1. **Realistic Crash Effects**: 
   - When miners crash in real Bitcoin networks, the effective hash power decreases
   - Block time increases proportionally: `new_block_time = base_interval × (total_miners / active_miners)`
   - Using all nodes as miners allows accurate simulation of this effect

2. **Fault Tolerance Testing**:
   - With 128 nodes and 50% crash: 64 nodes crash
   - 128 miners → 64 miners affected
   - Remaining 64 miners keep network operational, but block time doubles
   - Tests critical resilience boundaries with realistic hash power reduction

3. **Experimental Accuracy**:
   - Direct correlation between crashed nodes and block time increase
   - Enables precise measurement of network recovery under hash power loss
   - Mathematically correct simulation of real-world mining pool failures

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
  --mining-percentage 1.0

# Output:
# 📊 Mining Configuration:
#    Total nodes: 128
#    Mining percentage: 100.0%
#    Active miners: 128
#    Mining nodes: node01:18443, ..., node128:18443
```

### Custom Mining Percentage

To adjust the mining percentage (e.g., for sensitivity analysis):

```yaml
# In docker-compose template
--mining-percentage 1.0   # 100% miners (all nodes, recommended for crash effect simulation)
--mining-percentage 0.10  # 10% miners (alternative configuration)
--mining-percentage 0.03  # 3% miners (alternative configuration)
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


