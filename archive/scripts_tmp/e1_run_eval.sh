#!/bin/bash
# E1 official SWE-bench eval — one baseline at a time
# Usage: ./e1_run_eval.sh <baseline_name>
set -e

BL="$1"
OUT=/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_official_eval_django12125
PRED="$OUT/predictions/predictions.$BL.jsonl"
LOGS="$OUT/logs"
REPORTS="$OUT/reports"

mkdir -p "$LOGS" "$REPORTS"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

echo "=== E1 eval: $BL ==="
date -u +"start: %Y-%m-%dT%H:%M:%SZ"
echo "  pred:    $PRED"
echo "  log:     $LOGS/$BL.eval.log"
echo "  reports: $REPORTS"

cd ~/condiag
python3 -m swebench.harness.run_evaluation \
    --dataset_name princeton-nlp/SWE-bench_Verified \
    --split test \
    --instance_ids django__django-12125 \
    --predictions_path "$PRED" \
    --run_id "$BL" \
    --report_dir "$REPORTS" \
    --max_workers 1 \
    --cache_level instance \
    --force_rebuild false 2>&1 | tee "$LOGS/$BL.eval.log"

date -u +"end:   %Y-%m-%dT%H:%M:%SZ"
echo "=== exit ${PIPESTATUS[0]} ==="
