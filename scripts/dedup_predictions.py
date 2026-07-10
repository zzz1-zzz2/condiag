"""Deduplicate predictions across batches (same iid may appear in multiple batches).
For each iid, keep the latest batch's entry (by mtime). Output deduped per-bench JSONs.
"""
import json, glob, os, csv
from collections import defaultdict

PREDS_ROOT = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions"

# Collect all predictions by iid with batch info
by_iid = {}  # iid -> {batch, bench, mtime, pred}
for path in sorted(glob.glob(f"{PREDS_ROOT}/*/predictions_all.jsonl")):
    batch = path.split("/")[-2]
    mtime = os.path.getmtime(path)
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            iid = d["instance_id"]
            # bench from path name
            bench = None
            for b in ["Verified", "Multi", "Pro", "Poly"]:
                if f"predictions_{b}.json" in path or path.endswith(f"{batch}/predictions_all.jsonl"):
                    # search for bench-specific file in this batch dir
                    for spec in glob.glob(f"{PREDS_ROOT}/{batch}/predictions_{b}.json"):
                        with open(spec) as fp:
                            arr = json.load(fp)
                            if any(x["instance_id"] == iid for x in arr):
                                bench = b
                                break
                    if bench:
                        break
            by_iid.setdefault(iid, []).append({
                "batch": batch, "mtime": mtime, "bench": bench, "pred": d,
            })

# For each iid, pick latest
deduped = {}  # iid -> pred
bench_for_iid = {}
for iid, versions in by_iid.items():
    versions.sort(key=lambda v: v["mtime"], reverse=True)
    chosen = versions[0]
    deduped[iid] = chosen["pred"]
    bench_for_iid[iid] = chosen["bench"]

# Group by bench
by_bench = defaultdict(list)
for iid, pred in deduped.items():
    by_bench[bench_for_iid[iid]].append(pred)

print(f"Unique instances: {len(deduped)}")
for b, arr in sorted(by_bench.items()):
    print(f"  {b}: {len(arr)}")

# Write per-bench deduped JSON files
OUT = os.path.join(PREDS_ROOT, "deduped")
os.makedirs(OUT, exist_ok=True)
for bench, arr in by_bench.items():
    out_json = f"{OUT}/predictions_{bench}.json"
    out_jsonl = f"{OUT}/predictions_{bench}.jsonl"
    with open(out_json, "w") as f:
        json.dump(arr, f, indent=2)
    with open(out_jsonl, "w") as f:
        for p in arr:
            f.write(json.dumps(p) + "\n")
    print(f"  → {out_json} ({len(arr)})")
    print(f"  → {out_jsonl}")

# Master
with open(f"{OUT}/predictions_all.jsonl", "w") as f:
    for iid in sorted(deduped.keys()):
        f.write(json.dumps(deduped[iid]) + "\n")
print(f"  → {OUT}/predictions_all.jsonl ({len(deduped)})")