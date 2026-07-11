"""Step 7a FINAL: Docker eval of django-13513 x 4 baselines.

Uses Django runtests.py (not pytest) for proper Django test execution.
Normalizer v3 applied equally to all baselines.
Records: strict_apply, normalized_apply, F2P, P2P, resolved.
"""
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


def swb_to_django(test_id: str) -> str:
    """'test_name (module.path.ClassName)' -> 'module.path.ClassName.test_name'"""
    m = re.match(r"(.+?)\s+\((.+?)\)", test_id)
    if not m:
        return test_id
    return f"{m.group(2).strip()}.{m.group(1).strip()}"


def run_django_tests(container_name, test_list, timeout=600):
    """Run Django tests and return {passed, failed, error, output}."""
    if not test_list:
        return {"passed": 0, "failed": 0, "error": 0, "output": ""}
    test_arg = " ".join(test_list)
    r = subprocess.run([
        "docker", "exec", container_name, "bash", "-lc",
        f"cd /testbed && python tests/runtests.py {test_arg} --verbosity=2 2>&1"
    ], capture_output=True, text=True, timeout=timeout)
    output = r.stdout + r.stderr
    ran = 0; failed = 0; error = 0
    m = re.search(r"Ran\s+(\d+)\s+tests?", output)
    if m: ran = int(m.group(1))
    m = re.search(r"failures=(\d+)", output)
    if m: failed = int(m.group(1))
    m = re.search(r"errors=(\d+)", output)
    if m: error = int(m.group(1))
    return {"ran": ran, "failed": failed, "error": error, "output": output[-3000:]}


iid = "django__django-13513"
inst = inst_info[iid]
f2p_tests = [swb_to_django(t) for t in inst["f2p"]]
p2p_tests = [swb_to_django(t) for t in inst["p2p"]]

print(f"Instance: {iid}")
print(f"F2P: {f2p_tests}")
print(f"P2P: {len(p2p_tests)} tests")

# Prepare test_patch
Path("/tmp/_test.diff").write_text(inst["test_patch"], encoding="utf-8")

baselines = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]
results = {}

for bl in baselines:
    print(f"\n{'='*60}")
    print(f"[{bl}]")

    patch_path = RUNS_ROOT / bl / iid / "attempt_2" / "patch.diff"
    if not patch_path.is_file():
        print(f"  SKIP: no patch")
        results[bl] = {"error": "no_patch"}
        continue

    raw_patch = patch_path.read_text(encoding="utf-8").strip()
    patch_chars = len(raw_patch)
    print(f"  Patch: {patch_chars} chars")

    container = f"ev_{bl[:6]}"
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
    print(f"  test_patch apply: rc={r.returncode}")

    # Try strict apply
    Path("/tmp/_model.diff").write_text(raw_patch, encoding="utf-8")
    subprocess.run(["docker", "cp", "/tmp/_model.diff", f"{container}:/tmp/model.diff"], capture_output=True)
    r_strict = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply --check /tmp/model.diff 2>&1 && git apply /tmp/model.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    strict_ok = r_strict.returncode == 0
    print(f"  strict_apply: {'OK' if strict_ok else 'FAIL'}")

    normalized_ok = False
    if not strict_ok:
        # Reset to test_patch state
        subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git checkout -- . 2>&1 && git apply /tmp/test.diff 2>&1"
        ], capture_output=True)

        # Get baseline git diff (only test_patch changes)
        r_before = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        diff_before = r_before.stdout.strip()

        # Run normalizer v3
        subprocess.run(["docker", "cp", "/home/swelite/condiag/scripts_tmp/norm_v3_inline.py", f"{container}:/tmp/norm.py"], capture_output=True)
        r_norm = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && python3 /tmp/norm.py /tmp/model.diff 2>&1"
        ], capture_output=True, text=True, timeout=60)
        norm_report = r_norm.stdout[:600]
        print(f"  normalizer: {norm_report[:250]}")

        # Check if normalizer actually changed anything
        r_after = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        diff_after = r_after.stdout.strip()

        if diff_after != diff_before and len(diff_after) > len(diff_before):
            normalized_ok = True
            print(f"  normalized_apply: OK (diff grew from {len(diff_before)} to {len(diff_after)} chars)")
        else:
            print(f"  normalized_apply: FAIL (no additional changes)")
    else:
        normalized_ok = True

    # Run tests
    print(f"  Running F2P tests...")
    f2p_res = run_django_tests(container, f2p_tests)
    print(f"  F2P: ran={f2p_res['ran']} failed={f2p_res['failed']} error={f2p_res['error']}")

    p2p_res = {"ran": 0, "failed": 0, "error": 0}
    if normalized_ok or strict_ok:
        print(f"  Running P2P tests ({len(p2p_tests)} tests, may take a while)...")
        p2p_res = run_django_tests(container, p2p_tests, timeout=1200)
        print(f"  P2P: ran={p2p_res['ran']} failed={p2p_res['failed']} error={p2p_res['error']}")

    f2p_pass = f2p_res["failed"] == 0 and f2p_res["error"] == 0 and f2p_res["ran"] >= len(f2p_tests)
    p2p_pass = p2p_res["failed"] == 0 and p2p_res["error"] == 0

    if f2p_pass and p2p_pass:
        resolved = "RESOLVED"
    elif f2p_pass:
        resolved = "PARTIAL"
    else:
        resolved = "FAILED"

    print(f"  -> {resolved}")

    results[bl] = {
        "patch_chars": patch_chars,
        "strict_apply": strict_ok,
        "normalized_apply": normalized_ok,
        "f2p": {k: v for k, v in f2p_res.items() if k != "output"},
        "p2p": {k: v for k, v in p2p_res.items() if k != "output"},
        "resolved": resolved,
    }

    subprocess.run(["docker", "rm", "-f", container], capture_output=True)

# Summary
print(f"\n{'='*70}")
print("EVAL SUMMARY: django-13513 x 4 baselines")
print(f"{'='*70}")
print(f"{'baseline':<25s} {'chars':>6s} {'strict':>7s} {'norm':>7s} {'F2P(fail)':>10s} {'P2P(fail)':>10s} {'result':>10s}")
print("-" * 85)
for bl in baselines:
    r = results.get(bl, {})
    if "error" in r:
        print(f"{bl:<25s} {'-':>6s} {'-':>7s} {'-':>7s} {'-':>10s} {'-':>10s} {'ERR:'+r['error']:>10s}")
    else:
        f2p_str = str(r['f2p'].get('failed', '?'))
        p2p_str = str(r['p2p'].get('failed', '?'))
        print(f"{bl:<25s} {r['patch_chars']:>6d} {str(r['strict_apply']):>7s} {str(r['normalized_apply']):>7s} {f2p_str:>10s} {p2p_str:>10s} {r['resolved']:>10s}")

out_path = OUT_DIR / "repair_smoke_eval.json"
out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
print(f"\nSaved: {out_path}")
