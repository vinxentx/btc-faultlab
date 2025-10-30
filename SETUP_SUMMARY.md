# Setup Summary - Alle Probleme gelöst! ✅

## 🎯 Implementierte Lösungen

### 1. Bootstrap-Problem ✅
**Problem:** Bootstrap wurde bei jedem Run ausgeführt  
**Lösung:** 
- `skip_bootstrap=True` als Standard
- Optional `--with-bootstrap` Flag
- Bootstrap wird einmalig manuell ausgeführt

**Verwendung:**
```bash
# Einmalig vor allen Experimenten:
ansible-playbook -i inventories/lrz_local.ini playbooks/01_bootstrap.yml

# Dann Experimente ohne Bootstrap:
python3 run_tier_experiments.py --extended --runs 3
```

### 2. Ansible Permission Problem (macOS) ✅
**Problem:** `Operation not permitted: '/Users/.../.ansible/tmp/...'`  
**Lösung:**
- `ansible.cfg` erweitert mit `local_tmp` und `remote_tmp`
- Verwendet `/tmp/ansible-*` statt `~/.ansible/tmp/`
- Verzeichnisse werden automatisch erstellt

**Änderungen in ansible.cfg:**
```ini
local_tmp = /tmp/ansible-local-$USER
remote_tmp = /tmp/ansible-remote-$USER
```

### 3. Docker Cleanup Fehler ✅
**Problem:** Cleanup schlug fehl wenn Docker nicht läuft  
**Lösung:**
- Verbesserte Fehlerbehandlung
- Prüft ob Docker läuft vor Cleanup
- Graceful degradation wenn keine Container vorhanden

**Neue Logik:**
- Check `docker info` zuerst
- Liste Container einzeln auf
- Stoppe/Entferne nur existierende Container

### 4. Alle Parameter implementiert ✅
**Implementiert:**
- ✅ `jitter_ms` in netem role (Latenz-Variabilität)
- ✅ `recovery_mode` in experiment role (full_resync vs fast_resync)
- ✅ `reindex_on_restart` in allen 33 Experimenten

**Bereinigt:**
- ✅ `crashed_node_ids` entfernt (wird automatisch generiert)

### 5. Vollständige Tier-Experiment-Infrastruktur ✅
**Erstellt:**
- ✅ `run_tier_experiments.py` (369 Zeilen)
- ✅ `tier_experiments.json` bereinigt (33 Experimente)
- ✅ `inventories/lrz_local.ini`
- ✅ `lrz/TIER_EXPERIMENTS_GUIDE.md` (komplette Anleitung)

## 📊 Tier Experiments

### Struktur:
- **Baseline:** 1 Experiment (keine Fehler)
- **Tier A:** 24 Experimente (Crash-Impact-Analyse)
- **Tier B:** 4 Experimente (Stress-Umgebung)
- **Tier C:** 4 Experimente (Block-Intervall-Sensitivität)
- **Total:** 33 Experimente

### Bei 3 Replikationen:
- **99 Runs** total
- **~58 Stunden** Gesamtlaufzeit
- **Checkpoints** nach jedem Run
- **Retries** (2×) bei Fehlern

## 🚀 Verwendung

### Auf LRZ-VM (EMPFOHLEN):

```bash
# 1. SSH + tmux
ssh ubuntu@<LRZ-IP>
tmux new -s tier_experiments

# 2. Bootstrap (einmalig!)
cd btc-faultlab
ansible-playbook -i inventories/lrz_local.ini playbooks/01_bootstrap.yml

# 3. Neu einloggen (für Docker-Gruppe)
exit
ssh ubuntu@<LRZ-IP>
tmux attach -t tier_experiments

# 4. Liste anschauen
python3 run_tier_experiments.py --list

# 5. Baseline testen (1 Run, ~8 Min)
python3 run_tier_experiments.py --baseline --runs 1

# 6. Alle Experimente (99 Runs, ~58h)
python3 run_tier_experiments.py --extended --runs 3

# 7. Detach: Ctrl+B, dann D
```

### Einzelne Tiers:

```bash
# Nur Tier A (24 Experimente, ~42h bei 3 Reps)
python3 run_tier_experiments.py --tier A --runs 3

# Nur Tier B (4 Experimente, ~7h bei 3 Reps)
python3 run_tier_experiments.py --tier B --runs 3

# Nur Tier C (4 Experimente, ~7h bei 3 Reps)
python3 run_tier_experiments.py --tier C --runs 3
```

### Einzelnes Experiment:

```bash
# Spezifisches Experiment mit 5 Replikationen
python3 run_tier_experiments.py --experiment tier_a_001 --runs 5
```

## 📈 Monitoring

### Fortschritt prüfen:

```bash
# Checkpoint-Datei
cat results/tier_progress_checkpoint.json

# Events Log
tail -f results/<RUN_ID>/events.log

# Node Logs
tail -f results/<RUN_ID>/node01.log
```

### Wieder verbinden:

```bash
ssh ubuntu@<LRZ-IP>
tmux attach -t tier_experiments
```

## 📥 Ergebnisse abholen

```bash
# Auf dem Mac:
cd /Users/vincenttietze/btc-faultlab

# Alle Ergebnisse syncen
rsync -avz --progress ubuntu@<LRZ-IP>:~/btc-faultlab/results/ ./results/

# Nur Summaries
rsync -avz ubuntu@<LRZ-IP>:~/btc-faultlab/results/tier_*.json ./results/
```

## 🎯 Features

### Implementierte Parameter:
- ✅ `node_count`: 128 Nodes
- ✅ `jitter_ms`: Latenz-Variabilität (z.B. 100ms ± 5ms)
- ✅ `recovery_mode`: full_resync (mit -reindex) oder fast_resync
- ✅ `reindex_on_restart`: Automatisch basierend auf recovery_mode
- ✅ `crash_mode`: burst (simultaneous) oder staggered (sequential)

### Robustheit:
- ✅ **Replications:** 3 Runs pro Experiment für statistische Signifikanz
- ✅ **Retries:** 2 Versuche bei Fehlschlag
- ✅ **Checkpoints:** Fortschritt wird nach jedem Run gespeichert
- ✅ **Cleanup:** Automatisch bei Fehlern
- ✅ **Skip Bootstrap:** Bootstrap nur einmalig notwendig

## ✅ Bereit für Production!

Alle Probleme sind gelöst und das System ist ready für:

```bash
python3 run_tier_experiments.py --extended --runs 3
```

**Geschätzte Laufzeit:** ~58 Stunden für 99 Runs (33 Experimente × 3 Replikationen)

**Auf LRZ-VM läuft alles stabil mit tmux! 🚀**
