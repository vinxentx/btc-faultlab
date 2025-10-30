# Tier Experiments auf LRZ - Komplette Anleitung

## Übersicht

Diese Anleitung beschreibt, wie du die tier-basierten Experimente (33 Experimente, 99 Runs bei 3 Replikationen) auf der LRZ-Infrastruktur ausführst.

**Geschätzte Gesamtdauer:** ~58 Stunden (2.4 Tage)

---

## Option A: LRZ Compute Cloud / VM (EMPFOHLEN)

### Voraussetzungen

- Ubuntu 22.04+ VM mit:
  - Mindestens 32 GB RAM (empfohlen 64 GB für 128 Nodes)
  - 200+ GB Disk Space
  - 16+ CPU Cores
  - sudo-Rechte
- SSH-Zugang zur VM
- Python 3.8+ auf der VM

### 1. Initiales Setup (Einmalig)

#### 1.1 SSH zur VM verbinden

```bash
# Von deinem Mac
ssh ubuntu@<LRZ-VM-IP>
```

#### 1.2 Code deployen

```bash
# Auf der VM
cd ~
git clone <dein-repo-url> btc-faultlab
cd btc-faultlab

# Python Dependencies installieren
pip3 install -r requirements.txt

# Oder mit System-Paketen
sudo apt update
sudo apt install -y python3-pip python3-yaml ansible
```

#### 1.3 Docker installieren

```bash
# Bootstrap ausführen (einmalig!)
ansible-playbook -i inventories/lrz_local.ini playbooks/01_bootstrap.yml

# WICHTIG: Nach Docker-Installation neu einloggen!
exit
ssh ubuntu@<LRZ-VM-IP>

# Docker testen
docker ps

# HINWEIS: Bootstrap wird beim Experiment-Run automatisch übersprungen!
# Falls benötigt, kann es mit --with-bootstrap aktiviert werden
```

---

### 2. Experimente ausführen

#### 2.1 tmux Session starten (WICHTIG!)

```bash
# tmux ermöglicht, dass die Experimente weiterlaufen, 
# auch wenn die SSH-Verbindung abbricht

tmux new -s tier_experiments
```

**tmux Befehle:**
- `Ctrl+B, dann D` - Detach (Session läuft im Hintergrund)
- `tmux attach -t tier_experiments` - Wieder verbinden
- `tmux ls` - Aktive Sessions anzeigen

#### 2.2 Experimente Liste anschauen

```bash
cd ~/btc-faultlab
python3 run_tier_experiments.py --list
```

**Ausgabe:**
```
TIER BASELINE
  baseline - 128 nodes, no faults

TIER A (24 Experimente)
  tier_a_001 - tier_a_024
  Crash impact analysis

TIER B (4 Experimente)
  tier_b_001 - tier_b_004
  Stress environment

TIER C (4 Experimente)
  tier_c_001 - tier_c_004
  Block interval sensitivity

Total: 33 experiments
```

#### 2.3 Experimente starten

**Option 1: Baseline zum Testen**
```bash
# Nur Baseline mit 1 Replikation (zum Test, ~8 Min)
python3 run_tier_experiments.py --baseline --runs 1
```

**Option 2: Einzelnes Tier**
```bash
# Nur Tier A (24 Experimente, ~14 Stunden bei 3 Reps)
python3 run_tier_experiments.py --tier A --runs 3

# Oder Tier B (4 Experimente, ~2.3 Stunden)
python3 run_tier_experiments.py --tier B --runs 3

# Oder Tier C (4 Experimente, ~2.3 Stunden)
python3 run_tier_experiments.py --tier C --runs 3
```

**Option 3: Alle Experimente (EMPFOHLEN für finale Runs)**
```bash
# Alle 33 Experimente mit 3 Replikationen
# Dauer: ~58 Stunden
python3 run_tier_experiments.py --extended --runs 3
```

**Option 4: Einzelnes Experiment**
```bash
# Nur ein spezifisches Experiment
python3 run_tier_experiments.py --experiment tier_a_001 --runs 5
```

#### 2.4 Detach und warten

```bash
# In der tmux Session:
# Ctrl+B, dann D

# SSH kann jetzt getrennt werden!
exit
```

---

### 3. Fortschritt überwachen

#### 3.1 Wieder zur tmux Session verbinden

```bash
ssh ubuntu@<LRZ-VM-IP>
tmux attach -t tier_experiments
```

#### 3.2 Checkpoint-Datei prüfen

```bash
# In einer neuen Shell
ssh ubuntu@<LRZ-VM-IP>
cat ~/btc-faultlab/results/tier_progress_checkpoint.json

# Zeigt:
# - completed_runs: Anzahl erfolgreich
# - failed_runs: Fehlgeschlagen
# - timestamp: Letztes Update
```

#### 3.3 Log-Dateien live anschauen

```bash
# Neueste Run-ID finden
ls -lt ~/btc-faultlab/results/ | head

# Events Log
tail -f ~/btc-faultlab/results/<RUN_ID>/events.log

# Node Logs
tail -f ~/btc-faultlab/results/<RUN_ID>/node01.log
```

---

### 4. Ergebnisse abholen

Nach Abschluss aller Experimente:

#### 4.1 Vom Mac: Ergebnisse herunterladen

```bash
# Auf dem Mac
cd /Users/vincenttietze/btc-faultlab

# Alle Ergebnisse syncen
rsync -avz --progress ubuntu@<LRZ-VM-IP>:~/btc-faultlab/results/ ./results/

# Nur bestimmte Runs
rsync -avz ubuntu@<LRZ-VM-IP>:~/btc-faultlab/results/tier_full_suite_*.json ./results/
```

#### 4.2 Ergebnisse prüfen

```bash
# Auf dem Mac
cd /Users/vincenttietze/btc-faultlab

# Summary-Dateien
ls -lh results/tier_*.json

# Einzelne Run-Verzeichnisse
ls -1 results/ | grep "^202"

# Checkpoint
cat results/tier_progress_checkpoint.json
```

---

### 5. Analyse

```bash
# Auf dem Mac
cd /Users/vincenttietze/btc-faultlab

# Analyse-Skripte ausführen
python3 analysis/thesis_fault_analysis.py

# Metriken berechnen
python3 analysis/metrics.py --run-dir results/<RUN_ID>

# Vergleiche erstellen
python3 analysis/compare_experiments.py
```

---

## Troubleshooting

### Experiment hängt / ist abgestürzt

```bash
# SSH zur VM
ssh ubuntu@<LRZ-VM-IP>

# tmux Session prüfen
tmux attach -t tier_experiments

# Falls alles steht: Cleanup
docker ps -a | grep node
docker stop $(docker ps -a --filter 'name=node' --format '{{.Names}}')
docker rm $(docker ps -a --filter 'name=node' --format '{{.Names}}')

# Checkpoint prüfen welche Runs fertig sind
cat ~/btc-faultlab/results/tier_progress_checkpoint.json

# Fehlende Experimente einzeln nachführen
python3 run_tier_experiments.py --experiment tier_a_015 --runs 3
```

### Disk Space voll

```bash
# Auf der VM
df -h

# Alte Docker Images/Volumes löschen
docker system prune -af --volumes

# Alte Results archivieren
tar -czf old_results.tar.gz results/202*
rm -rf results/202*
```

### RAM/CPU zu hoch

```bash
# Node Count reduzieren für Tests
# In group_vars/all.yml:
# node_count: 32  # Statt 128

# Oder in tier_experiments.json einzeln anpassen
```

### Recovery Mode funktioniert nicht

```bash
# Prüfen ob bitcoind im Container läuft
docker exec node01 ps aux | grep bitcoin

# Logs prüfen
docker logs node01 --tail 100

# Bitcoin-CLI testen
docker exec node01 bitcoin-cli -regtest getblockchaininfo
```

---

## Experiment-Parameter

### Was wird getestet?

**Tier A (24 Runs):** Crash-Fraction (10%, 25%, 50%), Crash-Pattern (simultaneous/sequential), Downtime (20s/60s), Recovery (full/fast)

**Tier B (4 Runs):** Stress-Test mit hoher TX-Rate (500 tps), hoher Latenz (200ms), Packet Loss (10%)

**Tier C (4 Runs):** Block-Intervall-Sensitivität (10s vs 60s)

### Implementierte Features

✅ **Replications:** Jedes Experiment 3× für statistische Signifikanz  
✅ **Retries:** 2 Wiederholungen bei Fehlschlag  
✅ **Checkpoints:** Fortschritt wird nach jedem Run gespeichert  
✅ **jitter_ms:** Latenz-Variabilität (z.B. 100ms ± 5ms)  
✅ **recovery_mode:** full_resync (mit -reindex) vs fast_resync  
✅ **reindex_on_restart:** Automatisch basierend auf recovery_mode  

### Metadaten (für Analyse, nicht implementiert)

- `block_interval_target`: Ziel-Block-Intervall
- `block_size_limit`: Maximale Blockgröße
- `confirmations_for_latency_metric`: Anzahl Confirmations für Metriken

---

## Zeitplanung

| Phase | Experimente | Runs (3 Reps) | Dauer |
|-------|-------------|---------------|--------|
| Baseline | 1 | 3 | ~0.3h |
| Tier A | 24 | 72 | ~42h |
| Tier B | 4 | 12 | ~7h |
| Tier C | 4 | 12 | ~7h |
| **Total** | **33** | **99** | **~58h** |

**Pro Run:** ~35 Minuten (3 min warmup + 5 min observe + 1 min cooldown + overhead)

---

## Batch-Empfehlung

Für beste Ergebnisse:

```bash
# Freitag Abend:
python3 run_tier_experiments.py --baseline --runs 3
python3 run_tier_experiments.py --tier C --runs 3  # Kurz, testet Setup

# Samstag:
python3 run_tier_experiments.py --tier A --runs 3  # Lang, 42 Stunden

# Montag Abend:
python3 run_tier_experiments.py --tier B --runs 3  # Stress-Test
```

Oder alles in einem Durchgang:
```bash
# Freitag Abend - läuft über das Wochenende
nohup python3 run_tier_experiments.py --extended --runs 3 > experiment.log 2>&1 &
```

---

## Support

Bei Problemen:
1. Checkpoint-Datei prüfen (`tier_progress_checkpoint.json`)
2. Events-Log prüfen (`results/<RUN_ID>/events.log`)
3. Docker-Container-Status (`docker ps -a`)
4. tmux Session wiederverbinden

**Wichtig:** Retries und Checkpoints sorgen dafür, dass einzelne Fehler das Gesamtergebnis nicht beeinträchtigen!

