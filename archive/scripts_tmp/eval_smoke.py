"""Step 7a v3: Docker eval of attempt_2 patches on django-13513 x 4 baselines.

Uses norm_v3 to normalize patches, then runs F2P+P2P tests.
Records: strict_apply, normalized_apply, resolved.
"""
import json, re, subprocess, sys, time
from pathlib import Path

RUNS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")

# Load instance metadata
from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
inst_info = {}
for row in ds:
    iid = row.get("instance_id", "")
    if iid == "django__django-13513":
        inst_info[iid] = {
            "image": "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
            "f2p": row.get("FAIL_TO_PASS") or [],
            "p2p": row.get("PASS_TO_PASS") or [],
            "test_patch": row.get("test_patch") or "",
        }
        break

iid = "django__django-13513"
inst = inst_info[iid]
print(f"Instance: {iid}")
print(f"F2P tests: {inst['f2p']}")
print(f"P2P tests: {inst['p2p'][:3]}...")

# Step 1: Baseline — run tests WITHOUT model patch
print("\n" + "=" * 60)
print("BASELINE: Run F2P tests WITHOUT any model patch")
container = "eval_baseline"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64", inst["image"], "sleep", "3600",
], capture_output=True)

# Apply test_patch
Path("/tmp/_test.diff").write_text(inst["test_patch"], encoding="utf-8")
subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git apply /tmp/test.diff 2>&1"
], capture_output=True, text=True, timeout=30)
print(f"test_patch apply: rc={r.returncode} {r.stdout[:200]}")

# Install pytest
subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "pip install pytest -q 2>&1 | tail -3"
], capture_output=True, text=True, timeout=120)

# Run F2P tests
f2p_tests = inst["f2p"]
f2p_args = " ".join(f2p_tests)
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    f"cd /testbed && python -m pytest {f2p_args} -v --no-header --tb=short -p no:cacheprovider 2>&1"
], capture_output=True, text=True, timeout=300)
print(f"\nF2P BASELINE (no model patch):")
print(r.stdout[-2000:])
m = re.search(r"=+\s+(\d+)\s+passed", r.stdout)
passed = int(m.group(1)) if m else 0
m = re.search(r"(\d+)\s+failed", r.stdout)
failed = int(m.group(1)) if m else 0
print(f"  -> passed={passed}, failed={failed}")

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("Done.")
