#!/usr/bin/env python3
import json, os, sys, traceback
sys.path.insert(0, "/home/swelite/condiag/ContextBench")
from contextbench.parsers.gold import GoldLoader
from contextbench.evaluate import evaluate_instance, aggregate_results

GOLD_PATH = "/home/swelite/condiag/ContextBench/data/full.parquet"
PRED_PATH = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/contextbench_input/preds_all.jsonl"
CACHE_DIR = "/mnt/d/condiag-artifacts/cache/contextbench_repos"
OUT_PATH = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/contextbench_results/results_all.jsonl"
ERROR_LOG = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/contextbench_results/errors.log"

gold_loader = GoldLoader(GOLD_PATH)

pred_list = []
with open(PRED_PATH) as f:
    for line in f:
        if line.strip():
            pred_list.append(json.loads(line))
print(f"{len(pred_list)} predictions", file=sys.stderr)

done_keys = set()
if os.path.exists(OUT_PATH):
    with open(OUT_PATH) as f:
        for line in f:
            if line.strip():
                done_keys.add(json.loads(line).get("instance_id", ""))
    print(f"  {len(done_keys)} done", file=sys.stderr)

results = []
for i, pred_data in enumerate(pred_list):
    instance_id = pred_data.get("instance_id") or pred_data.get("original_inst_id")
    if not instance_id or instance_id in done_keys:
        continue
    print(f"[{i+1}/{len(pred_list)}] {str(instance_id)[:55]} ...", file=sys.stderr)
    sys.stderr.flush()
    gold_ctx = gold_loader.get(instance_id)
    if not gold_ctx:
        err = {"instance_id": instance_id, "error": "missing_gold"}
        results.append(err)
        with open(OUT_PATH, "a") as f:
            f.write(json.dumps(err) + "\n")
        continue
    try:
        result = evaluate_instance(instance_id, gold_ctx, pred_data, CACHE_DIR)
        results.append(result)
        with open(OUT_PATH, "a") as f:
            f.write(json.dumps(result) + "\n")
        status = "OK" if "error" not in result else "ERR:" + result["error"]
        print(f"  -> {status}", file=sys.stderr)
    except Exception as e:
        tb = traceback.format_exc()
        err = {"instance_id": instance_id, "error": "exception:" + str(e)}
        results.append(err)
        with open(OUT_PATH, "a") as f:
            f.write(json.dumps(err) + "\n")
        with open(ERROR_LOG, "a") as f:
            f.write("\n=== " + str(instance_id) + " ===\n" + tb + "\n")
        print(f"  -> EXCEPTION: {e}", file=sys.stderr)

print(f"DONE: {len(results)} processed", file=sys.stderr)
