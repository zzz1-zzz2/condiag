"""Step 7a final: Docker eval of django-13513 x 4 baselines with norm_v3.

Converts SWE-bench test IDs to pytest paths, runs F2P+P2P.
Records strict_apply, normalized_apply, resolved for each baseline.
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
    # Determine file path from module
    # e.g. view_tests.tests.test_debug -> tests/view_tests/tests/test_debug.py
    file_path = "/".join(parts) + ".py"
    if not file_path.startswith("tests/"):
        file_path = "tests/" + file_path
    # Class is last part? No, class is part of module path in SWE-bench format.
    # Actually: module.path.ClassName where ClassName is the test class
    # pytest wants: file.py::ClassName::method
    # The module path in SWE-bench is module.path.ClassName
    # But the file path is tests/module/path.py
    # Class is embedded in the module path
    # Let's try: file_path::ClassName::method
    # We need to extract ClassName from the module path
    # Strategy: try all suffixes of parts as class name
    for i in range(len(parts) - 1, -1, -1):
        cls = parts[i]
        file_candidate = "/".join(parts[:i]) + ".py"
        if not file_candidate.startswith("tests/"):
            file_candidate = "tests/" + file_candidate
        return f"{file_candidate}::{cls}::{method}"
    return f"{file_path}::{method}"


iid = "django__django-13513"
inst = inst_info[iid]
f2p_raw = inst["f2p"]
p2p_raw = inst["p2p"]

f2p_pytest = [swb_to_pytest(t) for t in f2p_raw]
p2p_pytest = [swb_to_pytest(t) for t in p2p_raw]

print(f"Instance: {iid}")
print(f"F2P ({len(f2p_raw)}): {f2p_raw}")
print(f"  -> pytest: {f2p_pytest}")
print(f"P2P ({len(p2p_raw)}): {p2p_raw[:3]}...")
print(f"  -> pytest: {p2p_pytest[:3]}...")

# Step 1: Baseline — run tests WITHOUT model patch
print("\n" + "=" * 60)
print("BASELINE: Run F2P tests WITHOUT any model patch")
container = "eval_bl"
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

subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "pip install pytest -q 2>&1 | tail -3"
], capture_output=True, text=True, timeout=120)

# Run F2P
def run_pytest(container_name, test_list):
    if not test_list:
        return {"passed": 0, "failed": 0, "error": 0, "output": ""}
    test_args = " ".join(test_list)
    r = subprocess.run([
        "docker", "exec", container_name, "bash", "-lc",
        f"cd /testbed && python -m pytest {test_args} -v --no-header --tb=short -p no:cacheprovider 2>&1"
    ], capture_output=True, text=True, timeout=600)
    output = r.stdout + r.stderr
    passed = 0
    failed = 0
    error = 0
    m = re.search(r"=+\s+(\d+)\s+passed", output)
    if m: passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", output)
    if m: failed = int(m.group(1))
    m = re.search(r"(\d+)\s+errors?", output)
    if m: error = int(m.group(1))
    return {"passed": passed, "failed": failed, "error": error, "output": output[-2000:]}

f2p_res = run_pytest(container, f2p_pytest)
print(f"F2P baseline: passed={f2p_res['passed']} failed={f2p_res['failed']} error={f2p_res['error']}")
if f2p_res['passed'] == 0 and f2p_res['failed'] == 0:
    print("OUTPUT:", f2p_res['output'][:500])

# Also run P2P baseline
p2p_res = run_pytest(container, p2p_pytest)
print(f"P2P baseline: passed={p2p_res['passed']} failed={p2p_res['failed']} error={p2p_res['error']}")
if p2p_res['passed'] == 0 and p2p_res['failed'] == 0:
    print("OUTPUT:", p2p_res['output'][:500])

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nBaseline done. Now testing 4 baselines...")

# Step 2: Test each baseline
baselines = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]
results = {}

for bl in baselines:
    print(f"\n{'='*60}")
    print(f"Testing: {bl}")

    patch_path = RUNS_ROOT / bl / iid / "attempt_2" / "patch.diff"
    if not patch_path.is_file():
        print(f"  -> SKIP: no patch file")
        results[bl] = {"error": "no_patch"}
        continue

    raw_patch = patch_path.read_text(encoding="utf-8").strip()
    patch_chars = len(raw_patch)
    print(f"  Patch chars: {patch_chars}")

    # Start container
    container = f"eval_{bl[:8]}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    subprocess.run([
        "docker", "run", "--rm", "-d", "--name", container,
        "--platform", "linux/amd64", inst["image"], "sleep", "3600",
    ], capture_output=True)

    # Apply test_patch
    subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply /tmp/test.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)

    # Try strict apply first
    Path("/tmp/_model.diff").write_text(raw_patch, encoding="utf-8")
    subprocess.run(["docker", "cp", "/tmp/_model.diff", f"{container}:/tmp/model.diff"], capture_output=True)
    r_strict = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply --check /tmp/model.diff 2>&1 && git apply /tmp/model.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    strict_ok = r_strict.returncode == 0
    print(f"  Strict apply: {'OK' if strict_ok else 'FAIL'}")

    normalized_ok = False
    if not strict_ok:
        # Reset and try normalized
        subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git checkout -- . 2>&1 && git apply /tmp/test.diff 2>&1"
        ], capture_output=True)
        subprocess.run(["docker", "cp", "/home/swelite/condiag/scripts_tmp/norm_v3_inline.py", f"{container}:/tmp/norm.py"], capture_output=True)
        r_norm = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && python3 /tmp/norm.py /tmp/model.diff 2>&1"
        ], capture_output=True, text=True, timeout=60)
        norm_report = r_norm.stdout[:500]
        print(f"  Normalizer: {norm_report[:300]}")

        # Check git diff
        r_diff = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        if r_diff.stdout.strip():
            normalized_ok = True
            print(f"  Normalized apply OK ({len(r_diff.stdout)} chars diff)")
        else:
            print(f"  Normalized apply FAIL (no diff produced)")
    else:
        normalized_ok = True  # strict_ok implies normalized_ok

    # Install pytest and run tests
    subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "pip install pytest -q 2>&1 | tail -3"
    ], capture_output=True, text=True, timeout=120)

    f2p_res = run_pytest(container, f2p_pytest)
    p2p_res = run_pytest(container, p2p_pytest)

    f2p_pass = f2p_res["failed"] == 0 and f2p_res["error"] == 0 and f2p_res["passed"] >= len(f2p_pytest)
    p2p_pass = p2p_res["failed"] == 0 and p2p_res["error"] == 0

    resolved = "RESOLVED" if (f2p_pass and p2p_pass) else ("PARTIAL" if f2p_pass else "FAILED")

    print(f"  F2P: passed={f2p_res['passed']} failed={f2p_res['failed']} error={f2p_res['error']}")
    print(f"  P2P: passed={p2p_res['passed']} failed={p2p_res['failed']} error={p2p_res['error']}")
    print(f"  -> {resolved}")

    results[bl] = {
        "patch_chars": patch_chars,
        "strict_apply": strict_ok,
        "normalized_apply": normalized_ok or strict_ok,
        "f2p": f2p_res,
        "p2p": p2p_res,
        "resolved": resolved,
    }

    subprocess.run(["docker", "rm", "-f", container], capture_output=True)

# Print summary
print(f"\n{'='*70}")
print("EVAL SUMMARY: django-13513 x 4 baselines")
print(f"{'='*70}")
print(f"{'baseline':<25s} {'chars':>6s} {'strict':>7s} {'norm':>7s} {'F2P':>12s} {'P2P':>12s} {'result':>10s}")
print("-" * 85)
for bl in baselines:
    r = results.get(bl, {})
    if "error" in r:
        print(f"{bl:<25s} {'-':>6s} {'-':>7s} {'-':>7s} {'-':>12s} {'-':>12s} {'ERR:'+r['error']:>10s}")
    else:
        f2p_str = f"{r['f2p']['passed']}/{r['f2p']['passed']+r['f2p']['failed']}"
        p2p_str = f"{r['p2p']['passed']}/{r['p2p']['passed']+r['p2p']['failed']}"
        print(f"{bl:<25s} {r['patch_chars']:>6d} {str(r['strict_apply']):>7s} {str(r['normalized_apply']):>7s} {f2p_str:>12s} {p2p_str:>12s} {r['resolved']:>10s}")

out_path = OUT_DIR / "repair_smoke_eval.json"
out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"\nResults saved to: {out_path}")
