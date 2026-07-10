"""Compare new predictions against canonical eval matrix."""
import json, glob, csv

CANON = "/mnt/d/condiag-artifacts/condiag/v0/canonical_base_eval_matrix.csv"
PREDS_ROOT = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions"

canonical = {}
with open(CANON) as f:
    r = csv.DictReader(f)
    for row in r:
        iid = row["instance_id"]
        canonical[iid] = row["eval_status"]

print(f"Canonical: {len(canonical)} instances, statuses:")
from collections import Counter
print(f"  {Counter(canonical.values())}")

# All new preds
all_preds = {}  # iid -> list of (batch, bench, traj_path)
for path in sorted(glob.glob(f"{PREDS_ROOT}/*/predictions_all.jsonl")):
    batch = path.split("/")[-2]
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            iid = d["instance_id"]
            all_preds.setdefault(iid, []).append(batch)

print(f"\nAll preds (unique iids): {len(all_preds)}")
overlap = set(all_preds) & set(canonical)
print(f"Overlap with canonical: {len(overlap)}")
need = set(all_preds) - set(canonical)
print(f"Need SWE-bench eval: {len(need)}")

# By bench
need_by_bench = {}
for iid in need:
    for b in all_preds[iid]:
        bench = b  # extract bench from predictions
        # Determine bench from path
        for p in glob.glob(f"{PREDS_ROOT}/*/predictions_{b}.json"):
            pass
    # Look up bench
    for path in glob.glob(f"{PREDS_ROOT}/*/predictions_*.json"):
        with open(path) as f:
            try:
                arr = json.load(f)
            except Exception:
                continue
            for d in arr:
                if d["instance_id"] == iid:
                    bench = path.split("_")[-1].replace(".json", "")
                    need_by_bench.setdefault(bench, []).append(iid)
                    break

print(f"\nNeed eval by bench:")
for b, ids in sorted(need_by_bench.items()):
    print(f"  {b}: {len(ids)}")
    for iid in sorted(ids):
        marker = ""
        if iid in canonical:
            marker = " [ALREADY EVAL'D]"
        print(f"    - {iid}{marker}")

# Save need-eval list
with open("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/need_eval.json", "w") as f:
    json.dump({
        "canonical_count": len(canonical),
        "new_pred_count": len(all_preds),
        "overlap_count": len(overlap),
        "need_eval_count": len(need),
        "need_eval_by_bench": {b: sorted(ids) for b, ids in need_by_bench.items()},
        "need_eval_all": sorted(need),
    }, f, indent=2)
print(f"\nSaved: /mnt/d/condiag-artifacts/condiag/v0/eval_predictions/need_eval.json")