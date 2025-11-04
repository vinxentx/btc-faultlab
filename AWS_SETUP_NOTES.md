# AWS Ubuntu Setup – Änderungen & Befehle

## 📦 Speicheranforderungen (WICHTIG!)

**Hinweis:** AWS EBS Volumes werden in **GiB** (Gibibyte) gemessen, nicht GB (Gigabyte).
- 1 GiB = 2³⁰ Bytes = 1.073.741.824 Bytes ≈ 1.074 GB
- 1 GB = 10⁹ Bytes = 1.000.000.000 Bytes

### Minimum-Speicher für verschiedene Szenarien:

| Nodes | Mindest-Speicher | Empfohlen | Für was? |
|-------|------------------|-----------|----------|
| **4-32** | **47 GiB (~50 GB)** | **93 GiB (~100 GB)** | Quick Tests, Development |
| **64** | **140 GiB (~150 GB)** | **186 GiB (~200 GB)** | Medium Tests, Validation |
| **128** | **186 GiB (~200 GB)** | **279-465 GiB (~300-500 GB)** | Production, Thesis Experiments |

### Speicher-Aufteilung (128 Nodes, ~186 GiB Minimum):

- **Docker Images & System:** ~9 GiB (~10 GB)
  - Bitcoin Core Image: ~1.4 GiB (~1.5 GB)
  - Docker System: ~5-7 GiB (~5-8 GB)
  - Ubuntu Base: ~2-3 GiB (~2-3 GB)

- **Container Volumes (128 Nodes):** ~47-75 GiB (~50-80 GB)
  - Jeder Node: ~400-600 MB Blockchain-Daten
  - 128 Nodes × 500 MB = ~60 GiB (~64 GB)
  - Docker Overhead: ~9-14 GiB (~10-15 GB)

- **Experiment-Ergebnisse:** ~5-9 GiB (~5-10 GB) pro Run
  - Logs: ~2-3 GiB (~2-3 GB) (128 Nodes × ~20 MB)
  - CSV-Dateien: ~100-500 MB
  - Plots: ~50-100 MB
  - Metadaten: ~10-50 MB

- **Mehrere Runs/Experimente:**
  - 10 Runs: +47-93 GiB (~50-100 GB)
  - 33 Experimente (99 Runs): +465-931 GiB (~500-1000 GB)

### Empfehlung für AWS EBS Volumes:

- **Quick Tests (4-32 Nodes):** **50-100 GiB** EBS Volume
- **Medium Tests (64 Nodes):** **200 GiB** EBS Volume
- **Production (128 Nodes, alle Experimente):** **500 GiB+ EBS Volume**

**Wichtig:** Bei 128 Nodes mit vielen Experimenten brauchst du deutlich mehr als 186 GiB!

### AWS EBS Volume Größen (typische Auswahl):

- `gp3`: 50 GiB, 100 GiB, 200 GiB, 500 GiB, 1000 GiB
- `gp2`: 50 GiB, 100 GiB, 200 GiB, 500 GiB, 1000 GiB
- Empfohlen: **gp3** (günstiger, besserer Performance)

### Speicher prüfen:

```bash
# Aktueller Speicher
df -h

# Docker-Speicher verbrauch
docker system df

# Größte Verzeichnisse finden
du -sh ~/btc-faultlab/* | sort -h
du -sh ~/.docker/* | sort -h
```

---

## 1. Grundinstallation & Python-Umgebung
```
# Pakete installieren und Docker vorbereiten
sudo apt update
sudo apt install -y docker.io docker-compose-v2 python3-pip python3-venv
sudo usermod -aG docker $USER
exit   # neu einloggen, damit Docker-Gruppe greift
ssh ubuntu@<AWS-IP>

# Projektverzeichnis
cd ~/btc-faultlab

# Virtuelle Umgebung (empfohlen)
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Pfade & docker compose für Linux korrigieren
```
# macOS-Pfade im Compose-File ersetzen
cp docker-compose.yml docker-compose.yml.backup
sed -i "s|/Users/vincenttietze/btc-faultlab|$HOME/btc-faultlab|g" docker-compose.yml
```

```
# Sicherstellen, dass docker compose V2 verwendet wird
sed -i 's|docker-compose -f "{{ output_compose }}" up -d|docker compose -f "{{ output_compose }}" up -d|' playbooks/02_deploy.yml
```

## 3. Ansible: Gruppenrechte auf Ubuntu anpassen
```
# Statt macOS-Gruppe "staff" die aktuelle USER-Gruppe verwenden (Shell, auf AWS-VM ausführen)
cd ~/btc-faultlab
sed -i 's/group: "staff"/group: "{{ lookup('\''env'\'', '\''USER'\'') }}"/g' roles/bitcoin/tasks/main.yml
sed -i 's/group: "staff"/group: "{{ lookup('\''env'\'', '\''USER'\'') }}"/g' playbooks/03_run_experiment.yml
```

> Alternative (idiotensicher) mit kleinem Python-Script – ersetzt beide Dateien sicher:
```
python3 - <<'PY'
from pathlib import Path
for path in [
    Path('roles/bitcoin/tasks/main.yml'),
    Path('playbooks/03_run_experiment.yml'),
]:
    text = path.read_text()
    old = 'group: "staff"'
    new = 'group: "{{ lookup(\'env\', \'USER\') }}"'
    if old not in text:
        print(f"⚠️  {path} enthielt keinen 'staff'-Eintrag")
        continue
    path.write_text(text.replace(old, new))
    print(f"✅ {path} aktualisiert")
PY
```

## 4. Bugfix: Letzten Run korrekt ermitteln
```
python3 - <<'PY'
from pathlib import Path
file_path = Path('run_experiments.py')
old = """    def get_latest_run_id(self):\n        if not self.results_dir.exists():\n            return None\n        \n        run_dirs = [d for d in self.results_dir.iterdir() \n                   if d.is_dir() and d.name.startswith(\"202\")]\n        \n        if not run_dirs:\n            return None\n        \n        return max(run_dirs, key=lambda x: x.name).name\n"""
new = """    def get_latest_run_id(self):\n        if not self.results_dir.exists():\n            return None\n\n        run_dirs = []\n        for entry in self.results_dir.iterdir():\n            if not entry.is_dir():\n                continue\n            if (entry / \"metadata.yml\").exists():\n                run_dirs.append(entry)\n\n        if not run_dirs:\n            return None\n\n        run_dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)\n        return run_dirs[0].name\n"""
text = file_path.read_text()
if old not in text:
    raise SystemExit('Block nicht gefunden')
file_path.write_text(text.replace(old, new))
PY
```

## 5. (Optional) Systemuhr synchronisieren
```
sudo timedatectl set-ntp true
sudo timedatectl status
```

## 6. Ergebnisse auf lokalen Rechner kopieren
```
# Private-Key-Rechte anpassen (einmalig, PowerShell als Admin)
icacls "C:\Users\Vincent.000\Desktop\BTC-Faultlab_Key.pem" /inheritance:r
icacls "C:\Users\Vincent.000\Desktop\BTC-Faultlab_Key.pem" /grant:r "Vincent.000:(R)"
icacls "C:\Users\Vincent.000\Desktop\BTC-Faultlab_Key.pem" /grant:r "SYSTEM:(F)"
icacls "C:\Users\Vincent.000\Desktop\BTC-Faultlab_Key.pem" /grant:r "Administratoren:(F)"

# Ergebnisordner & Zusammenfassung herunterladen (PowerShell)
scp -i "C:\Users\Vincent.000\Desktop\BTC-Faultlab_Key.pem" -r ubuntu@ec2-13-50-107-159.eu-north-1.compute.amazonaws.com:"~/btc-faultlab/results/baseline-20251104T123422Z" "C:\Users\Vincent.000\Desktop\btc-faultlab\results\"
scp -i "C:\Users\Vincent.000\Desktop\BTC-Faultlab_Key.pem" ubuntu@ec2-13-50-107-159.eu-north-1.compute.amazonaws.com:"~/btc-faultlab/results/tier_baseline_20251104_124749.json" "C:\Users\Vincent.000\Desktop\btc-faultlab\results\"
```