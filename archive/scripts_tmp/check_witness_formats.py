"""Check missing witness formats."""
import json
from pathlib import Path

inst = Path("/mnt/d/condiag-artifacts/condiag/instances")
pro_dir = Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_pro_official")
poly_dir = Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_poly_official")
multi_dir = Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_multi_official")

print("=== Pro instances missing witness ===")
for d in sorted(pro_dir.iterdir()):
    iid = d.name
    fw = inst / iid / "attempt_1" / "failure_witness.json"
    if not fw.exists():
        ws = d / "workspace"
        print(f"  {iid}: output.json={ws.joinpath('output.json').exists()}, stdout.log={ws.joinpath('stdout.log').exists()}, stderr.log={ws.joinpath('stderr.log').exists()}")

print("\n=== Poly instances missing witness ===")
for d in sorted(poly_dir.iterdir()):
    iid = d.name
    fw = inst / iid / "attempt_1" / "failure_witness.json"
    if not fw.exists():
        files = list(d.iterdir())
        print(f"  {iid}: {[f.name for f in files[:5]]}")

print("\n=== Multi instances missing witness ===")
for d in sorted(multi_dir.iterdir()):
    iid = d.name
    fw = inst / iid / "attempt_1" / "failure_witness.json"
    if not fw.exists():
        files = list(d.iterdir())
        print(f"  {iid}: {[f.name for f in files[:5]]}")

# Also check: which missing ones have NO eval logs anywhere?
print("\n=== Fully missing (no eval data at all) ===")
with open("/mnt/d/condiag-artifacts/condiag/manifests/instances_v1.jsonl") as f:
    manifest = {json.loads(l)["instance_id"]: json.loads(l) for l in f}

for iid, d in manifest.items():
    if d.get("resolved", False): continue
    fw = inst / iid / "attempt_1" / "failure_witness.json"
    if fw.exists(): continue
    # Check all known eval sources
    found = False
    for src in [pro_dir, poly_dir, multi_dir]:
        if src.joinpath(iid).exists():
            found = True
            break
    if not found:
        print(f"  {iid}")
