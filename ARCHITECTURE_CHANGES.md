# Architekturänderungen - Multi-Wallet & Block-Scheduler

## Übersicht

Heute haben wir die Bitcoin Fault Lab Architektur von einer monolithischen zu einer modularen Multi-Wallet-Architektur mit separatem Block-Scheduler umgebaut.

## Hauptänderungen

### 1. Multi-Wallet-Topologie

**Vorher:**
- Einzelne Wallet für alle Transaktionen
- TXGen verwaltet Funding und Spending selbst

**Jetzt:**
- **Funding Wallet**: Dedizierte Wallet für initiale Coin-Verteilung
- **Spender Wallets** (`txshard_a` bis `txshard_d`): Separate Wallets für Transaktionsgenerierung
- Jede Spender-Wallet verwendet descriptor-basierte Wallets für effizientes UTXO-Management

**Vorteil:** Klare Trennung von Funding und Spending, bessere Skalierbarkeit

### 2. Multi-TXGen-Shards

**Vorher:**
- Ein einzelner `txgen` Service generiert alle Transaktionen

**Jetzt:**
- 4 separate `txgen` Services (Shards), jeder verbunden mit einer eigenen Spender-Wallet
- Jeder Shard generiert einen Teil der Gesamt-Transaktionsrate
- Parallele Transaktionsgenerierung

**Vorteil:** Bessere Lastverteilung, realistischere parallele Transaktionsgenerierung

### 3. Separater Block-Scheduler

**Vorher:**
- `txgen.py` enthält Notfall-Mining-Logik
- Mining passiert nur bei Problemen (z.B. Mempool-Stau)

**Jetzt:**
- Dedizierter `block_scheduler` Service
- Deterministisches Mining mit festem Intervall (6 Sekunden)
- Rotierende Miner-Auswahl basierend auf Seed
- Mining ist komplett von Transaktionsgenerierung getrennt

**Vorteil:** Saubere Trennung von Concerns, deterministisches Verhalten für Reproduzierbarkeit

### 4. Verbesserte Datenprotokollierung

**Vorher:**
- `confirmations.csv` enthält nur Block-Höhe
- Throughput-Berechnung basiert auf Zeitfenstern

**Jetzt:**
- `confirmations.csv` enthält `confirm_block_hash` für exakte Block-Zuordnung
- `mining.csv` enthält `block_hash` für jeden Block
- Hash-basierte Throughput-Berechnung für perfekte Genauigkeit
- Fallback-Methode mit verbesserter Zeitfenster-Zuordnung

**Vorteil:** Exakte Zuordnung von TX zu Blöcken, genauere Metriken

## Realitätsnähe: Abwägung

### ✅ Realistischer

1. **Separates Mining-System**
   - Im echten Bitcoin-Netzwerk ist Mining komplett von Transaktionsgenerierung getrennt
   - Miner und User sind unterschiedliche Akteure
   - ✅ **Realistischer als vorher**

2. **Multi-Wallet-Architektur**
   - Im echten Netzwerk haben verschiedene Akteure verschiedene Wallets
   - ✅ **Realistischer als vorher**

3. **Parallele Transaktionsgenerierung**
   - Im echten Netzwerk werden Transaktionen von vielen unabhängigen Nodes generiert
   - ✅ **Realistischer als vorher**

4. **Deterministisches Block-Intervall**
   - Bitcoin hat ein **durchschnittliches** Block-Intervall von ~10 Minuten, aber mit hoher Varianz
   - Unser festes 6-Sekunden-Intervall ist für Regtest üblich, aber nicht realistisch für Mainnet
   - ⚠️ **Weniger realistisch für Mainnet, aber typisch für Regtest**

### ⚠️ Weniger realistisch

1. **Deterministisches Mining**
   - Echte Bitcoin-Miner konkurrieren mit Proof-of-Work (zufällig)
   - Unser deterministisches Mining mit Seed ist für Experimente nützlich, aber nicht realistisch
   - ⚠️ **Weniger realistisch, aber notwendig für Reproduzierbarkeit**

2. **Feste Block-Intervalle**
   - Echte Bitcoin-Blocks haben variable Intervalle (Exponentialverteilung)
   - Unser festes 6-Sekunden-Intervall eliminiert diese Varianz
   - ⚠️ **Weniger realistisch, aber besser für kontrollierte Experimente**

3. **Zentralisierter Block-Scheduler**
   - Im echten Netzwerk gibt es keinen zentralen Scheduler
   - Mining ist dezentral und kompetitiv
   - ⚠️ **Weniger realistisch, aber notwendig für deterministische Experimente**

## Fazit

**Gesamtbewertung:** Die neue Architektur ist **strukturell realistischer** (separates Mining, Multi-Wallet, parallele TX-Generierung), aber **operativ weniger realistisch** (deterministisches Mining, feste Intervalle).

**Für Experimente:** Die neue Architektur ist **besser geeignet**, weil:
- ✅ Reproduzierbare Ergebnisse durch Determinismus
- ✅ Saubere Trennung erleichtert Analyse
- ✅ Skalierbarer für größere Netzwerke
- ✅ Exaktere Metriken durch Hash-basierte Zuordnung

**Für Realismus:** Die alte Architektur war in manchen Aspekten realistischer (Notfall-Mining ähnelt realen Situationen), aber die neue Architektur ist strukturell näher am echten Bitcoin-Netzwerk.

**Empfehlung:** Die neue Architektur ist ein guter Kompromiss zwischen Realismus und Experimentierbarkeit. Für zukünftige Experimente könnten wir optional variable Block-Intervalle (Exponentialverteilung) hinzufügen, um mehr Realismus zu erreichen, während wir die Determinismus-Option für Reproduzierbarkeit beibehalten.

