#!/usr/bin/env bash
# P0-1 smoke: run mini-SWE on astropy-13398 to verify the run_args DNS override
# actually flows through mini-swe-agent's docker invocation.
# Designed to run detached (nohup). Single instance only — fast fail/success signal.
set -uo pipefail

source ~/condiag/scripts/env.sh

STAMP=$(date +%Y%m%d_%H%M%S)
OUT=/mnt/d/condiag-artifacts/runs/p0_miniswe_astropy_${STAMP}
mkdir -p "$OUT"

echo "=== smoke start: $(date) ===" > "$OUT/smoke.log"
echo "OUTDIR=$OUT" >> "$OUT/smoke.log"
echo "instance=astropy__astropy-13398" >> "$OUT/smoke.log"
grep -E 'run_args|model_name' ~/condiag/ContextBench/agent-frameworks/mini-swe-agent/multi-poly-pro-verified/configs/swebench_following_context.yaml >> "$OUT/smoke.log" 2>&1

cd ~/condiag/ContextBench
python -m contextbench.run \
  --agent miniswe \
  --bench Verified \
  --instances "astropy__astropy-13398" \
  --output "$OUT" \
  --rerun \
  --timeout 1800 >> "$OUT/smoke.log" 2>&1
RC=$?

echo >> "$OUT/smoke.log"
echo "=== smoke end: $(date) rc=$RC ===" >> "$OUT/smoke.log"
echo "OUTDIR=$OUT" > /tmp/p0_miniswe_last_outdir
