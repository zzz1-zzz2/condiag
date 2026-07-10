"""Compile SWE-bench eval report from per-instance report.json files."""
import json, os, sys
from collections import Counter

REPORT_DIR = "/home/swelite/condiag/logs/run_evaluation/miniswe_verified_new32"
OUT = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_verified_new32/miniswe_verified_new32_results.json"

results = {}
errors = []
for root, dirs, files in os.walk(REPORT_DIR):
    for fname in files:
        if fname == "report.json":
            path = os.path.join(root, fname)
            try:
                data = json.load(open(path))
            except Exception as e:
                errors.append((path, str(e)))
                continue
            # report.json has key=instance_id
            for inst_id, report in data.items():
                resolved = report.get("resolved", False)
                results[inst_id] = {
                    "resolved": resolved,
                    "patch_apply_ok": report.get("patch_successfully_applied", False),
                    "tests_status": report.get("tests_status", {}),
                }

status_counts = Counter(r["resolved"] for r in results.values())
print(f"Total instances: {len(results)}")
print(f"Resolved: {status_counts.get(True, 0)}")
print(f"Unresolved: {status_counts.get(False, 0)}")
print(f"Errors: {len(errors)}")

for inst_id in sorted(results.keys()):
    r = results[inst_id]
    label = "RESOLVED" if r["resolved"] else "UNRESOLVED"
    f2p_fail = len(r.get("tests_status", {}).get("FAIL_TO_PASS", {}).get("failure", []))
    p2p_fail = len(r.get("tests_status", {}).get("PASS_TO_PASS", {}).get("failure", []))
    print(f"  {inst_id:45s}: {label} (f2p_fail={f2p_fail}, p2p_fail={p2p_fail})")

# Save
json.dump({
    "n_total": len(results),
    "n_resolved": status_counts.get(True, 0),
    "n_unresolved": status_counts.get(False, 0),
    "n_errors": len(errors),
    "results": results,
}, open(OUT, "w"), indent=2)
print(f"\nSaved to: {OUT}")
