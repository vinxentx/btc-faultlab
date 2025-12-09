# 🔗 Bitcoin Faultlab: Netzwerk-Dynamik und Transaktionsfluss

## 📋 Inhaltsverzeichnis

1. [System-Architektur](#system-architektur)
2. [Komponenten-Übersicht](#komponenten-übersicht)
3. [Transaktionsfluss](#transaktionsfluss)
4. [Mining-Prozess](#mining-prozess)
5. [UTXO-Management](#utxo-management)
6. [Netzwerk-Synchronisation](#netzwerk-synchronisation)
7. [Fehlerbehandlung](#fehlerbehandlung)

---

## 🏗️ System-Architektur

### Gesamtübersicht

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    BITCOIN REGTEST NETZWERK                             │
│                         (128 Nodes)                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
            ┌───────▼──────┐  ┌─────▼─────┐  ┌─────▼──────┐
            │   WALLET     │  │   TXGEN   │  │   NODES    │
            │   Container  │  │ Container │  │ (128x)     │
            └───────┬──────┘  └─────┬─────┘  └─────┬──────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
            ┌───────▼──────┐  ┌─────▼─────┐  ┌─────▼──────┐
            │  Mining      │  │  Regular  │  │  Network   │
            │  Nodes       │  │  Nodes    │  │  Peering   │
            │  (8% = 10)   │  │  (92%)    │  │            │
            └──────────────┘  └───────────┘  └────────────┘
```

### Detaillierte Komponenten-Interaktion

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          TXGEN (Orchestrator)                            │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  • Generiert Transaktionen (10 tx/s)                              │  │
│  │  • Orchestriert Mining-Intervalle (alle 3s)                       │  │
│  │  • Überwacht Wallet-Status                                        │  │
│  │  • Führt UTXO-Konsolidierung durch                                │  │
│  │  • Loggt Performance-Metriken                                     │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │                   │
        ┌───────────▼────────┐  ┌───────▼──────────┐
        │   WALLET NODE      │  │  MINING NODES    │
        │                    │  │  (node01-10)     │
        │  ┌──────────────┐  │  │                  │
        │  │ Bitcoin Core │  │  │  ┌────────────┐  │
        │  │ Wallet:      │  │  │  │ Bitcoin    │  │
        │  │ "faultlab"   │  │  │  │ Core       │  │
        │  │              │  │  │  │ (Miner)    │  │
        │  │ UTXOs:       │  │  │  └────────────┘  │
        │  │ • Confirmed  │  │  │                  │
        │  │ • Unconfirmed│  │  │ generatetoaddress│
        │  └──────────────┘  │  │                  │
        │                    │  │ Block Production │
        │  RPC Calls:        │  │                  │
        │  • getnewaddress   │  │                  │
        │  • sendtoaddress   │  │                  │
        │  • listunspent     │  │                  │
        │  • sendmany        │  │                  │
        └────────────────────┘  └──────────────────┘
                    │                   │
                    └─────────┬─────────┘
                              │
                    ┌─────────▼─────────┐
                    │   NETWORK LAYER   │
                    │                   │
                    │  • P2P Protocol   │
                    │  • Block Prop.    │
                    │  • TX Prop.       │
                    │  • Consensus      │
                    └────────────────────┘
```

---

## 🧩 Komponenten-Übersicht

### 1. **TXGEN Container** (Python Script)

```
┌─────────────────────────────────────────────────────────┐
│                    TXGEN.PY                              │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Funktionen:                                             │
│  ┌──────────────────────────────────────────────────┐  │
│  │ • Transaction Generation Loop                     │  │
│  │   - Rate: 10 tx/s (konfigurierbar)               │  │
│  │   - Interval: 0.1s zwischen Transaktionen        │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │ • Mining Orchestration                            │  │
│  │   - Interval: 3s (für rate <= 10)                │  │
│  │   - Rotation zwischen 10 Mining Nodes            │  │
│  │   - Health Checks für Miner                      │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │ • Wallet Management                               │  │
│  │   - Initial Funding (301 Blöcke)                 │  │
│  │   - Coin Maturity Check (100 Confirmations)      │  │
│  │   - Wallet Synchronisation                        │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │ • UTXO Consolidation                              │  │
│  │   - Trigger: Alle 500 Transaktionen              │  │
│  │   - Minimum: 100 UTXOs                            │  │
│  │   - Methode: sendmany (80% des Balances)         │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │ • Performance Monitoring                          │  │
│  │   - RPC Latency (getnewaddress, sendtoaddress)   │  │
│  │   - UTXO Counts (confirmed/unconfirmed)          │  │
│  │   - Rolling Throughput (30s Window)              │  │
│  │   - Emergency Mining Events                      │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
│  ┌──────────────────────────────────────────────────┐  │
│  │ • Error Handling                                  │  │
│  │   - Insufficient Funds (-6) Detection            │  │
│  │   - Emergency Mining Trigger                      │  │
│  │   - Adaptive Timeouts (abhängig von Node-Count)  │  │
│  └──────────────────────────────────────────────────┘  │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 2. **WALLET Node** (Bitcoin Core)

```
┌─────────────────────────────────────────────────────────┐
│              WALLET CONTAINER                            │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  Bitcoin Core 27.0                                       │
│  ┌──────────────────────────────────────────────────┐   │
│  │ Wallet: "faultlab"                                │   │
│  │                                                   │   │
│  │ UTXO Set:                                         │   │
│  │ ┌─────────────────────────────────────────────┐ │   │
│  │ │ Confirmed UTXOs:    ████████████░░░░░░░░░░  │ │   │
│  │ │ Unconfirmed UTXOs:  ████░░░░░░░░░░░░░░░░░░  │ │   │
│  │ │ Total: ~150-400 UTXOs (dynamisch)           │ │   │
│  │ └─────────────────────────────────────────────┘ │   │
│  │                                                   │   │
│  │ Balance:                                          │   │
│  │ ┌─────────────────────────────────────────────┐ │   │
│  │ │ Confirmed:   ~20-50 BTC (spendable)         │ │   │
│  │ │ Unconfirmed: ~0.1-1 BTC (pending)          │ │   │
│  │ └─────────────────────────────────────────────┘ │   │
│  └──────────────────────────────────────────────────┘   │
│                                                          │
│  RPC Endpoint: http://wallet:18443                      │
│  P2P Port: 18444                                         │
│                                                          │
│  Verbindungen:                                           │
│  • node01-08 (direkte Peers)                            │
│  • Alle anderen Nodes (via P2P)                         │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 3. **Mining Nodes** (8% des Netzwerks)

```
┌─────────────────────────────────────────────────────────┐
│              MINING NODES (10 von 128)                  │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  node01 ──┐                                              │
│  node02 ──┤                                              │
│  node03 ──┤                                              │
│  node04 ──┤  ┌──────────────────────────────────────┐   │
│  node05 ──┼──┤  Round-Robin Mining Rotation         │   │
│  node06 ──┤  │                                      │   │
│  node07 ──┤  │  • Health Check vor Mining          │   │
│  node08 ──┤  │  • Fallback bei Ausfall             │   │
│  node09 ──┤  │  • Gleichmäßige Verteilung          │   │
│  node10 ──┘  └──────────────────────────────────────┘   │
│                                                          │
│  Mining Interval: 3 Sekunden                            │
│  Block Reward: 50 BTC (Regtest)                         │
│  Target Address: Wallet "faultlab"                      │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

### 4. **Regular Nodes** (92% des Netzwerks)

```
┌─────────────────────────────────────────────────────────┐
│              REGULAR NODES (118 von 128)                 │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  node11 ──┐                                              │
│  node12 ──┤                                              │
│  node13 ──┤                                              │
│  ...      │  ┌──────────────────────────────────────┐   │
│  node128 ─┘  │  • Validieren Blöcke                │   │
│              │  • Weiterleiten Transaktionen        │   │
│              │  • Blockchain Synchronisation         │   │
│              │  • Netzwerk-Messung                   │   │
│              └──────────────────────────────────────┘   │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 🔄 Transaktionsfluss

### Standard-Transaktionsfluss (Happy Path)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    TRANSACTION LIFECYCLE                             │
└─────────────────────────────────────────────────────────────────────┘

    TXGEN                    WALLET                  MINING NODE
     │                         │                          │
     │ 1. getnewaddress()      │                          │
     ├─────────────────────────>│                          │
     │                         │                          │
     │ 2. Neue Adresse         │                          │
     │<─────────────────────────┤                          │
     │                         │                          │
     │ 3. sendtoaddress(0.0001)│                          │
     ├─────────────────────────>│                          │
     │                         │                          │
     │                         │ 4. Erstellt TX           │
     │                         │    (wählt UTXOs)         │
     │                         │                          │
     │ 5. TXID zurück          │                          │
     │<─────────────────────────┤                          │
     │                         │                          │
     │                         │ 6. Broadcast TX          │
     │                         ├──────────────────────────>│
     │                         │                          │
     │                         │                          │ 7. Validiert TX
     │                         │                          │    & fügt zu
     │                         │                          │    Mempool hinzu
     │                         │                          │
     │                         │                          │ 8. Mining Interval
     │                         │                          │    (alle 3s)
     │                         │                          │
     │                         │                          │ 9. generatetoaddress(1)
     │                         │                          │    Erstellt Block
     │                         │                          │
     │                         │                          │ 10. Block enthält TX
     │                         │                          │     & wird propagiert
     │                         │                          │
     │                         │ 11. Empfängt Block       │
     │                         │<──────────────────────────┤
     │                         │                          │
     │                         │ 12. TX bestätigt         │
     │                         │    (1 Confirmation)      │
     │                         │                          │
     │                         │                          │
     ⏱️  Gesamtzeit: ~3-6 Sekunden (von TX-Erstellung bis Bestätigung)
```

### Detaillierter Ablauf mit Timing

```
Zeitachse (in Sekunden):

t=0.0s    TXGEN: Start Transaction Loop
          │
t=0.0s    TXGEN → WALLET: getnewaddress()
          │       RPC Time: ~5-50ms
          │
t=0.05s   WALLET → TXGEN: Neue Adresse zurück
          │
t=0.05s   TXGEN → WALLET: sendtoaddress(dst, 0.0001)
          │       RPC Time: ~10-200ms (abhängig von UTXO-Count)
          │
t=0.25s   WALLET: Erstellt Transaktion
          │       • Wählt UTXOs (Coin Selection)
          │       • Berechnet Fees
          │       • Signiert TX
          │
t=0.25s   WALLET → TXGEN: TXID zurück
          │
t=0.25s   WALLET: Broadcast TX ins Netzwerk
          │       (P2P Propagation)
          │
t=0.3s    NETWORK: TX propagiert zu allen Nodes
          │       (node01-128 empfangen TX)
          │
t=0.3s    MINING NODES: TX in Mempool
          │       (Warten auf Mining-Interval)
          │
t=3.0s    TXGEN: Mining-Interval erreicht
          │       (next_mine = t + 3.0s)
          │
t=3.0s    TXGEN → MINER: generatetoaddress(1, addr)
          │       RPC Time: ~500ms-5s
          │
t=3.5s    MINER: Block erstellt
          │       • Enthält TX
          │       • Proof-of-Work (Regtest = trivial)
          │
t=3.5s    MINER: Block propagiert ins Netzwerk
          │       (P2P Block Propagation)
          │
t=3.6s    NETWORK: Alle Nodes empfangen Block
          │       • Validieren Block
          │       • Aktualisieren Blockchain
          │
t=3.6s    WALLET: Block empfangen
          │       • TX hat jetzt 1 Confirmation
          │       • UTXO wird "confirmed"
          │
t=3.6s    ✅ TRANSACTION CONFIRMED
          │
          └─> Gesamt-Latency: ~3.6 Sekunden
```

---

## ⛏️ Mining-Prozess

### Mining-Orchestrierung

```
┌─────────────────────────────────────────────────────────────────────┐
│                    MINING INTERVAL LOGIC                            │
└─────────────────────────────────────────────────────────────────────┘

    TXGEN Main Loop
         │
         │  ┌─────────────────────────────────────┐
         │  │  Jede Transaktion:                  │
         │  │  • Prüfe: time.time() >= next_mine? │
         │  │  • Wenn JA → Trigger Mining         │
         │  └─────────────────────────────────────┘
         │              │
         │              ▼
         │  ┌─────────────────────────────────────┐
         │  │  Mining Trigger                     │
         │  │  1. get_healthy_miner()             │
         │  │     • Round-Robin Rotation          │
         │  │     • Health Check (getblockcount)  │
         │  │     • Fallback bei Ausfall          │
         │  └─────────────────────────────────────┘
         │              │
         │              ▼
         │  ┌─────────────────────────────────────┐
         │  │  RPC Call:                          │
         │  │  generatetoaddress(1, wallet_addr)  │
         │  │  • Timeout: 20-40s (adaptive)       │
         │  │  • Block Reward → Wallet            │
         │  └─────────────────────────────────────┘
         │              │
         │              ▼
         │  ┌─────────────────────────────────────┐
         │  │  Block Propagation                  │
         │  │  • Miner propagiert Block           │
         │  │  • Alle Nodes validieren            │
         │  │  • Wallet aktualisiert UTXOs        │
         │  └─────────────────────────────────────┘
         │              │
         │              ▼
         │  ┌─────────────────────────────────────┐
         │  │  Update next_mine                   │
         │  │  next_mine = time.time() + 3.0s     │
         │  └─────────────────────────────────────┘
         │
         └─> Weiter mit nächster Transaktion
```

### Mining-Rotation (Round-Robin)

```
┌─────────────────────────────────────────────────────────────────────┐
│              MINING ROTATION BEISPIEL (10 Miner)                    │
└─────────────────────────────────────────────────────────────────────┘

Block #1  →  node01  ⛏️  [████████████████████] 100%
Block #2  →  node02  ⛏️  [████████████████████] 100%
Block #3  →  node03  ⛏️  [████████████████████] 100%
Block #4  →  node04  ⛏️  [████████████████████] 100%
Block #5  →  node05  ⛏️  [████████████████████] 100%
Block #6  →  node06  ⛏️  [████████████████████] 100%
Block #7  →  node07  ⛏️  [████████████████████] 100%
Block #8  →  node08  ⛏️  [████████████████████] 100%
Block #9  →  node09  ⛏️  [████████████████████] 100%
Block #10 →  node10  ⛏️  [████████████████████] 100%
Block #11 →  node01  ⛏️  [████████████████████] 100%  (Rotation)

...

Nach 100 Blöcken:
┌─────────────────────────────────────────────────────┐
│  Mining Statistics:                                 │
│  node01: 10 blocks (10.0%)                         │
│  node02: 10 blocks (10.0%)                         │
│  node03: 10 blocks (10.0%)                         │
│  ...                                                │
│  node10: 10 blocks (10.0%)                         │
│                                                     │
│  ✅ Gleichmäßige Verteilung                        │
└─────────────────────────────────────────────────────┘
```

### Emergency Mining

```
┌─────────────────────────────────────────────────────────────────────┐
│                    EMERGENCY MINING TRIGGER                         │
└─────────────────────────────────────────────────────────────────────┘

    TXGEN                    WALLET                  MINING NODE
     │                         │                          │
     │ 1. sendtoaddress()      │                          │
     ├─────────────────────────>│                          │
     │                         │                          │
     │ 2. ERROR:               │                          │
     │    "Insufficient funds" │                          │
     │    oder "-6"           │                          │
     │<─────────────────────────┤                          │
     │                         │                          │
     │ 3. Error Detection      │                          │
     │    ┌─────────────────┐  │                          │
     │    │ • "insufficient │  │                          │
     │    │   funds"        │  │                          │
     │    │ • "-6"          │  │                          │
     │    │ • "Unconfirmed  │  │                          │
     │    │   UTXOs"        │  │                          │
     │    └─────────────────┘  │                          │
     │                         │                          │
     │ 4. Emergency Mining!    │                          │
     │    ┌─────────────────┐  │                          │
     │    │ Sofort Block    │  │                          │
     │    │ produzieren     │  │                          │
     │    └─────────────────┘  │                          │
     │                         │                          │
     │ 5. generatetoaddress(1) │                          │
     ├───────────────────────────────────────────────────>│
     │                         │                          │
     │                         │                          │ 6. Block erstellt
     │                         │                          │    (enthält pending TXs)
     │                         │                          │
     │                         │ 7. Block propagiert      │
     │                         │<──────────────────────────┤
     │                         │                          │
     │                         │ 8. Pending TXs           │
     │                         │    werden bestätigt       │
     │                         │                          │
     │ 9. Wait 2s              │                          │
     │    (für Propagation)    │                          │
     │                         │                          │
     │ 10. Retry Transaction   │                          │
     │     (jetzt sollten      │                          │
     │      UTXOs verfügbar    │                          │
     │      sein)              │                          │
     │                         │                          │
     │ 11. next_mine reset     │                          │
     │     (verhindert         │                          │
     │      doppeltes Mining)  │                          │
     │                         │                          │
```

---

## 💰 UTXO-Management

### UTXO-Lebenszyklus

```
┌─────────────────────────────────────────────────────────────────────┐
│                    UTXO LIFECYCLE                                   │
└─────────────────────────────────────────────────────────────────────┘

1. BLOCK MINING
   ┌─────────────────────────────────────┐
   │ Miner produziert Block              │
   │ • Block Reward: 50 BTC              │
   │ • Adresse: Wallet "faultlab"        │
   └─────────────────────────────────────┘
                    │
                    ▼
2. UTXO ERSTELLUNG
   ┌─────────────────────────────────────┐
   │ Neuer UTXO im Wallet:                │
   │ • Amount: 50.00000000 BTC            │
   │ • Confirmations: 0 (unconfirmed)    │
   │ • Status: Nicht spendable           │
   └─────────────────────────────────────┘
                    │
                    ▼
3. CONFIRMATION
   ┌─────────────────────────────────────┐
   │ Nach 1 Block:                       │
   │ • Confirmations: 1                  │
   │ • Status: Confirmed                 │
   │ • Spendable: JA                     │
   └─────────────────────────────────────┘
                    │
                    ▼
4. TRANSACTION ERSTELLUNG
   ┌─────────────────────────────────────┐
   │ sendtoaddress(dst, 0.0001)           │
   │ • Wählt UTXO (z.B. 50 BTC)          │
   │ • Sendet 0.0001 BTC                 │
   │ • Erstellt Change: 49.9999 BTC       │
   │ • Fees: ~0.00001 BTC                │
   └─────────────────────────────────────┘
                    │
                    ▼
5. UTXO VERBRAUCHUNG
   ┌─────────────────────────────────────┐
   │ Original UTXO:                      │
   │ • Status: SPENT                     │
   │ • Wird aus UTXO-Set entfernt         │
   └─────────────────────────────────────┘
                    │
                    ▼
6. NEUE UTXOs
   ┌─────────────────────────────────────┐
   │ Neue UTXOs erstellt:                │
   │ • UTXO #1: 0.0001 BTC → dst         │
   │   (unconfirmed)                      │
   │ • UTXO #2: 49.9999 BTC → wallet     │
   │   (unconfirmed, Change)              │
   └─────────────────────────────────────┘
                    │
                    ▼
7. BESTÄTIGUNG
   ┌─────────────────────────────────────┐
   │ Nach Block-Mining:                   │
   │ • Beide UTXOs: confirmed             │
   │ • Verfügbar für nächste TX          │
   └─────────────────────────────────────┘
```

### UTXO-Akkumulation Problem

```
┌─────────────────────────────────────────────────────────────────────┐
│              UTXO ACCUMULATION (Ohne Konsolidierung)                │
└─────────────────────────────────────────────────────────────────────┘

Zeit →  t=0s        t=30s       t=60s       t=90s       t=120s
        │           │           │           │           │
        │           │           │           │           │
UTXO    │           │           │           │           │
Count   │           │           │           │           │
        │           │           │           │           │
  400 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  350 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  300 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  250 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  200 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  150 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  100 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
   50 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
    0 ──┴───────────┴───────────┴───────────┴───────────┴───
        │           │           │           │           │
        Start       TX #300     TX #600     TX #900     TX #1200

Problem:
• Jede Transaktion erstellt 2 neue UTXOs (Output + Change)
• UTXO-Count wächst linear: ~2 UTXOs pro Transaktion
• Nach 600 TXs: ~1200 UTXOs (600 Outputs + 600 Change)
• Wallet-Performance degradiert:
  - RPC-Calls werden langsamer (Coin Selection)
  - sendtoaddress() braucht länger (10ms → 200ms)
  - Throughput sinkt (10 tx/s → 5 tx/s)
```

### UTXO-Konsolidierung

```
┌─────────────────────────────────────────────────────────────────────┐
│              UTXO CONSOLIDATION (Mit Konsolidierung)                │
└─────────────────────────────────────────────────────────────────────┘

Zeit →  t=0s        t=30s       t=60s       t=90s       t=120s
        │           │           │           │           │
        │           │           │           │           │
UTXO    │           │           │           │           │
Count   │           │           │           │           │
        │           │           │           │           │
  400 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  350 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  300 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  250 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  200 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  150 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
  100 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
   50 ──┼───────────┼───────────┼───────────┼───────────┼───
        │           │           │           │           │
    0 ──┴───────────┴───────────┴───────────┴───────────┴───
        │           │           │           │           │
        Start       TX #500     TX #1000    TX #1500    TX #2000
                    │           │           │           │
                    ▼           ▼           ▼           ▼
                Konsolidierung  Konsolidierung  Konsolidierung

Konsolidierungs-Prozess (alle 500 Transaktionen):

1. PRÜFUNG
   ┌─────────────────────────────────────┐
   │ if tx_count % 500 == 0:            │
   │   if confirmed_utxos >= 100:       │
   │     → Trigger Consolidation         │
   └─────────────────────────────────────┘

2. UTXO-SAMMLUNG
   ┌─────────────────────────────────────┐
   │ listunspent(1, 9999999)            │
   │ • Nur confirmed UTXOs              │
   │ • Beispiel: 150 UTXOs               │
   │ • Total: 25.5 BTC                  │
   └─────────────────────────────────────┘

3. KONSOLIDIERUNG
   ┌─────────────────────────────────────┐
   │ sendmany("", {addr: 20.4 BTC})      │
   │ • 80% von 25.5 BTC = 20.4 BTC       │
   │ • Bitcoin Core wählt UTXOs          │
   │ • Automatische Fee-Berechnung       │
   │ • Erstellt 1 großen UTXO            │
   └─────────────────────────────────────┘

4. SOFORTIGES MINING
   ┌─────────────────────────────────────┐
   │ generatetoaddress(1, addr)          │
   │ • Bestätigt Consolidation-TX        │
   │ • Schnelle Bestätigung              │
   └─────────────────────────────────────┘

Ergebnis:
• UTXO-Count bleibt stabil (~50-150 UTXOs)
• Wallet-Performance bleibt konstant
• RPC-Latency bleibt niedrig (~10-50ms)
• Throughput bleibt stabil (~10 tx/s)
```

### UTXO-Konsolidierung Visualisierung

```
Vor Konsolidierung:
┌─────────────────────────────────────────────────────────┐
│  Wallet UTXO Set (150 UTXOs)                           │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  UTXO #1:   0.0001 BTC  █                               │
│  UTXO #2:   0.0001 BTC  █                               │
│  UTXO #3:   0.0001 BTC  █                               │
│  UTXO #4:   0.0001 BTC  █                               │
│  ...                                                    │
│  UTXO #150: 0.0001 BTC  █                               │
│                                                          │
│  Total: 25.5 BTC (150 × ~0.17 BTC avg)                  │
│                                                          │
│  Problem:                                               │
│  • Viele kleine UTXOs                                   │
│  • Langsame Coin Selection                              │
│  • Hohe RPC-Latency                                    │
│                                                          │
└─────────────────────────────────────────────────────────┘

                    │
                    │ sendmany("", {addr: 20.4 BTC})
                    │ (80% von 25.5 BTC)
                    ▼

Nach Konsolidierung:
┌─────────────────────────────────────────────────────────┐
│  Wallet UTXO Set (~10 UTXOs)                            │
├─────────────────────────────────────────────────────────┤
│                                                          │
│  UTXO #1:   20.4 BTC    ████████████████████████████    │
│  UTXO #2:   0.0001 BTC  █                               │
│  UTXO #3:   0.0001 BTC  █                               │
│  ...                                                    │
│  UTXO #10:  0.0001 BTC  █                               │
│                                                          │
│  Total: ~25.5 BTC (1 großer + 9 kleine)                  │
│                                                          │
│  Vorteil:                                                │
│  • Weniger UTXOs                                        │
│  • Schnellere Coin Selection                           │
│  • Niedrige RPC-Latency                                │
│                                                          │
└─────────────────────────────────────────────────────────┘
```

---

## 🌐 Netzwerk-Synchronisation

### Initial Setup & Synchronisation

```
┌─────────────────────────────────────────────────────────────────────┐
│              INITIAL SETUP SEQUENCE                                 │
└─────────────────────────────────────────────────────────────────────┘

Phase 1: Netzwerk-Start
┌─────────────────────────────────────────────────────┐
│  1. Alle 128 Nodes starten                         │
│  2. P2P-Verbindungen werden aufgebaut               │
│  3. Nodes synchronisieren Blockchain                │
│  4. Wallet-Node verbindet zu node01-08             │
└─────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 2: Wallet-Setup
┌─────────────────────────────────────────────────────┐
│  1. TXGEN wartet auf Wallet-RPC (max 600s)         │
│  2. Wallet erstellt: "faultlab"                     │
│  3. Wallet lädt: "faultlab"                         │
│  4. Wallet-Adresse generiert                        │
└─────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 3: Initial Funding
┌─────────────────────────────────────────────────────┐
│  1. 201 Blöcke generiert (distributed mining)       │
│     • node01: 21 Blöcke                             │
│     • node02-10: je 20 Blöcke                       │
│  2. 100 zusätzliche Blöcke (Coin Maturity)         │
│     • Total: 301 Blöcke                             │
│     • Block Reward: 50 BTC pro Block                │
│     • Total Balance: ~15,050 BTC                    │
└─────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 4: Wallet-Synchronisation
┌─────────────────────────────────────────────────────┐
│  1. TXGEN prüft Wallet-Height vs. Network-Height     │
│  2. Wartet bis Wallet synchronisiert (max 180s)     │
│  3. Verifiziert Balance (max 120s)                  │
│  4. Prüft Coin Maturity (100 Confirmations)         │
└─────────────────────────────────────────────────────┘
                    │
                    ▼
Phase 5: Transaction Generation Start
┌─────────────────────────────────────────────────────┐
│  ✅ Wallet bereit:                                   │
│     • Synchronisiert                                 │
│     • Mature Coins verfügbar                        │
│     • Spendable Balance: > 0.001 BTC                │
│                                                      │
│  ⚡ TXGEN startet Transaction Loop                  │
│     • Rate: 10 tx/s                                 │
│     • Mining: alle 3s                                │
└─────────────────────────────────────────────────────┘
```

### Blockchain-Synchronisation

```
┌─────────────────────────────────────────────────────────────────────┐
│              BLOCKCHAIN SYNCHRONISATION                              │
└─────────────────────────────────────────────────────────────────────┘

    WALLET NODE              MINING NODE              REGULAR NODES
         │                        │                        │
         │                        │                        │
    Height: 0                Height: 0                Height: 0
         │                        │                        │
         │                        │                        │
         │  ┌──────────────────────────────────────────────┐
         │  │  Initial Mining (301 Blöcke)                 │
         │  └──────────────────────────────────────────────┘
         │                        │                        │
         │                        │                        │
    Height: 301              Height: 301              Height: 301
         │                        │                        │
         │                        │                        │
         │  ┌──────────────────────────────────────────────┐
         │  │  Block Propagation (P2P)                    │
         │  │  • Miner propagiert Block                   │
         │  │  • Nodes validieren & akzeptieren            │
         │  │  • Wallet empfängt Block                    │
         │  └──────────────────────────────────────────────┘
         │                        │                        │
         │                        │                        │
    Height: 302              Height: 302              Height: 302
         │                        │                        │
         │                        │                        │
         │  ┌──────────────────────────────────────────────┐
         │  │  Synchronisation Check                       │
         │  │  • TXGEN prüft: wallet_height >=             │
         │  │    network_height - 1                        │
         │  │  • Wenn nicht → Warte 2s & Retry            │
         │  └──────────────────────────────────────────────┘
         │                        │                        │
         │                        │                        │
    ✅ SYNCED                ✅ SYNCED                ✅ SYNCED
```

---

## ⚠️ Fehlerbehandlung

### Error-Handling-Fluss

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ERROR HANDLING LOGIC                             │
└─────────────────────────────────────────────────────────────────────┘

    TXGEN Transaction Loop
         │
         │  ┌─────────────────────────────────────┐
         │  │  sendtoaddress()                    │
         │  └─────────────────────────────────────┘
         │              │
         │              ▼
         │  ┌─────────────────────────────────────┐
         │  │  Erfolg?                            │
         │  └─────────────────────────────────────┘
         │      │                    │
         │   JA │                    │ NEIN
         │      │                    │
         │      ▼                    ▼
         │  ┌─────────┐      ┌──────────────────┐
         │  │ Log TX  │      │ Error Detection   │
         │  │ Continue│      │                  │
         │  └─────────┘      └──────────────────┘
         │                           │
         │                           ▼
         │              ┌─────────────────────────────┐
         │              │ Error Type Check            │
         │              │                             │
         │              │ • "insufficient funds"     │
         │              │ • "-6"                      │
         │              │ • "Unconfirmed UTXOs"      │
         │              └─────────────────────────────┘
         │                           │
         │                           ▼
         │              ┌─────────────────────────────┐
         │              │ Emergency Mining Trigger    │
         │              │                             │
         │              │ 1. get_healthy_miner()      │
         │              │ 2. generatetoaddress(1)     │
         │              │ 3. Wait 2s                  │
         │              │ 4. Reset next_mine          │
         │              └─────────────────────────────┘
         │                           │
         │                           ▼
         │              ┌─────────────────────────────┐
         │              │ Retry Transaction          │
         │              │ (nach Emergency Mining)     │
         │              └─────────────────────────────┘
         │                           │
         │                           ▼
         │              ┌─────────────────────────────┐
         │              │ Erfolg?                     │
         │              └─────────────────────────────┘
         │                  │              │
         │               JA │              │ NEIN
         │                  │              │
         │                  ▼              ▼
         │              ┌─────────┐  ┌──────────────┐
         │              │ Continue │  │ Log Error    │
         │              │          │  │ Sleep 1s     │
         │              │          │  │ Retry        │
         │              └─────────┘  └──────────────┘
         │
         └─> Weiter mit nächster Transaktion
```

### Adaptive Timeouts

```
┌─────────────────────────────────────────────────────────────────────┐
│              ADAPTIVE TIMEOUTS (Abhängig von Node-Count)             │
└─────────────────────────────────────────────────────────────────────┘

Node Count    RPC Timeout    Mining Timeout    Sync Timeout
─────────────────────────────────────────────────────────────────────
< 64 Nodes    10s            20s               60s
64-127 Nodes  15s            30s               120s
128+ Nodes    20s            40s               180s

Beispiel für 128 Nodes:
┌─────────────────────────────────────────────────────┐
│  Operation              Timeout                      │
├─────────────────────────────────────────────────────┤
│  getnewaddress         20s                           │
│  sendtoaddress         20s                           │
│  generatetoaddress    40s                            │
│  listunspent          20s                            │
│  Wallet Sync Check    180s                           │
└─────────────────────────────────────────────────────┘
```

---

## 📊 Performance-Metriken

### Gemessene Metriken

```
┌─────────────────────────────────────────────────────────────────────┐
│              PERFORMANCE METRICS (txlog_performance.csv)             │
└─────────────────────────────────────────────────────────────────────┘

1. RPC TIMING
   ┌─────────────────────────────────────┐
   │ • getnewaddress_time_ms             │
   │   (Zeit für Adress-Generierung)     │
   │ • sendtoaddress_time_ms              │
   │   (Zeit für TX-Erstellung)          │
   │ • total_time_ms                      │
   │   (Gesamt-RPC-Zeit)                  │
   └─────────────────────────────────────┘

2. UTXO STATISTICS
   ┌─────────────────────────────────────┐
   │ • utxo_count                        │
   │   (Gesamt-UTXO-Anzahl)              │
   │ • confirmed_utxos                    │
   │   (Bestätigte UTXOs)                 │
   │ • unconfirmed_utxos                 │
   │   (Unbestätigte UTXOs)               │
   └─────────────────────────────────────┘

3. THROUGHPUT
   ┌─────────────────────────────────────┐
   │ • rolling_throughput_tx_s           │
   │   (TX/s über 30s Window)             │
   └─────────────────────────────────────┘

4. EMERGENCY EVENTS
   ┌─────────────────────────────────────┐
   │ • emergency_mining_triggered        │
   │   (1 = Emergency Mining ausgelöst)  │
   └─────────────────────────────────────┘
```

---

## 🎯 Zusammenfassung: Netzwerk-Dynamik

### Warum ein einzelnes Wallet?

```
┌─────────────────────────────────────────────────────────────────────┐
│              WARUM EIN WALLET FÜR NETZWERK-MESSUNG?                │
└─────────────────────────────────────────────────────────────────────┘

Perspektive: Netzwerk-Performance (nicht User-Performance)
─────────────────────────────────────────────────────────────────────

✅ EIN WALLET IST KORREKT, WEIL:

1. NETZWERK-MESSUNG
   ┌─────────────────────────────────────┐
   │ • Wir messen die Performance des    │
   │   gesamten Netzwerks (128 Nodes)    │
   │ • Nicht die Performance eines       │
   │   einzelnen Users                    │
   │ • Wallet ist nur "Transaktions-     │
   │   Generator" für das Netzwerk        │
   └─────────────────────────────────────┘

2. REALISTISCHE LAST
   ┌─────────────────────────────────────┐
   │ • Ein Wallet mit 10 tx/s erzeugt     │
   │   realistische Netzwerk-Last        │
   │ • Mehrere Wallets würden die Last   │
   │   künstlich erhöhen                  │
   │ • Fokus liegt auf Netzwerk-         │
   │   Performance, nicht Wallet-        │
   │   Performance                        │
   └─────────────────────────────────────┘

3. NETZWERK-BEANSPRUCHUNG
   ┌─────────────────────────────────────┐
   │ • Jede Transaktion wird von allen   │
   │   128 Nodes verarbeitet             │
   │ • Block-Propagation wird gemessen   │
   │ • Konsens-Mechanismus wird getestet │
   │ • Netzwerk-Latenz wird analysiert   │
   └─────────────────────────────────────┘

4. UTXO-PROBLEM GELÖST
   ┌─────────────────────────────────────┐
   │ • UTXO-Konsolidierung verhindert    │
   │   Performance-Degradation            │
   │ • Wallet bleibt performant           │
   │ • Fokus bleibt auf Netzwerk-        │
   │   Messung                            │
   └─────────────────────────────────────┘
```

### Datenfluss-Übersicht

```
┌─────────────────────────────────────────────────────────────────────┐
│                    KOMPLETTER DATENFLUSS                            │
└─────────────────────────────────────────────────────────────────────┘

TXGEN
  │
  │ 1. Generiert Transaktion (10 tx/s)
  │
  ▼
WALLET
  │
  │ 2. Erstellt TX (sendtoaddress)
  │    • Wählt UTXOs
  │    • Signiert TX
  │
  ▼
NETZWERK (128 Nodes)
  │
  │ 3. TX-Propagation (P2P)
  │    • Alle Nodes empfangen TX
  │    • Validieren TX
  │    • Fügen zu Mempool hinzu
  │
  ▼
MINING NODES (10 Miner)
  │
  │ 4. Mining (alle 3s)
  │    • Erstellt Block
  │    • Enthält TXs aus Mempool
  │
  ▼
NETZWERK (128 Nodes)
  │
  │ 5. Block-Propagation (P2P)
  │    • Alle Nodes empfangen Block
  │    • Validieren Block
  │    • Aktualisieren Blockchain
  │
  ▼
WALLET
  │
  │ 6. TX bestätigt
  │    • UTXOs werden confirmed
  │    • Verfügbar für nächste TX
  │
  ▼
TXGEN
  │
  │ 7. Performance-Metriken
  │    • Loggt RPC-Times
  │    • Loggt UTXO-Counts
  │    • Loggt Throughput
  │
  ▼
METRICS FILES
  │
  │ • txlog.csv
  │ • mining.csv
  │ • txlog_performance.csv
  │
  └─> Analysiert für Netzwerk-Performance
```

---

## 📝 Fazit

Das **Bitcoin Faultlab** verwendet ein **zentrales Wallet** mit **UTXO-Konsolidierung**, um die **Netzwerk-Performance** unter Fehlerbedingungen zu messen. Der Fokus liegt auf der **Messung des gesamten Netzwerks** (128 Nodes), nicht auf der Performance eines einzelnen Users. Das Wallet dient als **Transaktions-Generator**, der realistische Last auf das Netzwerk ausübt, während die **UTXO-Konsolidierung** sicherstellt, dass die Wallet-Performance stabil bleibt und nicht die Netzwerk-Messung beeinträchtigt.

---

*Erstellt: 2025-01-XX*  
*Version: 1.0*
