# Bitcoin Faultlab - Vollständiger Experimentaufbau

> **Dokumentation für Thesis Supervisor**  
> Stand: Januar 2025  
> Version: 2.0 (Deterministisches Mining, Multi-Wallet-Sharding)

---

## 📋 Inhaltsverzeichnis

1. [Übersicht und Ziele](#übersicht-und-ziele)
2. [System-Architektur](#system-architektur)
3. [Komponenten im Detail](#komponenten-im-detail)
4. [Experiment-Phasen](#experiment-phasen)
5. [Konfiguration und Parameter](#konfiguration-und-parameter)
6. [Experiment-Ausführung](#experiment-ausführung)
7. [Daten-Sammlung](#daten-sammlung)
8. [Wissenschaftliche Metriken](#wissenschaftliche-metriken)
9. [Tier-Experimente](#tier-experimente)
10. [Reproduzierbarkeit](#reproduzierbarkeit)

---

## 🎯 Übersicht und Ziele

### Forschungsziel

Das **Bitcoin Faultlab** ist ein experimentelles Framework zur systematischen Analyse der **Resilienz und Fehlertoleranz** von Bitcoin-Netzwerken unter kontrollierten Fehlerbedingungen. Das System ermöglicht:

- **Kontrollierte Fehlerinjektion**: Crash-Fehler (Byzantinische Fehler) und Omissions-Fehler (Netzwerkdegradation)
- **Systematische Parameter-Variation**: Crash-Raten, Netzwerkbedingungen, Block-Intervalle, Recovery-Modi
- **Statistische Signifikanz**: Mehrfache Replikationen pro Experiment-Konfiguration
- **Deterministische Reproduzierbarkeit**: Seeded Randomisierung für exakte Wiederholbarkeit

### Experiment-Design

Das Framework implementiert ein **Tier-basiertes Experiment-Design**:

- **Baseline**: Perfekte Bedingungen ohne Fehler (Referenz)
- **Tier A**: Crash-Impact-Analyse (10%, 25%, 50% Crash-Raten)
- **Tier B**: Stress-Umgebungen (Netzwerkdegradation, hohe Last)
- **Tier C**: Block-Intervall-Sensitivität (6s vs. 12s)

**Total**: 47 Experiment-Konfigurationen × 3 Replikationen = **141 Runs** (~67 Stunden)

---

## 🏗️ System-Architektur

### Gesamtübersicht

```
┌─────────────────────────────────────────────────────────────────┐
│              BITCOIN REGTEST NETZWERK (128 Nodes)               │
│                                                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Mining Nodes │  │ Regular Nodes│  │ Network Layer │         │
│  │  (8% = 10)  │  │  (92% = 118) │  │  (P2P)       │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
┌───────▼────────┐  ┌────────▼────────┐  ┌────────▼────────┐
│ Block Scheduler│  │  Tx-Generators  │  │  Wallet Shards  │
│  (Determinist.)│  │   (4 Shards)     │  │   (4 Shards)    │
└────────────────┘  └─────────────────┘  └─────────────────┘
```

### Komponenten-Hierarchie

1. **Bitcoin Core Nodes** (128 Container)
   - Mining Nodes: 8% (10 Nodes) - Blockproduktion
   - Regular Nodes: 92% (118 Nodes) - Validierung & Relay

2. **Wallet-Infrastruktur**
   - Funding Wallet: Initiale Coinbase-Auszahlungen
   - Shard Wallets (4×): Dedizierte UTXO-Pools pro Tx-Generator

3. **Orchestrierung**
   - Block Scheduler: Deterministische Blockproduktion
   - Tx-Generators (4×): Parallele Transaktionsgenerierung
   - Ansible Playbooks: Experiment-Orchestrierung

4. **Fehlerinjektion**
   - Crash Injection: Container-Stopp/Start 
   - Network Emulation: Netem (Omissions-Fehler)

---

## 🧩 Komponenten im Detail

### 1. Bitcoin Core Nodes

**Konfiguration:**
- **Image**: `bitcoin/bitcoin:27.0`
- **Network**: Regtest (Testnetz)
- **RPC Ports**: 20443-20570 (increment 4)
- **P2P Ports**: 18444-18571 (increment 1)

**Mining Nodes (8%):**
- **Auswahl**: Erste N Nodes (node01-node10 bei 128 Nodes)
- **Funktion**: Blockproduktion via `generatetoaddress`
- **Rotation**: Deterministisch basierend auf Seed

**Regular Nodes (92%):**
- **Funktion**: Blockchain-Validierung, TX-Relay, Konsens
- **Verbindungen**: Automatisches P2P-Peering

### 2. Wallet-Infrastruktur

#### Funding Wallet (`wallet_funding`)
- **Zweck**: Initiale Coinbase-Auszahlungen
- **Prozess**:
  1. Generiert 201 Blöcke (distributed mining)
  2. Wartet 100 Blöcke (Coin Maturity)
  3. Verteilt UTXOs auf Shard-Wallets
  4. Erstellt `funding_snapshot.json`

#### Shard Wallets (4×)
- **Namen**: `wallet_shard_a`, `wallet_shard_b`, `wallet_shard_c`, `wallet_shard_d`
- **UTXO-Pool**: `wallet_descriptor_pool_size` (Standard: 2400) bestätigte UTXOs
- **Betrag pro UTXO**: `wallet_descriptor_amount_btc` (Standard: 0.0002 BTC)
- **Zweck**: Dedizierte UTXO-Pools für jeden Tx-Generator

### 3. Block Scheduler (`block_scheduler.py`)

**Funktion**: Deterministische Blockproduktion

**Eigenschaften:**
- **Intervall**: Konfigurierbar (Standard: 6s)
- **Miner-Rotation**: Deterministisch basierend auf Seed
- **Health Checks**: Automatisches Failover bei Miner-Ausfall
- **Logging**: Strukturierte `BLOCK_EVENT`-Einträge

**Ablauf:**
```
1. Wartet auf mining_targets.json (von funding_setup.py)
2. Liest Konfiguration (Miner-Liste, Seed, Intervall, Adresse)
3. Rotiert Miner deterministisch (seeded shuffle)
4. Ruft generatetoaddress(1, addr) im festen Intervall auf
5. Protokolliert jeden Block mit Timestamp und Miner-ID
```

### 4. Transaktions-Generatoren (`txgen.py`)

**Architektur**: Multi-Shard-Parallelisierung

**4 Shards** (parallel):
- **Shard A**: `wallet_shard_a` → `txgen_a_txlog.csv`
- **Shard B**: `wallet_shard_b` → `txgen_b_txlog.csv`
- **Shard C**: `wallet_shard_c` → `txgen_c_txlog.csv`
- **Shard D**: `wallet_shard_d` → `txgen_d_txlog.csv`

**Funktionsweise:**
1. **Adress-Pool**: Vorbereitete Adressen (`txgen_address_pool_size`)
2. **UTXO-Validierung**: Prüft verfügbare UTXOs beim Start
3. **Transaktionsgenerierung**: 
   - Rate: `tx_rate` (Standard: 10 tx/s pro Shard)
   - Betrag: `txgen_send_amount_btc` (Standard: 0.0001 BTC)
   - Adress-Wiederverwendung: `txgen_address_reuse_window` (Standard: 100)
4. **Kein Mining**: Generatoren erzeugen nur TXs, kein Block-Mining

**Vorteile:**
- **Parallele Last**: 4× höhere Transaktionsrate möglich
- **Isolierte UTXO-Pools**: Keine Interferenz zwischen Shards
- **Skalierbarkeit**: Einfach mehr Shards hinzufügbar

### 5. Experiment-Orchestrierung (Ansible)

**Playbooks:**
- `01_bootstrap.yml`: Einmalige System-Vorbereitung
- `02_deploy.yml`: Docker-Compose Deployment
- `02_prepare_wallets.yml`: Wallet-Funding & Setup
- `03_run_experiment.yml`: Experiment-Ausführung
- `04_collect.yml`: Daten-Sammlung

**Rollen:**
- `bitcoin`: Bitcoin Core Container-Setup
- `experiment`: Crash-Injection & Recovery
- `netem`: Network Emulation (Latenz, Loss, Bandwidth)
- `telemetry`: Monitoring & Logging

---

## ⏱️ Experiment-Phasen

### Phase 1: Warmup (Standard: 60s)

**Zweck**: Netzwerk stabilisieren, Baseline-Performance etablieren

**Aktivitäten:**
- Alle Nodes laufen stabil
- Transaktionsgenerierung läuft
- Blockproduktion läuft (6s Intervall)
- Mempool-Monitoring startet

**Event**: `start_warmup` → `events.log`

### Phase 2: Network Impairments (Netem)

**Zweck**: Netzwerkdegradation anwenden (Omissions-Fehler)

**Parameter:**
- `loss_pct`: Packet Loss (Standard: 0-10%)
- `latency_ms`: Latenz (Standard: 50-200ms)
- `bandwidth_mbit`: Bandbreitenlimit (Standard: 10-100 Mbit/s)
- `jitter_ms`: Latenz-Variabilität (Standard: 5-30ms)
- `network_fault_fraction`: Anteil betroffener Nodes (Standard: 1.0 = alle)

**Implementierung:**
- Linux Traffic Control (`tc`) via Docker-Container
- Netem QDisc auf Node-Containern
- Deterministische Node-Auswahl (seeded)

**Event**: `after_netem` → `events.log`

### Phase 3: Crash Injection

**Zweck**: Byzantinische Fehler simulieren (Node-Crashes)

**Parameter:**
- `crash_fraction`: Anteil crashender Nodes (0.1, 0.25, 0.5)
- `crash_duration_s`: Downtime (20s, 60s)
- `crash_mode`: `burst` (simultan) oder `staggered` (sequenziell)
- `recovery_mode`: `full_resync` (mit -reindex) oder `fast_resync`

**Crash-Modi:**

**Burst Mode:**
```
Alle Nodes crashen gleichzeitig:
t=0s: crash_start
t=0.5s: Alle Container gestoppt → crash_complete
t=0.5s-20s: Downtime (crash_duration_s)
t=20s: recovery_start (Container starten)
t=20s-120s: Synchronisation
t=120s: recovery_complete
```

**Staggered Mode:**
```
Nodes crashen sequenziell:
t=0s: crash_start
t=0s: node01 crash
t=1.8s: node02 crash (bei 32 Nodes, 20s/11 = 1.8s)
t=3.6s: node03 crash
...
t=20s: Letzter Node crash → crash_complete
t=20s-40s: Minimale Downtime für letzten Node
t=40s: recovery_start
t=40s-140s: Synchronisation
t=140s: recovery_complete
```

**Recovery-Modi:**

**Full Resync:**
- Container-Restart
- Bitcoin Core mit `-reindex` Flag
- Vollständige Blockchain-Neuindizierung
- Langsamer, aber vollständige Validierung

**Fast Resync:**
- Container-Start (ohne -reindex)
- Schnelle Synchronisation von Peers
- Schneller, aber mögliche Validierungslücken

**Events:**
- `crash_start` → `events.log`
- `node_crash node=nodeXX` (nur Staggered)
- `crash_complete` → `events.log`
- `recovery_start` → `events.log`
- `recovery_complete` → `events.log`

### Phase 4: Observation (Standard: 600s)

**Zweck**: Recovery-Performance messen

**Aktivitäten:**
- Crashed Nodes synchronisieren
- Transaktionsgenerierung läuft weiter
- Blockproduktion läuft weiter
- Performance-Metriken werden gesammelt

**Event**: `end_observe` → `events.log`

### Phase 5: Cooldown (Standard: 60s)

**Zweck**: System stabilisieren nach Recovery

**Aktivitäten:**
- Netzwerk-Impairments werden entfernt
- Alle Nodes laufen stabil
- Finale Metriken werden gesammelt

---

## ⚙️ Konfiguration und Parameter

### Basis-Konfiguration (`group_vars/all.yml`)

```yaml
# Netzwerk-Topologie
node_count: 128                    # Anzahl Bitcoin Nodes
mining_percentage: 0.08            # 8% Mining Nodes

# Transaktionslast
tx_rate: 10                        # TX/s pro Shard (4 Shards = 40 TX/s total)
txgen_shards: 4                    # Anzahl paralleler Tx-Generatoren

# Wallet-Konfiguration
wallet_descriptor_pool_size: 2400  # UTXOs pro Shard
wallet_descriptor_amount_btc: 0.0002  # Betrag pro UTXO

# Blockproduktion
block_scheduler_interval_s: 6      # Block-Intervall (Sekunden)
block_scheduler_seed: 1337         # Deterministischer Seed

# Experiment-Phasen
warmup_s: 60                       # Warmup-Dauer
observe_s: 600                     # Observation-Dauer
cooldown_s: 60                     # Cooldown-Dauer
```

### Fehlerinjektion-Parameter

```yaml
# Crash-Fehler (Byzantinische Fehler)
crash_fraction: 0.25               # 25% der Nodes crashen
crash_duration_s: 60               # 60 Sekunden Downtime
crash_mode: "burst"                # "burst" oder "staggered"
recovery_mode: "full_resync"       # "full_resync" oder "fast_resync"
reindex_on_restart: true           # Blockchain-Reindex bei Recovery

# Netzwerk-Fehler (Omissions-Fehler)
loss_pct: 0                        # Packet Loss (%)
latency_ms: 50                     # Latenz (ms)
bandwidth_mbit: 100                # Bandbreite (Mbit/s)
jitter_ms: 5                       # Latenz-Variabilität (ms)
network_fault_fraction: 1.0        # Anteil betroffener Nodes (1.0 = alle)
```

### Tier-Experiment-Konfiguration (`tier_experiments.json`)

**Struktur:**
- **Baseline**: 1 Experiment (keine Fehler)
- **Tier A**: 24 Experimente (Crash-Impact)
- **Tier B**: 18 Experimente (Stress-Umgebungen)
- **Tier C**: 4 Experimente (Block-Intervall)

**Beispiel (Tier A):**
```json
{
  "tier_a_001": {
    "name": "Tier A: 10% Crash, Simultaneous, 20s Down, Full Resync",
    "config": {
      "crash_fraction": 0.1,
      "crash_duration_s": 20,
      "crash_mode": "burst",
      "recovery_mode": "full_resync"
    }
  }
}
```

---

## 🚀 Experiment-Ausführung

### Automatische Ausführung

**Tier-Experimente (empfohlen):**
```bash
# Alle Experimente (141 Runs, ~67 Stunden)
python3 run_tier_experiments.py --extended --runs 3

# Nur Baseline
python3 run_tier_experiments.py --baseline --runs 3

# Nur Tier A (24 Experimente)
python3 run_tier_experiments.py --tier A --runs 3

# Einzelnes Experiment
python3 run_tier_experiments.py --experiment tier_a_001 --runs 5
```

**Manuelle Ausführung:**
```bash
# Einzelnes Experiment
python3 run_experiments.py --single

# Mit Custom Config
python3 run_experiments.py --config custom_config.json
```

### Workflow

**1. Bootstrap (einmalig):**
```bash
ansible-playbook -i inventories/lrz_local.ini playbooks/01_bootstrap.yml
```

**2. Experiment-Ausführung (automatisch via `run_tier_experiments.py`):**
```
Für jedes Experiment:
  1. 02_deploy.yml          → Docker-Compose Deployment
  2. 02_prepare_wallets.yml → Wallet-Funding & Setup
  3. 03_run_experiment.yml  → Experiment-Ausführung
  4. 04_collect.yml         → Daten-Sammlung
  5. Cleanup                → Container & Volumes entfernen
```

**3. Daten-Sammlung:**
- Automatisch nach jedem Run
- Ergebnisse in `results/<run_id>/`

---

## 📊 Daten-Sammlung

### Gesammelte Daten

#### Transaktionsdaten
- **`txlog.csv`**: Aggregierte Transaktionen (alle Shards)
- **`txgen_<id>_txlog.csv`**: Rohdaten pro Shard
- **`txlog_performance.csv`**: Performance-Metriken (RPC-Latency, UTXO-Counts)
- **`txlog_errors.csv`**: Fehlerhafte Transaktionen

#### Block-Daten
- **`block_scheduler.log`**: Blockproduktion-Log (Text)
- **`mining.csv`**: Strukturierte Block-Events (CSV)
- **`mining_summary.json`**: Mining-Statistiken (JSON)

#### Blockchain-Daten
- **`confirmations.csv`**: Transaktions-Bestätigungen
- **`chaintips.json`**: Chain-Tip-Status (Fork-Detection)
- **`mempool.json`**: Mempool-Statistiken

#### Node-Daten
- **`node_health.csv`**: Finaler Node-Status (running/stopped)
- **`nodeXX.log`**: Bitcoin Core Debug-Logs (alle Nodes)

#### Experiment-Metadaten
- **`metadata.yml`**: Experiment-Konfiguration (Snapshot)
- **`events.log`**: Experiment-Timeline (Phasen, Crashes, Recovery)
- **`funding_snapshot.json`**: Wallet-Funding-Status

#### Analysedaten
- **`metrics.json`**: Berechnete Performance-Metriken
- **`plots/`**: Visualisierungen (PNG)

### Datenfluss

```
Experiment Run
    │
    ├─> Tx-Generators → txgen_*_txlog*.csv
    ├─> Block Scheduler → block_scheduler.log
    ├─> Nodes → nodeXX.log
    │
    └─> 04_collect.yml:
        ├─> Aggregiere txlog*.csv → txlog.csv
        ├─> Parse block_scheduler.log → mining.csv
        ├─> Query Blockchain → confirmations.csv
        ├─> Query Nodes → node_health.csv
        ├─> Run metrics.py → metrics.json
        └─> Generate plots → plots/
```

---

## 📈 Wissenschaftliche Metriken

### Primäre Metriken

#### 1. Confirmation Latency
- **Definition**: Zeit von TX-Submission bis 6 Confirmations
- **Berechnung**: `confirm_time - submit_time`
- **Statistiken**: Mean, Median, P95, P99

#### 2. Throughput
- **Definition**: Bestätigte Transaktionen pro Sekunde
- **Berechnung**: `confirmed_txs / observation_duration`
- **Zeitfenster**: Rolling 30s Window

#### 3. Recovery Time
- **Definition**: Zeit von `recovery_start` bis `recovery_complete`
- **Berechnung**: Aus `events.log` extrahiert
- **Komponenten**:
  - Container-Start-Zeit
  - Blockchain-Synchronisation
  - Peer-Verbindungen

#### 4. Availability
- **Definition**: Anteil der Zeit, in der Netzwerk funktionsfähig ist
- **Berechnung**: `(total_time - downtime) / total_time`
- **Downtime**: Von `crash_start` bis `recovery_complete`

#### 5. Fork Rate
- **Definition**: Anzahl Blockchain-Forks während Experiment
- **Berechnung**: Aus `chaintips.json` (branchlen > 0)

### Sekundäre Metriken

#### 6. Mempool Size
- **Definition**: Anzahl unbestätigter Transaktionen
- **Quelle**: `mempool.json` (timeseries)

#### 7. Block Propagation Time
- **Definition**: Zeit von Block-Mining bis Propagation zu allen Nodes
- **Berechnung**: Aus Node-Logs (Block-Empfang-Timestamps)

#### 8. Node Health Rate
- **Definition**: Anteil funktionsfähiger Nodes
- **Berechnung**: `running_nodes / total_nodes`

### Metriken-Berechnung

**Tool**: `analysis/metrics.py`

```bash
python3 analysis/metrics.py --run-dir results/<run_id>
```

**Output**: `metrics.json` mit allen berechneten Metriken

---

## 🎯 Tier-Experimente

### Experiment-Design

**Tier A: Crash-Impact-Analyse (24 Experimente)**

**Parameter-Variation:**
- Crash-Rate: 10%, 25%, 50%
- Crash-Modus: Burst, Staggered
- Downtime: 20s, 60s
- Recovery: Full Resync, Fast Resync

**Hypothesen:**
- Höhere Crash-Raten führen zu erhöhter Latency
- Simultane Crashes verursachen schärfere Degradation als sequenzielle
- Längere Downtime signifikant beeinflusst Recovery-Metriken
- Fast Resync reduziert Recovery-Zeit im Vergleich zu Full Resync

**Tier B: Stress-Umgebungen (18 Experimente)**

**Parameter-Variation:**
- Netzwerkbedingungen: Loss, Latency, Bandwidth, Jitter
- Netzwerk-Fault-Fraction: 0%, 25%, 50%, 100%
- Transaktionsrate: 10 tx/s, 30 tx/s
- Block-Größe: 1MB, 2MB

**Hypothesen:**
- Schlechte Netzwerkbedingungen verstärken Crash-Impact
- Hohe Transaktionslast erhöht Mempool-Congestion während Recovery
- Größere Blöcke (2MB) verschärfen Netzwerk-Stress
- Omissions-Fehler allein (ohne Crashes) verursachen messbare Degradation

**Tier C: Block-Intervall-Sensitivität (4 Experimente)**

**Parameter-Variation:**
- Block-Intervall: 6s, 12s
- Recovery: Full Resync, Fast Resync

**Hypothesen:**
- Schnellere Block-Intervalle (6s) erhöhen Fork-Wahrscheinlichkeit während Recovery
- Langsamere Block-Intervalle (12s) bieten stabilere Recovery, aber längere Bestätigungszeiten
- Fast Resync-Vorteile sind ausgeprägter bei schnelleren Block-Intervallen

### Statistische Signifikanz

**Replikationen**: 3 Runs pro Experiment-Konfiguration

**Begründung:**
- Reduziert Zufallsvariabilität
- Ermöglicht statistische Tests (t-Test, ANOVA)
- Erhöht Vertrauen in Ergebnisse

**Total**: 47 Experimente × 3 Replikationen = **141 Runs**

---

## 🔬 Reproduzierbarkeit

### Deterministische Komponenten

#### 1. Seeded Randomisierung
- **Crash-Node-Auswahl**: `shuffle(seed=seed)` in Ansible
- **Miner-Rotation**: `random.Random(seed)` in `block_scheduler.py`
- **Network-Fault-Node-Auswahl**: `shuffle(seed=seed)` in Ansible

#### 2. Deterministisches Mining
- **Block-Intervall**: Fest (6s oder 12s)
- **Miner-Rotation**: Deterministisch basierend auf Seed
- **Mining-Adresse**: Konstant (von Funding-Setup)

#### 3. Konfigurations-Snapshots
- **`metadata.yml`**: Vollständige Experiment-Konfiguration
- **`funding_snapshot.json`**: Wallet-Funding-Status
- **`mining_targets.json`**: Block-Scheduler-Konfiguration

### Reproduktions-Workflow

**1. Konfiguration wiederherstellen:**
```bash
# Aus metadata.yml
cat results/<run_id>/metadata.yml
```

**2. Seed verwenden:**
```yaml
seed: 42  # Aus metadata.yml
```

**3. Experiment wiederholen:**
```bash
python3 run_tier_experiments.py --experiment tier_a_001 --runs 1
```

**Erwartung**: Identische Ergebnisse bei identischer Konfiguration

---

## 📝 Zusammenfassung

### Kern-Features

1. **Deterministisches Mining**: Block-Scheduler mit seeded Rotation
2. **Multi-Wallet-Sharding**: 4 parallele Tx-Generatoren mit isolierten UTXO-Pools
3. **Systematische Fehlerinjektion**: Crash-Fehler (Byzantinisch) + Omissions-Fehler (Netzwerk)
4. **Tier-basiertes Design**: 47 Experiment-Konfigurationen
5. **Statistische Signifikanz**: 3 Replikationen pro Experiment
6. **Vollständige Daten-Sammlung**: Transaktionen, Blöcke, Nodes, Metriken
7. **Reproduzierbarkeit**: Seeded Randomisierung + Konfigurations-Snapshots

### Wissenschaftlicher Beitrag

- **Systematische Analyse** der Bitcoin-Netzwerk-Resilienz
- **Kontrollierte Fehlerinjektion** unter reproduzierbaren Bedingungen
- **Quantitative Metriken** für Recovery-Performance
- **Vergleichende Analyse** verschiedener Recovery-Strategien
- **Skalierbare Architektur** für zukünftige Experimente

### Technische Innovationen

- **Deterministisches Mining**: Eliminiert Zufälligkeit in Blockproduktion
- **Multi-Shard-Architektur**: Parallele Last ohne UTXO-Interferenz
- **Präzise Event-Logging**: Wissenschaftlich validierte Timeline-Metriken
- **Automatisierte Orchestrierung**: Ansible-basierte Experiment-Ausführung

---

**Erstellt**: Januar 2025  
**Autor**: Bitcoin Faultlab Team  
**Version**: 2.0

