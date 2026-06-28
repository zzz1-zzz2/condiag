#!/usr/bin/env bash
# Stage 1: run mini-SWE on the 4 Verified Pilot instances whose images are cached.
# (astropy-13398 already done in p0 smoke — skipped here to save API budget.)
# Output goes into a single batched directory for downstream COMPARISON export.
set -uo pipefail

source ~/condiag/scripts/env.sh

STAMP=$(date +%Y%m%d_%H%M%S)
ROOT=/mnt/d/condiag-artifacts/runs/stage1_miniswe_verified_${STAMP}
mkdir -p "$ROOT"

INSTANCES="django__django-11400,django__django-13195,sympy__sympy-13877,sympy__sympy-16597"

{
  echo "=== stage 1 mini-SWE start: $(date) ==="
  echo "instances: $INSTANCES"
  echo "model: deepseek-v4-pro"
  echo "yaml run_args: [--rm, --dns 223.5.5.5, --dns 1.1.1.1]"
} > "$ROOT/batch.log"

cd ~/condiag/ContextBench
python -m contextbench.run \
  --agent miniswe \
  --bench Verified \
  --instances "$INSTANCES" \
  --output "$ROOT" \
  --timeout 1800 >> "$ROOT/batch.log" 2>&1
RC=$?

echo >> "$ROOT/batch.log"
echo "=== stage 1 mini-SWE end: $(date) rc=$RC ===" >> "$ROOT/batch.log"
echo "ROOTDIR=$ROOT" > /tmp/stage1_miniswe_last_root
