"""Step 7a final v2: Fixed test ID parsing + diff detection."""
import json, re, subprocess, sys, time
from pathlib import Path

RUNS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")

from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
inst_info = {}
for row in ds:
    iid = row.get("instance_id", "")
    if iid == "django__django-13513":
        f2p_raw = row.get("FAIL_TO_PASS") or []
        p2p_raw = row.get("PASS_TO_PASS") or []
        # HF datasets may return JSON strings for list columns
        if isinstance(f2p_raw, str):
            f2p_raw = json.loads(f2p_raw)
        if isinstance(p2p_raw, str):
            p2p_raw = json.loads(p2p_raw)
        inst_info[iid] = {
            "image": "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
            "f2p": f2p_raw,
            "p2p": p2p_raw,
            "test_patch": row.get("test_patch") or "",
        }
        break


def swb_to_pytest(test_id: str) -> str:
    """Convert SWE-bench test ID to pytest path.
    'test_name (module.path.ClassName)' -> 'tests/module/path.py::ClassName::test_name'
    """
    m = re.match(r"(.+?)\s+\((.+?)\)", test_id)
    if not m:
        return test_id
    method = m.group(1).strip()
    modpath = m.group(2).strip()
    parts = modpath.split(".")
    # parts = ['view_tests', 'tests', 'test_debug', 'ExceptionReporterTests']
    # file = tests/view_tests/tests/test_debug.py
    # class = ExceptionReporterTests (last element)
    cls = parts[-1]
    file_parts = parts[:-1]  # ['view_tests', 'tests', 'test_debug']
    file_path = "tests/" + "/".join(file_parts) + ".py"
    return f"{file_path}::{cls}::{method}"


iid = "django__django-13513"
inst = inst_info[iid]
f2p_pytest = [swb_to_pytest(t) for t in inst["f2p"]]
p2p_pytest = [swb_to_pytest(t) for t in inst["p2p"]]

print(f"Instance: {iid}")
print(f"F2P ({len(inst['f2p'])}): {inst['f2p']}")
print(f"  -> pytest: {f2p_pytest}")
print(f"P2P ({len(inst['p2p'])}): {inst['p2p'][:3]}...")
print(f"  -> pytest: {p2p_pytest[:3]}...")

# Step 1: Baseline — check test file exists and run tests
print("\n" + "=" * 60)
print("BASELINE: Verify test setup")
container = "eval_bl2"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64", inst["image"], "sleep", "3600",
], capture_output=True)

Path("/tmp/_test.diff").write_text(inst["test_patch"], encoding="utf-8")
subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git apply /tmp/test.diff 2>&1"
], capture_output=True, text=True, timeout=30)
print(f"test_patch apply: rc={r.returncode}")

# Check if test file exists
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "ls -la /testbed/tests/view_tests/tests/test_debug.py 2>&1 && echo '---' && grep -n 'test_innermost_exception_without_traceback' /testbed/tests/view_tests/tests/test_debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(f"Test file check:\n{r.stdout[:500]}")

# Check git diff to verify test_patch changes
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git diff --stat 2>&1"
], capture_output=True, text=True, timeout=30)
print(f"Git diff stat:\n{r.stdout[:300]}")

subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "pip install pytest -q 2>&1 | tail -3"
], capture_output=True, text=True, timeout=120)

# Run F2P with pytest path
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    f"cd /testbed && python -m pytest {f2p_pytest[0]} -v --no-header --tb=short 2>&1"
], capture_output=True, text=True, timeout=300)
print(f"\nF2P test run:\n{r.stdout[-1500:]}")
if r.stderr:
    print(f"STDERR: {r.stderr[:300]}")

# Also try running all tests in the file
r2 = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    f"cd /testbed && python -m pytest tests/view_tests/tests/test_debug.py -k test_innermost_exception_without_traceback -v --no-header --tb=short 2>&1"
], capture_output=True, text=True, timeout=300)
print(f"\nF2P (by -k filter):\n{r2.stdout[-1000:]}")

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
