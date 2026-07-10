#!/bin/bash
set -e
cd /home/swelite/condiag/ContextBench

OUTDIR="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/contextbench_results"
mkdir -p ""

python3 -m contextbench.evaluate   --gold data/full.parquet   --pred /mnt/d/condiag-artifacts/condiag/v0/eval_predictions/contextbench_input/preds_all.jsonl   --cache /mnt/d/condiag-artifacts/cache/contextbench_repos   --out "/results_all.jsonl"   > "/eval.log" 2>&1

echo "DONE" >> "/eval.log"
