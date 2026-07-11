"""Step 7c: sympy-19954 x 4 baseline docker eval.

Sympy uses pytest. Tests are function-level (no class prefix).
Normalizer v3 applied equally to all baselines.
"""
import json, re, subprocess, time
from pathlib import Path

RUNS = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")

from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")

IID = "sympy__sympy-19954"
IMG = "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest"

f2p_raw = p2p_raw = test_patch = ""
for row in ds:
    if row.get("instance_id") == IID:
        f2p_raw = row.get("FAIL_TO_PASS") or []
        p2p_raw = row.get("PASS_TO_PASS") or []
        if isinstance(f2p_raw, str): f2p_raw = json.loads(f2p_raw)
        if isinstance(p2p_raw, str): p2p_raw = json.loads(p2p_raw)
        test_patch = row.get("test_patch") or ""
        break

print(f"Instance: {IID}")
print(f"Image: {IMG}")
print(f"F2P ({len(f2p_raw)}): {f2p_raw}")
print(f"P2P ({len(p2p_raw)} tests)")
print(f"test_patch: {len(test_patch)} chars")

# Write test_patch
Path("/tmp/_test.diff").write_text(test_patch, encoding="utf-8")

def run_pytest(container_name, test_names, timeout=600):
    """Run pytest with -k filters. Returns {ran, passed, failed, error, output}."""
    if not test_names:
        return {"ran": 0, "passed": 0, "failed": 0, "error": 0, "output": ""}
    # Use -k to filter by test name
    k_expr = " or ".join(test_names)
    r = subprocess.run([
        "docker", "exec", container_name, "bash", "-lc",
        f"cd /testbed && python -m pytest sympy/combinatorics/tests/test_perm_groups.py "
        f"-k '{k_expr}' -v --no-header --tb=short 2>&1"
    ], capture_output=True, text=True, timeout=timeout)
    output = r.stdout + r.stderr
    passed = 0; failed = 0; error = 0
    m = re.search(r"(\d+)\s+passed", output)
    if m: passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", output)
    if m: failed = int(m.group(1))
    m = re.search(r"(\d+)\s+errors?", output)
    if m: error = int(m.group(1))
    return {"passed": passed, "failed": failed, "error": error, "output": output[-3000:]}


# Step 0: Baseline — run F2P test WITHOUT model patch
print(f"\n{'='*60}")
print("BASELINE: Run F2P without model patch")

container = "sympy_base"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64", IMG, "sleep", "3600",
], capture_output=True)

# Apply test_patch
subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git apply /tmp/test.diff 2>&1"
], capture_output=True, text=True, timeout=30)
print(f"test_patch apply: rc={r.returncode}")

# Install pytest
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "pip install pytest -q 2>&1 | tail -3"
], capture_output=True, text=True, timeout=120)
print(f"pytest install: {r.stdout.strip()}")

# Run baseline F2P
baseline_f2p = run_pytest(container, f2p_raw)
print(f"Baseline F2P: passed={baseline_f2p['passed']} failed={baseline_f2p['failed']} error={baseline_f2p['error']}")
if baseline_f2p["failed"] == 0 and baseline_f2p["error"] == 0:
    print("WARNING: F2P test passes WITHOUT fix! Image may already have fix applied.")
subprocess.run(["docker", "rm", "-f", container], capture_output=True)

# Step 1: Eval each baseline
baselines = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]
all_reports = []

for bl in baselines:
    print(f"\n{'='*60}")
    print(f"[{bl}]")

    pp = RUNS / bl / IID / "attempt_2" / "patch.diff"
    report = {
        "instance_id": IID, "baseline": bl, "attempt": "attempt_2",
        "patch_exists": pp.is_file(),
        "strict_apply_ok": False, "recount_apply_ok": False,
        "newline_recount_apply_ok": False,
        "normalizer_used": False, "normalized_apply_ok": False,
        "eval_used_patch": None, "normalization_status": None,
        "normalization_reason": "", "used_gold": False,
        "patch_apply_ok": False, "resolved": False,
        "fail_to_pass_passed": 0, "fail_to_pass_total": len(f2p_raw),
        "pass_to_pass_passed": 0, "pass_to_pass_total": len(p2p_raw),
        "pass_to_pass_regressed": 0,
        "eval_error": None, "official_counted": True,
        "final_source": "norm_v3", "notes": "",
    }

    if not pp.is_file():
        report["eval_error"] = "patch_not_found"
        all_reports.append(report)
        print("  SKIP: no patch")
        continue

    raw = pp.read_text(encoding="utf-8").strip()
    lines = [l for l in raw.split("\n") if not l.startswith("```")]
    patch_text = "\n".join(lines)
    report["patch_chars"] = len(patch_text)
    print(f"  Patch: {len(patch_text)} chars")

    # Check if it looks like a diff
    if "--- " not in patch_text and "+++ " not in patch_text and "@@" not in patch_text:
        report["eval_error"] = "patch_not_a_diff"
        report["notes"] = "content is explanation text, not a unified diff"
        all_reports.append(report)
        print(f"  SKIP: not a diff (explanation text)")
        continue

    container = f"sym_{bl[:8]}"
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    subprocess.run([
        "docker", "run", "--rm", "-d", "--name", container,
        "--platform", "linux/amd64", IMG, "sleep", "3600",
    ], capture_output=True)

    try:
        # Apply test_patch
        subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
        r = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git apply /tmp/test.diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            report["eval_error"] = f"test_patch_failed: {(r.stdout+r.stderr)[:200]}"
            all_reports.append(report)
            continue

        # Install pytest
        subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "pip install pytest -q 2>&1 | tail -3"
        ], capture_output=True, text=True, timeout=120)

        # Step 1: Strict apply
        Path("/tmp/_model.diff").write_text(patch_text, encoding="utf-8")
        subprocess.run(["docker", "cp", "/tmp/_model.diff", f"{container}:/tmp/model.diff"], capture_output=True)
        r = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git apply --check /tmp/model.diff 2>&1 && git apply /tmp/model.diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        report["strict_apply_ok"] = (r.returncode == 0)
        print(f"  strict_apply: {'OK' if report['strict_apply_ok'] else 'FAIL'}")

        if not report["strict_apply_ok"]:
            # Normalize via norm_v3
            report["normalizer_used"] = True
            report["normalization_reason"] = (r.stdout + r.stderr)[:300]

            # Reset
            subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && git checkout -- . 2>&1 && git apply /tmp/test.diff 2>&1"
            ], capture_output=True)
            r_before = subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && git diff 2>&1"
            ], capture_output=True, text=True, timeout=30)
            diff_before = r_before.stdout.strip()

            # Run norm_v3
            subprocess.run(["docker", "cp",
                "/home/swelite/condiag/scripts_tmp/norm_v3_inline.py",
                f"{container}:/tmp/norm.py"], capture_output=True)
            r_norm = subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && python3 /tmp/norm.py /tmp/model.diff 2>&1"
            ], capture_output=True, text=True, timeout=60)

            try:
                norm_data = json.loads(r_norm.stdout)
            except json.JSONDecodeError:
                norm_data = []

            r_after = subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && git diff 2>&1"
            ], capture_output=True, text=True, timeout=30)
            diff_after = r_after.stdout.strip()

            any_applied = any(
                f.get("applied") for f in norm_data
                if isinstance(f, dict)
            )
            diff_grew = len(diff_after) > len(diff_before)

            if diff_grew or any_applied:
                report["normalized_apply_ok"] = True
                report["patch_apply_ok"] = True
                report["eval_used_patch"] = "normalized_via_norm_v3"
                report["normalization_status"] = "norm_v3_applied"
                print(f"  norm_apply: OK (diff {len(diff_before)} -> {len(diff_after)} chars)")
            else:
                report["normalized_apply_ok"] = False
                report["patch_apply_ok"] = False
                report["eval_error"] = "patch_apply_failed_no_hunks_matched"
                report["normalization_status"] = "norm_v3_no_match"
                # Still collect hunk info for notes
                hunk_statuses = []
                for f in norm_data:
                    if isinstance(f, dict):
                        for h in f.get("hunks", []):
                            hunk_statuses.append(h.get("status", "?"))
                report["notes"] = "; ".join(hunk_statuses)
                print(f"  norm_apply: FAIL ({report['notes']})")
                all_reports.append(report)
                continue
        else:
            report["patch_apply_ok"] = True
            report["eval_used_patch"] = "strict_raw"
            report["normalization_status"] = "strict_passed"

        # Run tests
        print(f"  Running F2P...", end=" ", flush=True)
        f2p_res = run_pytest(container, f2p_raw)
        report["fail_to_pass_passed"] = f2p_res["passed"]
        f2p_ok = (f2p_res["failed"] == 0 and f2p_res["error"] == 0
                  and f2p_res["passed"] >= len(f2p_raw))
        print(f"passed={f2p_res['passed']} failed={f2p_res['failed']} error={f2p_res['error']}")

        p2p_res = {"passed": 0, "failed": 0, "error": 0}
        if report["patch_apply_ok"]:
            print(f"  Running P2P ({len(p2p_raw)} tests)...", end=" ", flush=True)
            p2p_res = run_pytest(container, p2p_raw, timeout=1200)
            report["pass_to_pass_passed"] = p2p_res["passed"]
            report["pass_to_pass_regressed"] = p2p_res["failed"] + p2p_res["error"]
            print(f"passed={p2p_res['passed']} failed={p2p_res['failed']} error={p2p_res['error']}")

        p2p_ok = (p2p_res["failed"] == 0 and p2p_res["error"] == 0)
        if f2p_ok and p2p_ok:
            report["resolved"] = True

        if report["resolved"]:
            print(f"  -> RESOLVED!")
        elif f2p_ok:
            print(f"  -> PARTIAL (F2P ok, P2P regressed)")
        else:
            print(f"  -> FAILED")

    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    all_reports.append(report)

# Print matrix
print(f"\n{'='*90}")
print(f"EVAL MATRIX: {IID} x 4 baselines")
print(f"{'='*90}")
print(f"Baseline F2P (no fix): passed={baseline_f2p['passed']} failed={baseline_f2p['failed']}")
print()
header = (f"{'baseline':<25s} {'chars':>6s} {'strict':>7s} {'norm':>7s} "
          f"{'F2P':>8s} {'P2P_reg':>8s} {'result':>14s}")
print(header)
print("-" * 90)
for r in all_reports:
    f2p_str = f"{r['fail_to_pass_passed']}/{r['fail_to_pass_total']}"
    p2p_str = str(r['pass_to_pass_regressed'])
    if r.get("eval_error"):
        result = f"ERR:{r['eval_error'][:25]}"
    elif r["resolved"]:
        result = "RESOLVED"
    else:
        result = "FAILED"
    chars = r.get('patch_chars', 0)
    print(f"{r['baseline']:<25s} {chars:>6d} {str(r['strict_apply_ok']):>7s} "
          f"{str(r['normalized_apply_ok']):>7s} {f2p_str:>8s} {p2p_str:>8s} {result:>14s}")

resolved = sum(1 for r in all_reports if r["resolved"])
print(f"\nResolved: {resolved}/{len(all_reports)}")

# Save
out_path = OUT / "sympy19954_eval.json"
out_path.write_text(json.dumps(all_reports, indent=2, ensure_ascii=False))
print(f"Saved: {out_path}")
