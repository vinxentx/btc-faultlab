# btc-faultlab — Ansible-Orchestrated Bitcoin Regtest Fault Lab

Lokal lauffähige Experimente mit Crash/Omission-Faults, Recovery-Modi und Metriken (TPS, Confirmation Latency, Availability, Stale-Rate, TTR).
Siehe `group_vars/all.yml` für Parameter; `playbooks/` für Orchestrierung; `files/` für Generator & Telemetrie; `analysis/` für Auswertung; `lrz/` für LRZ-Ready Hinweise.

## macOS

Für macOS wird eine vorhandene [Homebrew](https://brew.sh)-Installation vorausgesetzt. Nach der Installation kann es nötig sein, Docker Desktop manuell zu starten.
