# Detaillierte Architektur-Dokumentation

## Übersicht: Wie die Komponenten zusammenarbeiten

Die neue Architektur besteht aus 5 Hauptkomponenten, die in einer definierten Reihenfolge initialisiert werden und dann parallel arbeiten:

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. BOOTSTRAP: Docker Compose Stack wird hochgefahren           │
│    - Bitcoin Nodes (node01, node02, ..., node64)               │
│    - Funding Wallet Container (wallet_funding)                  │
│    - Shard Wallet Containers (wallet_shard_a, b, c, d)         │
│    - Block Scheduler Container (block_scheduler)                │
│    - TXGen Shard Containers werden NICHT gestartet             │
│      (verwenden Docker Compose Profile "txgen")                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. FUNDING SETUP: Initiale Coin-Verteilung                     │
│    funding_setup.py                                             │
│    - Erstellt Funding Wallet                                    │
│    - Mine 201+ Blöcke (Coinbase-Reife)                          │
│    - Erstellt Shard Wallets (txshard_a, b, c, d)               │
│    - Verteilt UTXOs an Shard Wallets                            │
│    - Erstellt mining_targets.json                               │
│    - Erstellt funding_snapshot.json                            │
│                                                                 │
│    Nach erfolgreichem Abschluss:                                │
│    - TXGen Shard Containers werden gestartet                    │
│      (docker compose --profile txgen up)                        │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. RUNTIME: Parallele Ausführung                                │
│                                                                 │
│  ┌──────────────────┐      ┌──────────────────┐               │
│  │ Block Scheduler  │      │ TXGen Shards     │               │
│  │                  │      │                  │               │
│  │ - Liest Config   │      │ - Liest Wallet   │               │
│  │ - Mine Blöcke    │      │ - Generiert TX   │               │
│  │   alle 6s        │      │   mit Rate       │               │
│  │ - Rotiert Miner  │      │ - Nutzt Address  │               │
│  │ - Loggt Events   │      │   Pool           │               │
│  └──────────────────┘      └──────────────────┘               │
│           │                            │                        │
│           └──────────┬─────────────────┘                        │
│                      ↓                                          │
│            ┌──────────────────┐                                │
│            │ Bitcoin Nodes     │                                │
│            │ - Validieren TX   │                                │
│            │ - Broadcast TX    │                                │
│            │ - Mine Blöcke     │                                │
│            └──────────────────┘                                │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. DATA COLLECTION: Metriken sammeln                           │
│    - TXGen Logs (txlog_a.csv, txlog_b.csv, ...)                 │
│    - Block Scheduler Log (block_scheduler.log)                  │
│    - Mining CSV (mining.csv)                                    │
│    - Confirmations CSV (confirmations.csv mit Block-Hashes)     │
└─────────────────────────────────────────────────────────────────┘
```

---

## Komponente 1: Bitcoin Nodes

### Was es macht
- **Bitcoin Core (`bitcoind`) Instanzen** in Docker Containern
- Jeder Node ist ein vollständiger Bitcoin-Knoten mit:
  - Blockchain-Datenbank
  - Mempool (unbestätigte Transaktionen)
  - P2P-Netzwerk (verbindet mit anderen Nodes)
  - RPC-Interface (für Wallet- und Mining-Operationen)

### Mainnet-Pendant
✅ **Realistisch**: Entspricht echten Bitcoin Full Nodes
- Mainnet: Tausende von unabhängigen Full Nodes weltweit
- Regtest: Unsere 64 Nodes simulieren ein kleines Netzwerk

### Interaktionen
- **Empfängt**: Transaktionen von TXGen Shards (via RPC `sendtoaddress`)
- **Empfängt**: Mining-Befehle von Block Scheduler (via RPC `generatetoaddress`)
- **Sendet**: Bestätigte Transaktionen zurück (via RPC `getrawtransaction`)
- **Kommuniziert**: Mit anderen Nodes via P2P (Block- und TX-Broadcast)

### Konfiguration
- **RPC Ports**: `20443 + (node_id - 1) * 4` (z.B. node01 = 20443, node02 = 20447)
- **P2P Ports**: `18444 + (node_id - 1) * 4`
- **Mining**: Nur ein Teil der Nodes kann minen (`mining_percentage = 0.08` = 8%)

---

## Komponente 2: Funding Wallet

### Was es macht
- **Dedizierte Wallet** (`wallet_funding`) für initiale Coin-Verteilung
- Wird nur während `funding_setup.py` verwendet
- Erhält die ersten 201+ Blöcke (Coinbase-Transaktionen)
- Verteilt Coins an Shard Wallets via `sendmany` (Batch-Transfers)

### Mainnet-Pendant
⚠️ **Teilweise realistisch**: 
- **Realistisch**: Initial Coin Distribution existiert (Genesis Block, Mining Rewards)
- **Nicht realistisch**: Zentralisierte "Funding Wallet" existiert nicht im Mainnet
  - Im Mainnet: Coins werden durch Mining generiert und verteilt
  - In unserem Setup: Zentralisierte Verteilung für Experimente

### Interaktionen
- **Empfängt**: Coinbase-Transaktionen (durch Mining in `funding_setup.py`)
- **Sendet**: Batch-Transfers an Shard Wallets (via `sendmany` mit 50 Adressen pro Batch)
- **Wird nicht verwendet**: Nach Funding-Phase (nur für Setup)

### Datenfluss
```
funding_setup.py
  → Mine 201 Blöcke zu Funding Wallet
  → Generiere Adressen für Shard Wallets (800 pro Shard)
  → sendmany: 50 Adressen pro Batch
  → Mine 2 Blöcke nach jedem 4. Batch (um Mempool-Druck zu vermeiden)
  → Mine finale Blöcke zur Bestätigung (max(confirmation_blocks, total_batches + 20))
```

---

## Komponente 3: Shard Wallets

### Was es macht
- **4 separate Wallets** (`txshard_a`, `txshard_b`, `txshard_c`, `txshard_d`)
- Jede Wallet ist **descriptor-basiert** für effizientes UTXO-Management
- Jede Wallet erhält **800 bestätigte UTXOs** à **0.0002 BTC** vom Funding Wallet
- Jede Wallet hat einen eigenen **Bitcoin Core Container** (`wallet_shard_a`, etc.)

### Mainnet-Pendant
✅ **Realistisch**: Entspricht echten Bitcoin Wallets
- Mainnet: Jeder User/Service hat seine eigene Wallet
- Unsere Shards: Simulieren verschiedene Akteure, die Transaktionen generieren
- **Unterschied**: Im Mainnet sind Wallets nicht zentralisiert auf einem Server

### Interaktionen
- **Empfängt**: UTXOs vom Funding Wallet (während `funding_setup.py`)
- **Sendet**: Transaktionen via TXGen Shards (via RPC `sendtoaddress`)
- **Wird abgefragt**: Von TXGen Shards (UTXO-Status, Balance)

### Datenfluss
```
funding_setup.py
  → sendmany zu txshard_a: 800 Outputs à 0.0002 BTC
  → sendmany zu txshard_b: 800 Outputs à 0.0002 BTC
  → sendmany zu txshard_c: 800 Outputs à 0.0002 BTC
  → sendmany zu txshard_d: 800 Outputs à 0.0002 BTC
  → Warte auf Bestätigung (max 5 Minuten)
  → Validiere: Jede Shard Wallet hat ≥ 800 bestätigte UTXOs
```

---

## Komponente 4: TXGen Shards

### Was es macht
- **4 separate Python-Services** (`txgen_a`, `txgen_b`, `txgen_c`, `txgen_d`)
- Jeder Shard generiert **1/4 der Gesamt-Transaktionsrate**
  - Bei `tx_rate = 10 tx/s`: Jeder Shard generiert **2.5 tx/s**
- Jeder Shard ist verbunden mit einer eigenen Shard Wallet
- Verwendet **Address Pool** (2048 Adressen) statt `getnewaddress` pro TX
- **Kein Mining mehr** (wurde entfernt)

### Mainnet-Pendant
✅ **Realistisch**: Entspricht echten Transaktionsgeneratoren
- Mainnet: Viele unabhängige Services/User generieren Transaktionen
  - Exchanges (Coinbase, Binance)
  - Payment Processors (Stripe, PayPal)
  - Wallets (Electrum, Bitcoin Core)
  - DeFi-Protokolle
- Unsere Shards: Simulieren verschiedene Transaktionsquellen
- **Unterschied**: Im Mainnet sind Generatoren geografisch verteilt, nicht zentralisiert

### Interaktionen
- **Verbindet**: Mit eigener Shard Wallet (via RPC)
- **Sendet**: Transaktionen an Bitcoin Nodes (via RPC `sendtoaddress`)
- **Liest**: Wallet-Status (UTXO-Count, Balance)
- **Loggt**: Alle Transaktionen in `txlog_<suffix>.csv`

### Datenfluss
```
txgen_a (Shard A)
  → Verbinde mit wallet_shard_a:18443
  → Validiere: ≥ 800 bestätigte UTXOs vorhanden
  → Lade Address Pool (2048 Adressen)
  → Loop:
      → Wähle zufällige Zieladresse aus Pool
      → Sende 0.0001 BTC (via sendtoaddress)
      → Logge TX (txid, submit_ts_utc)
      → Warte 1 / (tx_rate / 4) Sekunden
```

### Address Pool Mechanismus
- **Pool-Größe**: 2048 Adressen pro Shard
- **Reuse Window**: Nur jede 100. TX ruft `getnewaddress` auf
- **Vorteil**: Reduziert RPC-Aufrufe, beschleunigt TX-Generierung

---

## Komponente 5: Block Scheduler

### Was es macht
- **Dedizierter Python-Service** (`block_scheduler`)
- **Deterministisches Mining** mit festem Intervall (6 Sekunden)
- **Rotiert Miner** basierend auf Seed (deterministische Reihenfolge)
- **Komplett getrennt** von Transaktionsgenerierung
- Loggt alle Block-Events in `block_scheduler.log`

### Mainnet-Pendant
❌ **Nicht realistisch**: 
- **Mainnet**: Mining ist **dezentral** und **wettbewerbsorientiert**
  - Tausende von Minern konkurrieren um Block-Rewards
  - Block-Intervalle sind **zufällig** (Exponentialverteilung, ~10 Minuten Durchschnitt)
  - Mining basiert auf **Proof-of-Work** (Hash-Rate)
- **Unser Setup**: Zentralisierter Scheduler mit festem Intervall
  - **Vorteil**: Reproduzierbare Experimente
  - **Nachteil**: Nicht realistisch für Mainnet-Verhalten

### Interaktionen
- **Liest**: `mining_targets.json` (Miner-Liste, Seed, Intervall)
- **Sendet**: Mining-Befehle an Bitcoin Nodes (via RPC `generatetoaddress`)
- **Rotiert**: Zwischen verfügbaren Minern (basierend auf Seed)
- **Loggt**: Jeden Block-Event (`BLOCK_EVENT,timestamp,block_number,miner,block_hash`)

### Datenfluss
```
block_scheduler
  → Warte auf mining_targets.json (max 10 Minuten)
  → Lade Config:
      - mining_address (Empfänger der Coinbase-Rewards)
      - miner_hosts (Liste der Mining-Nodes)
      - interval_s (6 Sekunden)
      - seed (1337 für Determinismus)
  → Loop:
      → Wähle nächsten Miner (rotierend, basierend auf Seed)
      → Prüfe: Ist Miner gesund? (RPC erreichbar?)
      → generatetoaddress(1, mining_address)
      → Logge: BLOCK_EVENT,timestamp,block_number,miner,block_hash
      → Warte interval_s Sekunden
```

### Miner-Rotation
- **Seed-basiert**: Deterministische Reihenfolge
- **Beispiel** (Seed=1337, 5 Miner):
  ```
  Block 1: node02
  Block 2: node01
  Block 3: node05
  Block 4: node03
  Block 5: node04
  Block 6: node02 (wiederholt)
  ```

---

## Datenfluss: Kompletter Zyklus

### 1. Bootstrap-Phase
```
Docker Compose startet (OHNE txgen Profile):
  → node01, node02, ..., node64 (Bitcoin Nodes)
  → wallet_funding (Funding Wallet Container)
  → wallet_shard_a, b, c, d (Shard Wallet Container)
  → block_scheduler (wartet auf mining_targets.json)
  → txgen_a, b, c, d werden NICHT gestartet (verwenden Profile "txgen")
```

### 2. Funding-Phase
```
funding_setup.py:
  → Erstelle Funding Wallet
  → Mine 201 Blöcke → Funding Wallet (Coinbase-Reife)
  → Erstelle Shard Wallets (txshard_a, b, c, d)
  → Generiere UTXOs pro Shard (z.B. 2400)
  → sendmany: 50 Adressen pro Batch
  → Nach jedem 4. Batch: Mine 2 Blöcke
  → Finale Bestätigung: Mine max(confirmation_blocks, total_batches + 20) Blöcke
  → Warte auf Synchronisation (Timeout skaliert mit Block-Intervall)
  → Schreibe mining_targets.json
  → Schreibe funding_snapshot.json

Nach erfolgreichem Abschluss:
  → Starte TXGen Container explizit (docker compose --profile txgen up)
  → TXGen Container beginnen mit Transaktionsgenerierung
```

### 3. Runtime-Phase (Parallel)
```
TXGen Shards (parallel):
  txgen_a → wallet_shard_a → node01: sendtoaddress(...)
  txgen_b → wallet_shard_b → node02: sendtoaddress(...)
  txgen_c → wallet_shard_c → node03: sendtoaddress(...)
  txgen_d → wallet_shard_d → node04: sendtoaddress(...)

Block Scheduler (parallel):
  block_scheduler → node02: generatetoaddress(1, address)
  → Warte 6 Sekunden
  → block_scheduler → node01: generatetoaddress(1, address)
  → Warte 6 Sekunden
  → ...

Bitcoin Nodes (parallel):
  → Empfangen TX von TXGen Shards
  → Validieren TX
  → Broadcast TX im P2P-Netzwerk
  → Empfangen Mining-Befehle von Block Scheduler
  → Erstellen Blöcke mit TX aus Mempool
  → Broadcast Blöcke im P2P-Netzwerk
```

### 4. Data Collection-Phase
```
Telemetry Playbook:
  → Sammle txlog_a.csv, txlog_b.csv, txlog_c.csv, txlog_d.csv
  → Sammle block_scheduler.log
  → Konvertiere block_scheduler.log → mining.csv
  → Extrahiere Confirmations: txlog.csv → confirmations.csv (mit Block-Hashes)
  → Berechne Metriken: metrics.py
  → Generiere Plots
```

---

## Realitätsvergleich: Komponente für Komponente

| Komponente | Mainnet-Pendant | Realismus | Begründung |
|------------|----------------|-----------|------------|
| **Bitcoin Nodes** | Full Nodes | ✅ **Sehr realistisch** | Entspricht echten Bitcoin Core Nodes |
| **Funding Wallet** | Initial Coin Distribution | ⚠️ **Teilweise** | Mainnet hat keine zentrale Funding Wallet, aber initiale Verteilung existiert |
| **Shard Wallets** | User/Service Wallets | ✅ **Realistisch** | Entspricht echten Bitcoin Wallets |
| **TXGen Shards** | Transaktionsgeneratoren | ✅ **Realistisch** | Simuliert echte Transaktionsquellen (Exchanges, Payment Processors) |
| **Block Scheduler** | Mining-Pool/Network | ❌ **Nicht realistisch** | Mainnet: Dezentral, zufällig, Proof-of-Work. Unser Setup: Zentralisiert, deterministisch |

---

## Vorteile der neuen Architektur

1. **Saubere Trennung**: Mining und TX-Generierung sind getrennt (wie im Mainnet)
2. **Skalierbarkeit**: Mehr Shards = mehr parallele TX-Generierung
3. **Reproduzierbarkeit**: Deterministisches Mining ermöglicht exakte Replikation
4. **Genauigkeit**: Hash-basierte TX-zu-Block-Zuordnung für präzise Metriken
5. **Realismus**: Multi-Wallet-Architektur näher am echten Netzwerk

## Nachteile / Einschränkungen

1. **Deterministisches Mining**: Nicht realistisch für Mainnet (aber nötig für Experimente)
2. **Feste Block-Intervalle**: Mainnet hat variable Intervalle (aber besser für kontrollierte Experimente)
3. **Zentralisierter Scheduler**: Mainnet ist dezentral (aber nötig für Determinismus)

---

## Zusammenfassung

Die neue Architektur ist **strukturell realistischer** (separates Mining, Multi-Wallet, parallele TX-Generierung) als die alte, aber **operativ weniger realistisch** (deterministisches Mining, feste Intervalle). Sie ist **optimal für Experimente**, weil sie reproduzierbar, skalierbar und analysierbar ist, während sie die wichtigsten strukturellen Aspekte des Bitcoin-Netzwerks nachbildet.

