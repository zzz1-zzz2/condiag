"""Inspect eval anomalies: capture raw test output for each anomaly case.

Anomalies to investigate:
  A1: django-13513 retry baselines — F2P 0/0/0 (empty test run)
  A2: sympy-19954 retry baselines — error=1 (systematic)
  A3: sympy-19954 base_miniswe — patch apply failed

For each, we:
  1. Start docker container
  2. Apply test_patch + model_patch
  3. Run the exact same test command, but save FULL raw output
  4. Check: test collection, target test name presence, tracebacks, import errors
"""
import json, re, subprocess, time
from pathlib import Path
from datasets import load_dataset

RUNS = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")
NORM_V3 = Path("/home/swelite/condiag/scripts_tmp/norm_v3_inline.py")

CASES_TO_INSPECT = [
    # A1: django-13513 retry baselines — empty F2P
    ("django__django-13513", "feedback_retry", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
     False, "tests/view_tests/tests/test_debug.py"),
    ("django__django-13513", "broad_expansion", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
     False, "tests/view_tests/tests/test_debug.py"),
    ("django__django-13513", "condiag_packet_only", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
     False, "tests/view_tests/tests/test_debug.py"),
    # A1 control: base_miniswe (which DID work) — to compare
    ("django__django-13513", "base_miniswe", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
     False, "tests/view_tests/tests/test_debug.py"),

    # A2: sympy-19954 retry baselines — error=1
    ("sympy__sympy-19954", "feedback_retry", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest",
     True, "sympy/combinatorics/tests/test_perm_groups.py"),
    ("sympy__sympy-19954", "broad_expansion", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest",
     True, "sympy/combinatorics/tests/test_perm_groups.py"),
    ("sympy__sympy-19954", "condiag_packet_only", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest",
     True, "sympy/combinatorics/tests/test_perm_groups.py"),

    # A3: sympy-19954 base_miniswe — patch apply fail
    ("sympy__sympy-19954", "base_miniswe", "attempt_2",
     "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest",
     True, "sympy/combinatorics/tests/test_perm_groups.py"),
]


def load_test_data(iid):
    ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
    for row in ds:
        if row.get("instance_id") == iid:
            tp = row.get("test_patch") or ""
            f2p = row.get("FAIL_TO_PASS") or []
            p2p = row.get("PASS_TO_PASS") or []
            if isinstance(f2p, str): f2p = json.loads(f2p)
            if isinstance(p2p, str): p2p = json.loads(p2p)
            return tp, f2p, p2p
    return "", [], []


def swb_to_django(swb_name):
    m = re.match(r"(.+?)\s+\((.+?)\)", swb_name.strip())
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    return swb_name.strip()


def inspect_one(iid, bl, attempt, image, is_pytest, test_file):
    """Run full inspection on one case. Returns dict with raw output."""
    label = f"{iid}/{bl}/{attempt}"
    print(f"\n{'='*70}")
    print(f"INSPECT: {label}")
    print(f"{'='*70}")

    result = {"label": label, "iid": iid, "baseline": bl, "attempt": attempt}

    test_patch, f2p, p2p = load_test_data(iid)
    result["f2p_expected"] = f2p
    result["p2p_expected_count"] = len(p2p)

    patch_path = RUNS / bl / iid / attempt / "patch.diff"
    if not patch_path.is_file():
        result["error"] = "patch_not_found"
        print(f"  PATCH NOT FOUND: {patch_path}")
        return result

    patch_text = patch_path.read_text(encoding="utf-8").strip()
    lines = [l for l in patch_text.split("\n") if not l.startswith("```")]
    patch_text = "\n".join(lines)
    result["patch_chars"] = len(patch_text)
    result["patch_bytes"] = patch_path.stat().st_size

    # Check if it looks like a diff
    has_diff_markers = "--- " in patch_text and "+++ " in patch_text
    result["looks_like_diff"] = has_diff_markers
    print(f"  Patch: {len(patch_text)} chars, looks_like_diff={has_diff_markers}")

    # Also read messages.json for context
    msgs_path = patch_path.parent / "messages.json"
    if msgs_path.is_file():
        msgs = json.loads(msgs_path.read_text(encoding="utf-8"))
        result["files_written"] = msgs.get("files_written", [])
        result["has_patch_flag"] = msgs.get("has_patch", None)
        resp = msgs.get("response", "")
        result["response_len"] = len(resp)
        # Check if response is code or explanation
        result["response_has_file_header"] = "### FILE:" in resp
        result["response_has_diff_markers"] = "--- " in resp or "@@" in resp
        print(f"  Messages: files={result['files_written']}, "
              f"response_len={result['response_len']}, "
              f"has_FILE_header={result['response_has_file_header']}, "
              f"has_diff_markers={result['response_has_diff_markers']}")

    # Start container
    container = f"insp_{iid.split('__')[0][:4]}_{bl[:8]}".replace("__", "_")
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    r = subprocess.run([
        "docker", "run", "--rm", "-d", "--name", container,
        "--platform", "linux/amd64", image, "sleep", "3600",
    ], capture_output=True, text=True)
    if r.returncode != 0:
        result["error"] = f"docker_start_failed: {r.stderr[:200]}"
        print(f"  DOCKER START FAILED: {r.stderr[:200]}")
        return result

    try:
        # 1. Apply test_patch
        Path("/tmp/_test.diff").write_text(test_patch, encoding="utf-8")
        subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"],
                       capture_output=True)
        r = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git apply /tmp/test.diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        result["test_patch_apply_ok"] = (r.returncode == 0)
        result["test_patch_apply_output"] = (r.stdout + r.stderr)[:500]
        print(f"  test_patch apply: {'OK' if result['test_patch_apply_ok'] else 'FAIL'}")

        # 2. Apply model patch
        Path("/tmp/_model.diff").write_text(patch_text, encoding="utf-8")
        subprocess.run(["docker", "cp", "/tmp/_model.diff", f"{container}:/tmp/model.diff"],
                       capture_output=True)
        r = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git apply --check /tmp/model.diff 2>&1 && git apply /tmp/model.diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        result["model_patch_strict_ok"] = (r.returncode == 0)
        result["model_patch_strict_output"] = (r.stdout + r.stderr)[:500]
        print(f"  model_patch strict apply: {'OK' if result['model_patch_strict_ok'] else 'FAIL'}")

        if not result["model_patch_strict_ok"]:
            # Try norm_v3
            # Reset
            subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && git checkout -- . 2>&1 && git apply /tmp/test.diff 2>&1"
            ], capture_output=True)
            r_before = subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && git diff 2>&1"
            ], capture_output=True, text=True, timeout=30)

            subprocess.run(["docker", "cp", str(NORM_V3), f"{container}:/tmp/norm.py"],
                           capture_output=True)
            r_norm = subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && python3 /tmp/norm.py /tmp/model.diff 2>&1"
            ], capture_output=True, text=True, timeout=60)
            result["norm_v3_output"] = r_norm.stdout[:2000]
            result["norm_v3_stderr"] = r_norm.stderr[:500]

            r_after = subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "cd /testbed && git diff 2>&1"
            ], capture_output=True, text=True, timeout=30)
            result["norm_v3_diff_applied"] = (len(r_after.stdout.strip()) > len(r_before.stdout.strip()))
            result["model_patch_applied"] = result["norm_v3_diff_applied"]
            print(f"  norm_v3 diff applied: {result['norm_v3_diff_applied']}")
        else:
            result["model_patch_applied"] = True
            result["norm_v3_diff_applied"] = None

        # 3. Install pytest if needed
        if is_pytest:
            subprocess.run([
                "docker", "exec", container, "bash", "-lc",
                "pip install pytest -q 2>&1 | tail -2"
            ], capture_output=True, text=True, timeout=120)

        # 4. Run F2P tests with FULL output capture
        if is_pytest:
            k_expr = " or ".join(f2p)
            cmd = (f"cd /testbed && python -m pytest {test_file} "
                   f"-k '{k_expr}' -v --tb=long 2>&1")
        else:
            specs = " ".join(swb_to_django(t) for t in f2p)
            cmd = (f"cd /testbed && python tests/runtests.py --verbosity=2 {specs} 2>&1")

        result["test_command"] = cmd
        r = subprocess.run([
            "docker", "exec", container, "bash", "-lc", cmd
        ], capture_output=True, text=True, timeout=600)
        full_output = r.stdout + r.stderr
        result["f2p_raw_output"] = full_output
        result["f2p_raw_output_len"] = len(full_output)
        result["f2p_exit_code"] = r.returncode

        # Parse
        if is_pytest:
            m = re.search(r"(\d+)\s+passed", full_output)
            result["f2p_pytest_passed"] = int(m.group(1)) if m else 0
            m = re.search(r"(\d+)\s+failed", full_output)
            result["f2p_pytest_failed"] = int(m.group(1)) if m else 0
            m = re.search(r"(\d+)\s+errors?", full_output)
            result["f2p_pytest_error"] = int(m.group(1)) if m else 0
        else:
            m = re.search(r"Ran (\d+) test", full_output)
            total = int(m.group(1)) if m else 0
            m = re.search(r"FAILED.*failures=(\d+)", full_output)
            fail = int(m.group(1)) if m else 0
            m = re.search(r"FAILED.*errors=(\d+)", full_output)
            err = int(m.group(1)) if m else 0
            result["f2p_django_ran"] = total
            result["f2p_django_failed"] = fail
            result["f2p_django_error"] = err
            result["f2p_django_passed"] = max(0, total - fail - err)

        # Diagnostics
        result["has_target_test_name"] = any(
            t.split("(")[0].strip() in full_output for t in f2p
        )
        result["has_traceback"] = "Traceback" in full_output
        result["has_syntax_error"] = "SyntaxError" in full_output
        result["has_import_error"] = "ImportError" in full_output
        result["has_collection_error"] = "ERROR collecting" in full_output or "error collecting" in full_output.lower()
        result["has_no_tests_ran"] = "no tests ran" in full_output.lower()
        result["has_django_settings_error"] = "DJANGO_SETTINGS_MODULE" in full_output or "django.core.exceptions.ImproperlyConfigured" in full_output

        print(f"  Results: "
              f"target_test={'YES' if result['has_target_test_name'] else 'NO'} "
              f"traceback={'YES' if result['has_traceback'] else 'NO'} "
              f"syntax={'YES' if result['has_syntax_error'] else 'NO'} "
              f"import_err={'YES' if result['has_import_error'] else 'NO'} "
              f"collection_err={'YES' if result['has_collection_error'] else 'NO'} "
              f"no_tests={'YES' if result['has_no_tests_ran'] else 'NO'} "
              f"django_settings={'YES' if result['has_django_settings_error'] else 'NO'}")

        # Print key excerpts
        if result["has_traceback"] or result["has_syntax_error"] or result["has_import_error"]:
            print(f"\n  --- RAW OUTPUT EXCERPT (last 2000 chars) ---")
            print(full_output[-2000:])

    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    return result


def main():
    all_results = []
    for args in CASES_TO_INSPECT:
        try:
            r = inspect_one(*args)
        except Exception as e:
            r = {"label": f"{args[0]}/{args[1]}/{args[2]}", "error": str(e)}
            print(f"  EXCEPTION: {e}")
        all_results.append(r)

    # Save full inspection
    out_path = OUT / "eval_anomaly_inspection.json"
    # Truncate raw outputs for JSON size
    for r in all_results:
        if "f2p_raw_output" in r and len(r["f2p_raw_output"]) > 5000:
            r["f2p_raw_output_head"] = r["f2p_raw_output"][:2000]
            r["f2p_raw_output_tail"] = r["f2p_raw_output"][-2000:]
            del r["f2p_raw_output"]

    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False))
    print(f"\n{'='*70}")
    print(f"Inspection saved: {out_path}")

    # Summary
    print(f"\nANOMALY SUMMARY:")
    for r in all_results:
        label = r.get("label", "?")
        err = r.get("error", "")
        if err:
            print(f"  {label}: ERROR={err[:80]}")
            continue
        applied = "applied" if r.get("model_patch_applied") else "NO_APPLY"
        trace = "TRACEBACK" if r.get("has_traceback") else ""
        syn = "SYNTAX_ERR" if r.get("has_syntax_error") else ""
        imp = "IMPORT_ERR" if r.get("has_import_error") else ""
        coll = "COLLECTION_ERR" if r.get("has_collection_error") else ""
        no_test = "NO_TESTS_RAN" if r.get("has_no_tests_ran") else ""
        django_err = "DJANGO_ERR" if r.get("has_django_settings_error") else ""
        target = "HAS_TARGET" if r.get("has_target_test_name") else "NO_TARGET"

        if is_pytest := (r.get("f2p_pytest_passed") is not None):
            stats = f"P={r['f2p_pytest_passed']} F={r['f2p_pytest_failed']} E={r['f2p_pytest_error']}"
        else:
            stats = f"R={r.get('f2p_django_ran',0)} F={r.get('f2p_django_failed',0)} E={r.get('f2p_django_error',0)}"

        flags = " ".join(f for f in [trace, syn, imp, coll, no_test, django_err, target] if f)
        print(f"  {label}: [{applied}] {stats} | {flags}")

    print("\nDone.")


if __name__ == "__main__":
    main()
