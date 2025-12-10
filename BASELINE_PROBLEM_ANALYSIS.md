# Baseline-Problem Analyse - Zusammenfassung

## 🎯 Hauptproblem: Ab wann traten die Probleme auf?

### **Kritischer Zeitpunkt: 9. Dezember 2025**

Die beiden problematischen Runs vom **9. Dezember 2025** zeigen unterschiedliche, aber schwerwiegende Probleme:

---

## 📊 Run `baseline-20251209T173058Z` (17:30:58)

### Probleme:
1. **Nur 10 Miner statt 128!**
   - Log zeigt: `Config geladen: 10 Miner`
   - Sollte: 128 Miner (100% von 128 Nodes)
   - Tatsächlich: 7.8% der Nodes

2. **Exponentialverteilung aktiviert**
   - Log zeigt: `mit Exponentialverteilung`
   - Sollte: `festes Intervall` (baseline)
   - Führt zu extrem kurzen Intervallen (0.16s - 0.51s)

3. **Folgen:**
   - 4 valid-forks
   - Availability: 98.9% (noch akzeptabel)
   - Block Propagation Mean: 11.2s (zu hoch)

### Root Cause:
- **Konfigurationsfehler:** `mining_percentage` wurde wahrscheinlich auf `0.08` (8%) gesetzt statt `1.0` (100%)
- **Variance aktiviert:** `block_scheduler_use_variance` war `true` statt `false`

---

## 📊 Run `baseline-20251209T182942Z` (18:29:42)

### Probleme:
1. **Korrekte Anzahl Miner (128)**
   - Log zeigt: `Config geladen: 128 Miner`
   - ✅ Korrekt konfiguriert

2. **Festes Intervall (korrekt)**
   - Log zeigt: `festes Intervall`
   - ✅ Korrekt für Baseline

3. **Aber massive Performance-Probleme:**
   - **18 valid-forks** (extrem hoch!)
   - **Max branchlen: 45** (45 Blöcke divergiert!)
   - **Availability: 88.7%** (sollte >99% sein)
   - **Block Propagation Mean: 23.5s** (sollte <5s sein)
   - **Block Propagation Max: 159s** (katastrophal!)
   - **Nur 35/113 Blöcke** erreichten alle 128 Nodes

### Root Cause:
- **Ressourcenüberlastung** auf AWS EC2-Instanz
- 128 Bitcoin Core Container überlasten:
  - CPU (Blockchain-Validierung)
  - RAM (UTXO-Set pro Node)
  - Docker-Netzwerk (P2P-Traffic zwischen 128 Containern)

---

## 📈 Chronologische Entwicklung

### Vorherige Runs (bis 8. Dezember):
- **baseline-20251111T111836Z** (AWS, 128 Nodes): ✅ **0 Forks**, 1 active chain-tip
- **baseline-20251111T181910Z** (AWS, 128 Nodes): ✅ **0 Forks**, 1 active chain-tip
- **baseline-20251208T121724Z** (AWS, 128 Nodes): ✅ **0 Forks**, 1 active chain-tip

### Erste Anzeichen (9. Dezember, frühe Runs):
- **baseline-20251209T133830Z** (13:38): 
  - 64 Miner, **variance ON** (falsch!), 12 forks
  - Problem: Variance sollte für Baseline aus sein

- **baseline-20251209T143401Z** (14:34):
  - 64 Miner, variance OFF, 9 forks, branchlen 32
  - Problem: Immer noch viele Forks

- **baseline-20251209T152507Z** (15:25):
  - 64 Miner, variance OFF, 9 forks, branchlen 32
  - Problem: Immer noch viele Forks

### Kritische Runs (9. Dezember, späte Runs):
- **baseline-20251209T173058Z** (17:30): ⚠️ **Nur 10/128 Miner + Variance**
- **baseline-20251209T182942Z** (18:29): ⚠️ **Ressourcenüberlastung**

---

## 🔍 Historische Probleme (vor 9. Dezember)

### Ab 11. November 2025:
- **Nur 5 Miner** statt 64/128 in vielen Runs
- Beispiel: `baseline-20251111T144635Z` - 5/64 Miner
- Problem: `mining_percentage` war auf `0.08` (8%) gesetzt

### Ab 1. Dezember 2025:
- **Variance aktiviert** in Baseline-Runs
- Beispiel: `baseline-20251201T162537Z` - variance ON
- Problem: Sollte für Baseline aus sein

---

## ✅ Empfehlungen

### 1. Sofortige Fixes:
- ✅ Prüfe `group_vars/all.yml`: `mining_percentage: 1.0`
- ✅ Prüfe `group_vars/all.yml`: `block_scheduler_use_variance: false`
- ✅ Prüfe ob `temp_override.yml` oder andere Overrides diese Werte überschreiben

### 2. AWS-Instanz:
- **Prüfe Instanz-Größe** - für 128 Nodes benötigt:
  - **32+ vCPUs**
  - **64+ GB RAM**
  - **Schnelle Netzwerk-Performance**
- **Alternative:** Reduziere auf 64 Nodes für stabilere Experimente

### 3. Validierung:
- **Vor jedem Run prüfen:**
  - Block-Scheduler Log: Miner-Anzahl korrekt?
  - Block-Scheduler Log: Variance für Baseline aus?
  - Metriken nach Run: Forks < 5, Availability > 99%

---

## 📝 Zusammenfassung

**Probleme begannen am 9. Dezember 2025** mit zwei unterschiedlichen Ursachen:

1. **17:30 Run:** Konfigurationsfehler (10 Miner + Variance)
2. **18:29 Run:** Ressourcenüberlastung (128 Miner, aber zu viele Container)

**Historisch:** Bereits ab 11. November gab es sporadische Probleme mit zu wenigen Minern (5 statt 64/128).
