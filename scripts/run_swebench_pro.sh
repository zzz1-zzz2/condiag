#!/usr/bin/env bash
# Run SWE-bench eval on Pro (16) instances.
set -uo pipefail

PREDS="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/deduped/predictions_Pro.jsonl"
OUT_DIR="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_pro"
RUN_ID="miniswe_pro"
DATASET="ScaleAI/SWE-bench_Pro"
MAX_WORKERS=6

echo "[Eval Pro] Run id: $RUN_ID"
echo "  dataset      = $DATASET"
echo "  predictions  = $PREDS"
echo "  out_dir      = $OUT_DIR"
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
    --namespace 'swebench' \
    2>&1 | tee "$OUT_DIR/run_eval.log"

echo "[Eval Pro] done: $(date)"