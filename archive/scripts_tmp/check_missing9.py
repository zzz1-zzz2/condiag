"""Check the 9 remaining missing-witness instances."""
import json
from pathlib import Path

with open("/mnt/d/condiag-artifacts/condiag/manifests/instances_v1.jsonl") as f:
    manifest = {json.loads(l)["instance_id"]: json.loads(l) for l in f}

missing = [
    "Significant-Gravitas__AutoGPT-4652",
    "alibaba__fastjson2-2559",
    "catchorg__Catch2-1608",
    "django__django-13195",
    "facebook__zstd-2094",
    "grpc__grpc-go-2996",
    "instance_element-hq__element-web-aeabf3b18896ac1eb7ae9757e66ce886120f8309-vnan",
    "mui__material-ui-34337",
    "ponylang__ponyc-2532",
]

print("Missing witness instances:")
for iid in missing:
    md = manifest.get(iid, {})
    print(f"  {iid}")
    print(f"    resolved={md.get('resolved')}, attempt1_status={md.get('attempt1_status')}, pool={md.get('pool')}, notes={md.get('notes', '')}")

    # Check if there's a log we might have missed
    from pathlib import Path
    v0 = Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions")
    for src in [v0 / "swebench_verified_official", v0 / "swebench_multi_official",
                v0 / "swebench_poly_official", v0 / "swebench_pro_official"]:
        inst_dir = src / iid
        if inst_dir.exists():
            print(f"      in {src.name}: {[p.name for p in inst_dir.iterdir()][:5]}")
    # Check runs/
    runs = Path("/mnt/d/condiag-artifacts/runs")
    import subprocess
    result = subprocess.run(["find", str(runs), "-name", f"{iid}.traj.json"],
                          capture_output=True, text=True, timeout=10)
    if result.stdout.strip():
        print(f"      traj in runs/: {result.stdout.strip()[:200]}")
    print()

# Count: timeout + pending
timeout = [iid for iid, d in manifest.items() if d.get("attempt1_status") == "timeout"]
pending = [iid for iid, d in manifest.items() if d.get("attempt1_status") == "pending"]
ff_actual = [iid for iid, d in manifest.items()
             if not d.get("resolved", False)
             and d.get("attempt1_status") not in ("timeout", "pending")]
print(f"timeout ({len(timeout)}): {timeout}")
print(f"pending ({len(pending)}): {pending}")
print(f"actual first-failed: {len(ff_actual)}")
