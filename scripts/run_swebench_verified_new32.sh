#!/usr/bin/env bash
# Run SWE-bench official eval on 32 NEW Verified instances.
# Uses deduped predictions built from traj.json info.submission.
# Writes report to D:/condiag-artifacts/condiag/v0/eval_predictions/swebench_verified_new32/
set -uo pipefail

PREDS="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/deduped/predictions_Verified_NEW32.jsonl"
OUT_DIR="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_verified_new32"
RUN_ID="miniswe_verified_new32"
DATASET="princeton-nlp/SWE-Bench_Verified"
MAX_WORKERS="${MAX_WORKERS:-6}"

echo "[Eval Verified NEW32] Run id: $RUN_ID"
echo "  dataset      = $DATASET"
echo "  predictions  = $PREDS"
echo "  out_dir      = $OUT_DIR"
echo "  max_workers  = $MAX_WORKERS"
echo "  start: $(date)"

mkdir -p "$OUT_DIR"

cd /home/swelite/condiag

python3 -m swebench.harness.run_evaluation \
    --dataset_name "$DATASET" \
    --split test \
    --predictions_path "$PREDS" \
    --max_workers "$MAX_WORKERS" \
    --run_id "$RUN_ID" \
    --report_dir "$OUT_DIR" \
    --open_file_limit 8192 \
    --timeout 1800 \
    --cache_level env \
    2>&1 | tee "$OUT_DIR/run_eval.log"

echo "[Eval Verified NEW32] done: $(date)"
echo "Report: $OUT_DIR/$RUN_ID.json"