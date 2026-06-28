#!/usr/bin/env bash
# Run mini-SWE on Pilot50 instances.
# Usage:
#   ./run_miniswe_pilot50.sh sanity   # 1 instance (django-12858)
#   ./run_miniswe_pilot50.sh batch1   # 10 instances
#   ./run_miniswe_pilot50.sh batch2   # 20 instances
#   ./run_miniswe_pilot50.sh batch3   # 20 instances
set -uo pipefail
source ~/condiag/scripts/env.sh

MODE="${1:-sanity}"
STAMP=$(date +%Y%m%d_%H%M%S)
ROOT="/mnt/d/condiag-artifacts/runs/pilot50_${MODE}_${STAMP}"
mkdir -p "$ROOT"

case "$MODE" in
  sanity)
    INSTANCES="django__django-12858"
    TIMEOUT=1800  # 30 min for sanity
    ;;
  batch1)
    INSTANCES=$(tr '\n' ',' < /mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_batch1.txt | sed 's/,$//')
    TIMEOUT=2700  # 45 min per instance, 10 instances
    ;;
  batch2|batch3)
    INSTANCES=$(tr '\n' ',' < /mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_${MODE}.txt | sed 's/,$//')
    TIMEOUT=3600  # 60 min per instance, 20 instances
    ;;
  *)
    echo "Unknown mode: $MODE"
    exit 1
    ;;
esac

echo "=== run_miniswe_pilot50.sh ==="
echo "mode: $MODE"
echo "instances: $INSTANCES"
echo "output: $ROOT"
echo "timeout per instance: ${TIMEOUT}s"
echo "start: $(date)"
echo ""

cd ~/condiag/ContextBench || exit 1

# Sanity: docker daemon + first image present
docker info >/dev/null 2>&1 || { echo "docker DOWN"; exit 1; }
echo "docker: OK"

python -m contextbench.run \
  --agent miniswe \
  --bench Verified \
  --instances "$INSTANCES" \
  --output "$ROOT" \
  --rerun \
  --timeout "$TIMEOUT" 2>&1 | tee "$ROOT/run.log"

echo ""
echo "=== done at $(date) ==="
echo "artifacts in: $ROOT"
