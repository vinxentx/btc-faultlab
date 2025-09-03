#!/bin/bash
#SBATCH --job-name=btc-faultlab
#SBATCH --output=%x-%j.out
#SBATCH --time=00:30:00
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=8G
#SBATCH --partition=general

set -euo pipefail
module load apptainer || module load singularity

RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
WORKDIR=$PWD
RESULTS=$WORKDIR/results/$RUN_ID
mkdir -p "$RESULTS/plots"

CRASH_FRACTION=${CRASH_FRACTION:-0.30}
LOSS_PCT=${LOSS_PCT:-5}
LAT_MS=${LAT_MS:-200}
RECOVERY_MODE=${RECOVERY_MODE:-cold}
NODE_COUNT=${NODE_COUNT:-8}
TX_RATE=${TX_RATE:-10}

IMG="docker://ruimarinho/bitcoin-core:27.0"

# Hinweis: Docker ist meist verboten; Apptainer funktioniert.
# Multi-Instance bitcoind innerhalb eines Nodes ist möglich; NetEm ggf. nicht.
echo "Run ID: $RUN_ID"
echo "Params: NODE_COUNT=$NODE_COUNT CRASH_FRACTION=$CRASH_FRACTION LOSS=$LOSS_PCT LAT_MS=$LAT_MS RECOVERY=$RECOVERY_MODE TX=$TX_RATE"
# Implementierung vor Ort an LRZ-Bedingungen anpassen (Port-/Netz-Namespace, Caps).
