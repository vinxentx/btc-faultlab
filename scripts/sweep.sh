#!/usr/bin/env bash
set -euo pipefail
MATRIX=(
  "crash_fraction=0.0 loss_pct=0 latency_ms=0 recovery_mode=fast"
  "crash_fraction=0.3 loss_pct=5 latency_ms=200 recovery_mode=cold"
)
for cfg in "${MATRIX[@]}"; do
  echo ">>> Running: $cfg"
  ansible-playbook -i inventory.ini playbooks/03_run_experiment.yml -e "$cfg"
  ansible-playbook -i inventory.ini playbooks/04_collect.yml
done
