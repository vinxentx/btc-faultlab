# Real Mining Proof-of-Concept (Regtest)

Diese PoC-Umgebung zeigt, wie in `regtest` echtes Proof-of-Work-Mining mit
zehn unabhängigen Minern umgesetzt werden kann, ohne den bestehenden
Experimentaufbau zu verändern. Alle Komponenten sind in diesem Ordner isoliert
und lassen sich separat starten, testen und wieder entfernen.

## Übersicht

- **`docker-compose.yml`** – startet einen einzelnen Bitcoin-Core-Node im
  Regtest sowie einen Python-Mining-Controller.
- **`miner/`** – Docker-Build-Kontext für den Controller mit allen
  Abhängigkeiten (Python 3.11, `python-bitcoinlib`, `requests`).
- **`config/bitcoin.conf`** – Basiskonfiguration für den Node (Regtest,
  RPC-Zugriff, deaktiviertes SegWit für einfaches Mining).
- **`.env.example`** – Beispiel-Umgebungsvariablen (kann zu `.env` kopiert und
  angepasst werden).

Der Mining-Controller (`run_mining_demo.py`) startet standardmäßig zehn Worker
Threads. Jeder Worker verhält sich wie ein eigenständiger Miner: Er baut Blöcke
aus `getblocktemplate`, erzeugt eine eigene Coinbase-Transaktion, sucht per
Real-PoW eine gültige Nonce und submitted den gefundenen Block via RPC.

## Schnellstart

1. **`.env` erzeugen**

   ```bash
   cd real_mining_poc
   cp env.example .env
   ```

2. **Docker-Compose bauen & starten**

   ```bash
   docker compose up --build miner-controller
   ```

   Dies startet zuerst den Bitcoin-Core-Node (`regtest`). Sobald der Node
   gesund ist, beginnt der Controller automatisch mit dem Mining. Standard-
   Einstellungen:

   - 10 Miner (Threads)
   - 10 Blöcke Ziel (1 pro Miner)

   Fortschritt & gefundene Blöcke erscheinen im Controller-Log.

3. **Ergebnisse prüfen**

   ```bash
   # Blockhöhe & Schwierigkeitsziel kontrollieren
   docker compose exec node bitcoin-cli -regtest getblockchaininfo

   # Neu geminte Blöcke ansehen
   docker compose exec node bitcoin-cli -regtest listtransactions "real-miners"
   ```

4. **Aufräumen**

   ```bash
   docker compose down -v
   ```

## Konfiguration

Alle relevanten Parameter können über die `.env` gesteuert werden:

| Variable            | Bedeutung                                      | Standard |
|---------------------|------------------------------------------------|----------|
| `RPC_USER`          | RPC-User für den Regtest-Node                  | `user`   |
| `RPC_PASS`          | RPC-Passwort                                   | `pass`   |
| `RPC_HOST`          | Hostname des Nodes (Compose-Service)           | `node`   |
| `RPC_PORT`          | RPC-Port                                       | `18443`  |
| `MINER_THREADS`     | Anzahl Miner (Threads)                         | `10`     |
| `TARGET_BLOCKS`     | Anzahl zu minender Blöcke in einem Testlauf    | `10`     |
| `COINBASE_LABEL`    | Wallet-Label für Miner-Adressen                | `miner`  |

## Sicherheit & Hinweise

- SegWit ist in dieser PoC deaktiviert (`-segwitheight=999999999`), um die
  Coinbase-Erzeugung zu vereinfachen.
- Die Wallet `real-miners` wird automatisch erzeugt und erhält für jeden Miner
  eine eigene Adresse (Legacy-Format). Alle Rewards landen damit im Pool der
  PoC-Testumgebung.
- Das Mining nutzt echtes Proof-of-Work. Die Schwierigkeit im Regtest
  (`0x207fffff`) ist sehr niedrig, sodass Blöcke innerhalb weniger Millisekunden
  gefunden werden.
- Die PoC verändert keine bestehenden Dateien oder Rollen der Hauptumgebung.

## Weitere Schritte

- Parameter anpassen (`MINER_THREADS`, `TARGET_BLOCKS`) für längere Runs.
- SegWit aktivieren und Coinbase-Anpassungen erweitern, falls vollständige
  Mainnet-Kompatibilität benötigt wird.
- Zusätzliche Observability einbauen (z. B. Prometheus-Exporter oder Log-Parser).

Viel Erfolg beim Experimentieren mit echtem Mining im Regtest!


