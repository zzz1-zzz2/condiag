#!/bin/bash
# D4-9 Step 3: Batch2 17 instances × 4 baselines official compare matrix.
# All 17 worktrees are prepped under /home/swelite/condiag/workspaces/<iid>/repo_base.
set -e
cd /home/swelite/condiag

ROOT=/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4
INST=$ROOT/instances.txt
OUT=$ROOT/runs
MAN=$ROOT/manifest.csv
LOG=$ROOT/run.log

# Wipe previous attempt so handlers don't reuse stale attempt_1/
rm -rf "$OUT"
mkdir -p "$OUT"

echo "=== D4-9 Step 3: Batch2 17×4 official compare matrix ===" | tee "$LOG"
echo "start: $(date -Iseconds)" | tee -a "$LOG"

for bl in base_miniswe feedback_retry broad_expansion condiag_packet_only; do
    echo "" | tee -a "$LOG"
    echo "=== $bl ===" | tee -a "$LOG"
    python3 -m experiments.baseline_runner --agent miniswe --baseline "$bl" \
        --instances "$INST" --out "$OUT" --mode smoke --manifest "$MAN" \
        2>&1 | tee -a "$LOG" | tail -5
done

echo "" | tee -a "$LOG"
echo "end: $(date -Iseconds)" | tee -a "$LOG"
