"""Step 7a final: Use Django runtests.py instead of pytest."""
import json, re, subprocess, time
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


def swb_to_django_test(test_id: str) -> str:
    """Convert SWE-bench test ID to Django runtests.py format.
    'test_name (module.path.ClassName)' -> 'module.path.ClassName.test_name'
    """
    m = re.match(r"(.+?)\s+\((.+?)\)", test_id)
    if not m:
        return test_id
    method = m.group(1).strip()
    modpath = m.group(2).strip()
    return f"{modpath}.{method}"


iid = "django__django-13513"
inst = inst_info[iid]
f2p_tests = [swb_to_django_test(t) for t in inst["f2p"]]
p2p_tests = [swb_to_django_test(t) for t in inst["p2p"]]

print(f"Instance: {iid}")
print(f"F2P: {f2p_tests}")
print(f"P2P (first 3): {p2p_tests[:3]}")

# Step 1: Baseline — run tests WITHOUT model patch
print("\n" + "=" * 60)
print("BASELINE: Run F2P tests via Django runtests.py")
container = "eval_dj"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64", inst["image"], "sleep", "3600",
], capture_output=True)

Path("/tmp/_test.diff").write_text(inst["test_patch"], encoding="utf-8")
subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git apply /tmp/test.diff 2>&1"
], capture_output=True, text=True, timeout=30)

# Run Django tests using runtests.py
f2p_arg = " ".join(f2p_tests)
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    f"cd /testbed && python tests/runtests.py {f2p_arg} --verbosity=2 2>&1"
], capture_output=True, text=True, timeout=600)
print(f"F2P baseline output:\n{r.stdout[-3000:]}")
if r.stderr:
    print(f"STDERR: {r.stderr[:500]}")

# Parse results
m = re.search(r"Ran\s+(\d+)\s+tests?", r.stdout)
ran = int(m.group(1)) if m else 0
m = re.search(r"FAILED\s+\(failures=(\d+)\)", r.stdout)
failures = int(m.group(1)) if m else 0
m = re.search(r"FAILED\s+\(errors=(\d+)\)", r.stdout)
errors = int(m.group(1)) if m else 0
ok = "OK" in r.stdout and "FAILED" not in r.stdout
print(f"  Ran={ran}, failures={failures}, errors={errors}, OK={ok}")

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nBaseline done.")
