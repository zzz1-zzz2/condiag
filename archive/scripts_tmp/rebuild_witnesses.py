"""Clean up witnesses and rebuild for first-failed only."""
import json
from pathlib import Path

inst = Path("/mnt/d/condiag-artifacts/condiag/instances")
v0 = Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions")

with open("/mnt/d/condiag-artifacts/condiag/manifests/instances_v1.jsonl") as f:
    manifest = {json.loads(l)["instance_id"]: json.loads(l) for l in f}

# Step 1: Delete ALL witnesses, rebuild from scratch
deleted = 0
for iid in manifest:
    fw = inst / iid / "attempt_1" / "failure_witness.json"
    if fw.exists():
        fw.unlink()
        deleted += 1
print(f"Deleted {deleted} witness files")

# Step 2: Build witnesses ONLY for first-failed
ff_ids = [iid for iid, md in manifest.items() if not md.get("resolved", False)]
print(f"First-failed total: {len(ff_ids)}")

import sys
sys.path.insert(0, "/home/swelite/condiag")
from experiments.failure_witness_builder import build_failure_witness, from_eval_log

# Register eval log sources
sources = [
    v0 / "swebench_verified_official",
    v0 / "swebench_multi_official",
    v0 / "swebench_poly_official",
    v0 / "swebench_pro_official",
    v0 / "swebench_verified_new32",
]

# Also search recursively in run_evaluation logs
RUN_EVAL = Path("/home/swelite/condiag/logs/run_evaluation")

def find_log(iid):
    for src in sources:
        # 1. test_output.log (standard Verified/Multi/Poly)
        f = src / iid / "test_output.log"
        if f.exists(): return f
        # 2. test_output.txt
        f = src / iid / "test_output.txt"
        if f.exists(): return f
        # 3. workspace/stdout.log (Pro)
        f = src / iid / "workspace" / "stdout.log"
        if f.exists(): return f
        # 4. recursive search for test_output.txt in run_evaluation
        for found in src.rglob(iid):
            if found.is_dir():
                for fn in ["test_output.txt", "test_output.log"]:
                    f2 = found / fn
                    if f2.exists(): return f2

    # 5. Fallback: search run_evaluation logs recursively
    if RUN_EVAL.exists():
        for found in RUN_EVAL.rglob(iid):
            if found.is_dir():
                for fn in ["test_output.txt", "test_output.log"]:
                    f2 = found / fn
                    if f2.exists(): return f2
    return None

built = 0
nowitness = 0
errors = 0
for iid in ff_ids:
    log = find_log(iid)
    if log is None:
        print(f"  [NOWITNESS] {iid}: no eval log")
        nowitness += 1
        continue

    try:
        # Use from_eval_log directly (it's a standalone function)
        witness = from_eval_log(iid, log)
        target = inst / iid / "attempt_1" / "failure_witness.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        from dataclasses import asdict
        target.write_text(json.dumps(asdict(witness), indent=2))
        print(f"  [OK] {iid}: {log.parent.name}/{log.name}")
        built += 1
    except Exception as e:
        print(f"  [ERROR] {iid}: {e} (log={log})")
        errors += 1

print(f"\nBuilt: {built}/{len(ff_ids)}, No-witness: {nowitness}, Errors: {errors}")
