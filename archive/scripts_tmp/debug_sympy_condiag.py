"""Debug: check condiag_packet_only patch application and test error on sympy-19954."""
import subprocess, json
from pathlib import Path

IMG = "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest"
IID = "sympy__sympy-19954"

from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
test_patch = ""
f2p_raw = []
for row in ds:
    if row.get("instance_id") == IID:
        test_patch = row.get("test_patch") or ""
        f2p_raw = row.get("FAIL_TO_PASS") or []
        if isinstance(f2p_raw, str): f2p_raw = json.loads(f2p_raw)
        break

container = "debug_sympy"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64", IMG, "sleep", "3600",
], capture_output=True)

# Apply test_patch
Path("/tmp/_test.diff").write_text(test_patch, encoding="utf-8")
subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
subprocess.run(["docker", "exec", container, "bash", "-lc",
    "cd /testbed && git apply /tmp/test.diff 2>&1"], capture_output=True)

# Read condiag patch and normalize
pp = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/condiag_packet_only/sympy__sympy-19954/attempt_2/patch.diff")
raw = pp.read_text(encoding="utf-8").strip()
lines = [l for l in raw.split("\n") if not l.startswith("```")]
patch_text = "\n".join(lines)
Path("/tmp/_model.diff").write_text(patch_text, encoding="utf-8")
subprocess.run(["docker", "cp", "/tmp/_model.diff", f"{container}:/tmp/model.diff"], capture_output=True)

# Run norm_v3 and capture full output
subprocess.run(["docker", "cp",
    "/home/swelite/condiag/scripts_tmp/norm_v3_inline.py",
    f"{container}:/tmp/norm.py"], capture_output=True)
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && python3 /tmp/norm.py /tmp/model.diff 2>&1"
], capture_output=True, text=True, timeout=60)
print("=== Normalizer output ===")
print(r.stdout)

print("\n=== Git diff ===")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git diff 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout[:2000])

print("\n=== Check patched source (lines 2195-2220) ===")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && sed -n '2195,2220p' sympy/combinatorics/perm_groups.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("\n=== Python syntax check ===")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && python -m py_compile sympy/combinatorics/perm_groups.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(f"Syntax check: rc={r.returncode}")
if r.stderr:
    print(r.stderr[:500])

print("\n=== Install pytest and run F2P with full output ===")
subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "pip install pytest -q 2>&1 | tail -2"
], capture_output=True, text=True, timeout=120)

k_expr = " or ".join(f2p_raw)
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    f"cd /testbed && python -m pytest sympy/combinatorics/tests/test_perm_groups.py "
    f"-k '{k_expr}' -v --tb=long 2>&1"
], capture_output=True, text=True, timeout=300)
print(r.stdout[-3000:])
print(f"STDERR: {r.stderr[:500]}")

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("Done.")
