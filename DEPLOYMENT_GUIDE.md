# Deployment Guide - Bitcoin Fault Lab

## 🎯 Node Count Recommendations

### Getestete Konfigurationen:

| Nodes | Status | Use Case | Dauer | Ressourcen |
|-------|--------|----------|-------|------------|
| **4** | ✅ Perfekt | Quick Tests, Development | ~2 Min | Minimal |
| **64** | ✅ Funktioniert | Lokale Tests, Validation | ~7 Min | Mittel |
| **128** | ⚠️ Nur LRZ | Production, Thesis Experiments | ~10 Min | Hoch |

---

## 💻 Lokale Entwicklung (Mac)

### Quick Test (4 Nodes) - Empfohlen!
```bash
python3 run_experiments.py --config quick_test_config.json
```
**Perfekt für:**
- Feature-Tests
- Bug-Fixes validieren
- Schnelles Feedback

### Medium Test (64 Nodes)
```bash
python3 run_experiments.py --config test_64_nodes.json
```
**Perfekt für:**
- Realistische Tests
- Pre-Production Validation
- Lokale Baseline-Tests

---

## 🚀 LRZ Production (128 Nodes)

### Setup auf LRZ-VM:

```bash
# 1. SSH + tmux
ssh ubuntu@<LRZ-IP>
tmux new -s tier_experiments

# 2. Bootstrap (einmalig!)
cd btc-faultlab
ansible-playbook -i inventories/lrz_local.ini playbooks/01_bootstrap.yml

# WICHTIG: Neu einloggen für Docker-Gruppe!
exit
ssh ubuntu@<LRZ-IP>
tmux attach -t tier_experiments

# 3. Liste anschauen
python3 run_tier_experiments.py --list

# 4. Baseline Test (3 Runs, ~30 Min)
python3 run_tier_experiments.py --baseline --runs 3

# 5. Einzelnes Tier (z.B. Tier A, 24 Experimente, ~42h)
python3 run_tier_experiments.py --tier A --runs 3

# 6. Alle Experimente (33 Experimente, 99 Runs, ~58h)
python3 run_tier_experiments.py --extended --runs 3

# 7. Detach: Ctrl+B, dann D
```

---

## 📊 Tier Experiments (LRZ-VM)

### Struktur:
```
Baseline:  1 Experiment  (keine Fehler)
Tier A:   24 Experimente (Crash-Impact)
Tier B:    4 Experimente (Stress-Test)
Tier C:    4 Experimente (Block-Intervall)
───────────────────────────────────────
Total:    33 Experimente

Bei 3 Replikationen: 99 Runs, ~58 Stunden
```

### Quick Commands:

```bash
# Nur Baseline
python3 run_tier_experiments.py --baseline --runs 3

# Spezifisches Tier
python3 run_tier_experiments.py --tier A --runs 3
python3 run_tier_experiments.py --tier B --runs 3
python3 run_tier_experiments.py --tier C --runs 3

# Einzelnes Experiment
python3 run_tier_experiments.py --experiment tier_a_001 --runs 5

# Alles
python3 run_tier_experiments.py --extended --runs 3
```

---

## 🔧 Troubleshooting

### Mac: "Experiment failed after XXX seconds"

**Ursache:** Zu viele Nodes für lokale Ressourcen

**Lösung:**
```bash
# Verwende weniger Nodes:
python3 run_experiments.py --config quick_test_config.json  # 4 Nodes
python3 run_experiments.py --config test_64_nodes.json      # 64 Nodes
```

### LRZ: Container Health Checks timeout

**Ursache:** Zu wenig RAM/CPU für 128 Nodes

**Lösung:**
```bash
# VM-Anforderungen prüfen:
# - Mindestens 32 GB RAM (empfohlen 64 GB)
# - 16+ CPU Cores
# - 200+ GB Disk Space
```

### Collection failed

**Ursache:** Container wurden vor Collection gestoppt

**Lösung:**
- Collection-Fehler sind jetzt graceful gehandhabt
- Leere confirmations.csv wird erstellt
- Metrics werden trotzdem berechnet

---

## 📈 Monitoring

### Fortschritt prüfen:

```bash
# Checkpoint-Datei
cat results/tier_progress_checkpoint.json

# Live Events
tail -f results/<RUN_ID>/events.log

# Container Status
docker ps | grep node | wc -l
```

### Wieder verbinden:

```bash
ssh ubuntu@<LRZ-IP>
tmux attach -t tier_experiments
```

---

## 📥 Ergebnisse abholen

### Von LRZ-VM zum Mac:

```bash
# Auf dem Mac:
cd /Users/vincenttietze/btc-faultlab

# Alle Ergebnisse
rsync -avz --progress ubuntu@<LRZ-IP>:~/btc-faultlab/results/ ./results/

# Nur Summaries
rsync -avz ubuntu@<LRZ-IP>:~/btc-faultlab/results/tier_*.json ./results/

# Nur neueste Runs
rsync -avz ubuntu@<LRZ-IP>:~/btc-faultlab/results/2025* ./results/
```

---

## ✅ Erfolgreiche Tests

### Quick Test (4 Nodes):
```bash
✅ Run: 20251026T115226Z
   - 25 Transaktionen gesendet
   - Alle Logs gesammelt
   - Plots generiert
   - Dauer: 130.5s (~2 Min)
```

### Medium Test (64 Nodes):
```bash
✅ Run: 20251026T121833Z
   - 64 Bitcoin Nodes
   - 25 Transaktionen gesendet
   - Alle Logs gesammelt
   - Plots generiert
   - Dauer: 414.0s (~7 Min)
```

---

## 🎯 Empfohlener Workflow

### Phase 1: Lokale Entwicklung
```bash
# Quick Tests mit 4 Nodes
python3 run_experiments.py --config quick_test_config.json
```

### Phase 2: Lokale Validation
```bash
# Medium Tests mit 64 Nodes
python3 run_experiments.py --config test_64_nodes.json
```

### Phase 3: LRZ Production
```bash
# SSH zur LRZ-VM
ssh ubuntu@<LRZ-IP>
tmux new -s tier_exp

# Alle Tier-Experimente (128 Nodes)
cd btc-faultlab
python3 run_tier_experiments.py --extended --runs 3

# Detach und warten
# Ctrl+B, dann D
```

### Phase 4: Analyse
```bash
# Ergebnisse holen
rsync -avz ubuntu@<LRZ-IP>:~/btc-faultlab/results/ ./results/

# Analysen durchführen
python3 run_thesis_analysis.py
```

---

## 💡 Best Practices

1. **Immer mit Quick Test (4 Nodes) starten**
   - Schnelles Feedback
   - Wenig Ressourcen
   - Ideal für Development

2. **Medium Test (64 Nodes) für Validation**
   - Realistischer
   - Noch lokal machbar
   - Gut für Pre-Production

3. **Production nur auf LRZ-VM (128 Nodes)**
   - Volle Experimente
   - Lange Laufzeit (58h)
   - Robuste Infrastruktur

4. **Immer tmux auf LRZ verwenden**
   - SSH-Disconnect überlebt
   - Wiederverbinden jederzeit möglich
   - Logs bleiben erhalten

5. **Checkpoints nutzen**
   - Nach jedem Run gespeichert
   - Bei Abbruch weitermachen
   - Fehlgeschlagene Runs identifizieren

---

## 🚀 Ready for Production!

Alle Systeme getestet und funktionsfähig:
- ✅ Bootstrap überspringen
- ✅ Transaction Generation
- ✅ Data Collection
- ✅ Metrics & Plots
- ✅ 4, 64, 128 Nodes getestet
- ✅ Graceful Error Handling
- ✅ Checkpoints & Retries

**Viel Erfolg mit deinen Thesis-Experimenten! 🎓**


