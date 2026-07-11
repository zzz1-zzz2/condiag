"""Step 8: Docker eval for workspace-based retry smoke (django-13513 + sympy-19954 + django-11099).

Produces repair_smoke_eval_matrix.csv at artifacts dir.
"""
import json, re, subprocess, time, csv
from pathlib import Path
from datasets import load_dataset

RUNS = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")
NORM_V3 = Path("/home/swelite/condiag/scripts_tmp/norm_v3_inline.py")

# ── config ──────────────────────────────────────────────────
CASES = {
    "django__django-13513": {
        "image": "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
        "is_pytest": False,  # uses tests/runtests.py
        "test_file": "tests/view_tests/tests/test_debug.py",
        "official_final": {
            "base_miniswe": "attempt_1",       # anchor: original agent patch
            "feedback_retry": "attempt_2",     # new workspace patch
            "broad_expansion": "attempt_2",    # new workspace patch
            "condiag_packet_only": "attempt_2", # new workspace patch
        },
    },
    "sympy__sympy-19954": {
        "image": "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest",
        "is_pytest": True,
        "test_file": "sympy/combinatorics/tests/test_perm_groups.py",
        "official_final": {
            "base_miniswe": "attempt_1",       # anchor: original agent patch (NOT workspace retry)
            "feedback_retry": "attempt_2",
            "broad_expansion": "attempt_2",
            "condiag_packet_only": "attempt_2",
        },
    },
    "django__django-11099": {
        "image": "docker.io/swebench/sweb.eval.x86_64.django_1776_django-11099:latest",
        "is_pytest": False,
        "test_file": "tests/model_forms/tests/test_uuid.py",
        "official_final": {
            "base_miniswe": "attempt_1",       # anchor: original agent patch
            # NOOP: ConDiag says NOOP → final = attempt_1, NOT attempt_2
            "condiag_packet_only": "attempt_1",
        },
        # feedback_retry and broad_expansion have no context_packet for NOOP case
    },
}

# ── test name converters ────────────────────────────────────
def swb_to_django(swb_name):
    """'test_name (module.path.ClassName)' -> 'module.path.ClassName.test_name'"""
    m = re.match(r"(.+?)\s+\((.+?)\)", swb_name.strip())
    if m:
        return f"{m.group(2)}.{m.group(1)}"
    return swb_name.strip()

def swb_to_pytest(swb_name):
    """'test_name (module.path)' -> 'path/to/test_file.py::test_name'"""
    m = re.match(r"(.+?)\s+\((.+?)\)", swb_name.strip())
    if m:
        test_name = m.group(1)
        mod = m.group(2)
        # Determine test file path from module path
        # e.g., sympy.combinatorics.tests.test_perm_groups -> sympy/combinatorics/tests/test_perm_groups.py
        py_path = "/".join(mod.split(".")) + ".py"
        return f"{py_path}::{test_name}"
    return swb_name.strip()


def load_test_data(iid):
    """Return (test_patch, f2p_list, p2p_list) from dataset."""
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


def run_django_tests(container, test_names, timeout=600):
    """Django test runner."""
    if not test_names:
        return {"passed": 0, "failed": 0, "error": 0, "output": ""}
    specs = " ".join(swb_to_django(t) for t in test_names)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        f"cd /testbed && python tests/runtests.py --verbosity=2 {specs} 2>&1"
    ], capture_output=True, text=True, timeout=timeout)
    output = r.stdout + r.stderr
    passed = 0; failed = 0; error = 0

    # Standard parse: Django's "Ran N test" footer
    m = re.search(r"Ran (\d+) test", output)
    total = int(m.group(1)) if m else 0
    m = re.search(r"FAILED.*failures=(\d+)", output)
    if m: failed = int(m.group(1))
    m = re.search(r"FAILED.*errors=(\d+)", output)
    if m: error = int(m.group(1))

    if total > 0:
        passed = total - failed - error
    else:
        # No "Ran N test" line — Django crashed before running any test
        # (ImportError / SyntaxError / missing module).
        n_expected = len(test_names)
        crash_type = "unknown"
        if "ImportError" in output or "ModuleNotFoundError" in output:
            crash_type = "IMPORT_ERROR"
            error = n_expected
        elif "SyntaxError" in output:
            crash_type = "SYNTAX_ERROR"
            error = n_expected
        elif "Traceback" in output:
            crash_type = "PRE_TEST_CRASH"
            error = n_expected
        elif r.returncode != 0:
            crash_type = "NONZERO_NO_OUTPUT"
            error = n_expected
        passed = 0
        failed = 0

        # Attach diagnostic excerpt (last 800 chars of the traceback)
        crash_excerpt = output[-800:] if len(output) > 800 else output
        return {
            "passed": 0, "failed": 0, "error": error,
            "output": output[-3000:],
            "crash_type": crash_type,
            "crash_excerpt": crash_excerpt,
        }

    return {"passed": max(0, passed), "failed": failed, "error": error, "output": output[-3000:]}


def run_pytest(container, test_names, test_file, timeout=600):
    """Pytest with -k filters."""
    if not test_names:
        return {"passed": 0, "failed": 0, "error": 0, "output": ""}
    k_expr = " or ".join(test_names)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        f"cd /testbed && python -m pytest {test_file} "
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


def eval_patch_in_docker(container, iid, patch_path, test_patch, f2p, p2p, cfg):
    """Apply patch + run tests in docker. Returns report dict."""
    report = {"patch_apply_ok": False, "normalized_apply_ok": False,
              "eval_used_patch": None, "normalization_status": ""}

    # 1. Apply test_patch
    Path("/tmp/_test.diff").write_text(test_patch, encoding="utf-8")
    subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"],
                   capture_output=True)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply /tmp/test.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        report["eval_error"] = f"test_patch_failed: {(r.stdout+r.stderr)[:200]}"
        return report

    # Install pytest for sympy
    if cfg["is_pytest"]:
        subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "pip install pytest -q 2>&1 | tail -2"
        ], capture_output=True, text=True, timeout=120)

    # Read patch
    if not patch_path.is_file():
        report["eval_error"] = "patch_not_found"
        return report

    patch_text = patch_path.read_text(encoding="utf-8").strip()
    # Strip markdown ``` fences
    lines = [l for l in patch_text.split("\n") if not l.startswith("```")]
    patch_text = "\n".join(lines)

    # Check if it looks like a diff
    if "--- " not in patch_text and "+++ " not in patch_text and "@@" not in patch_text:
        report["eval_error"] = "patch_not_a_diff"
        report["notes"] = "content is explanation text, not a unified diff"
        return report

    report["patch_chars"] = len(patch_text)

    # 2. Try strict apply
    Path("/tmp/_model.diff").write_text(patch_text, encoding="utf-8")
    subprocess.run(["docker", "cp", "/tmp/_model.diff", f"{container}:/tmp/model.diff"],
                   capture_output=True)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply --check /tmp/model.diff 2>&1 && git apply /tmp/model.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    report["strict_apply_ok"] = (r.returncode == 0)

    if report["strict_apply_ok"]:
        report["patch_apply_ok"] = True
        report["eval_used_patch"] = "strict_raw"
        report["normalization_status"] = "strict_passed"
    else:
        # 3. Try norm_v3
        report["normalization_reason"] = (r.stdout + r.stderr)[:300]

        # Reset workspace
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
        subprocess.run(["docker", "cp", str(NORM_V3), f"{container}:/tmp/norm.py"],
                       capture_output=True)
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
            f.get("applied") for f in norm_data if isinstance(f, dict)
        )
        diff_grew = len(diff_after) > len(diff_before)

        if diff_grew or any_applied:
            report["normalized_apply_ok"] = True
            report["patch_apply_ok"] = True
            report["eval_used_patch"] = "normalized_via_norm_v3"
            report["normalization_status"] = "norm_v3_applied"
        else:
            report["normalized_apply_ok"] = False
            report["patch_apply_ok"] = False
            report["eval_error"] = "patch_apply_failed_no_hunks_matched"
            report["normalization_status"] = "norm_v3_no_match"
            hunk_statuses = []
            for f in norm_data:
                if isinstance(f, dict):
                    for h in f.get("hunks", []):
                        hunk_statuses.append(h.get("status", "?"))
            report["notes"] = "; ".join(hunk_statuses)
            return report

    # 4. Run tests
    print(f"    Running F2P ({len(f2p)} tests)...", end=" ", flush=True)
    if cfg["is_pytest"]:
        f2p_res = run_pytest(container, f2p, cfg["test_file"])
    else:
        f2p_res = run_django_tests(container, f2p)
    report["fail_to_pass_passed"] = f2p_res["passed"]
    report["fail_to_pass_total"] = len(f2p)
    report["crash_type"] = f2p_res.get("crash_type", "")
    report["crash_excerpt"] = f2p_res.get("crash_excerpt", "")
    f2p_ok = (f2p_res["failed"] == 0 and f2p_res["error"] == 0
              and f2p_res["passed"] >= len(f2p))
    crash_label = f" [{f2p_res.get('crash_type', '')}]" if f2p_res.get("crash_type") else ""
    print(f"passed={f2p_res['passed']} failed={f2p_res['failed']} error={f2p_res['error']}{crash_label}")

    if report["patch_apply_ok"]:
        print(f"    Running P2P ({len(p2p)} tests)...", end=" ", flush=True)
        if cfg["is_pytest"]:
            p2p_res = run_pytest(container, p2p, cfg["test_file"], timeout=1200)
        else:
            p2p_res = run_django_tests(container, p2p, timeout=1200)
        report["pass_to_pass_passed"] = p2p_res["passed"]
        report["pass_to_pass_total"] = len(p2p)
        report["pass_to_pass_regressed"] = p2p_res["failed"] + p2p_res["error"]
        print(f"passed={p2p_res['passed']} failed={p2p_res['failed']} error={p2p_res['error']}")
    else:
        report["pass_to_pass_passed"] = 0
        report["pass_to_pass_total"] = len(p2p)
        report["pass_to_pass_regressed"] = 0

    p2p_ok = (report["pass_to_pass_regressed"] == 0)
    report["resolved"] = f2p_ok and p2p_ok

    return report


def main():
    all_reports = []

    for iid, cfg in CASES.items():
        print(f"\n{'='*60}")
        print(f"[{iid}]")
        test_patch, f2p, p2p = load_test_data(iid)
        print(f"  F2P: {len(f2p)}, P2P: {len(p2p)}, test_patch: {len(test_patch)} chars")

        for bl, final_attempt in cfg["official_final"].items():
            print(f"\n  --- {bl} (final={final_attempt}) ---")

            report = {
                "instance_id": iid, "baseline": bl,
                "official_counted": True,
                "final_source": final_attempt,
                "patch_exists": False,
                "has_patch": False,
                "patch_chars": 0,
                "patch_files": [],
                "strict_apply_ok": False,
                "normalized_apply_ok": False,
                "patch_apply_ok": False,
                "resolved": False,
                "fail_to_pass_passed": 0, "fail_to_pass_total": len(f2p),
                "pass_to_pass_passed": 0, "pass_to_pass_total": len(p2p),
                "pass_to_pass_regressed": 0,
                "eval_error": None, "notes": "",
                "crash_type": "", "crash_excerpt": "",
            }

            # Locate patch
            attempt_dir = RUNS / bl / iid / final_attempt
            patch_path = attempt_dir / "patch.diff"

            if not patch_path.is_file():
                report["eval_error"] = "patch_not_found"
                all_reports.append(report)
                print(f"    SKIP: no patch at {patch_path}")
                continue

            report["patch_exists"] = True
            report["patch_bytes"] = patch_path.stat().st_size

            # Read messages.json for file info
            msgs_path = attempt_dir / "messages.json"
            if msgs_path.is_file():
                try:
                    msgs = json.loads(msgs_path.read_text(encoding="utf-8"))
                    report["patch_files"] = msgs.get("files_written", [])
                    report["has_patch"] = msgs.get("has_patch", False)
                except Exception:
                    pass

            print(f"    patch: {report['patch_bytes']} bytes, files={report['patch_files']}")

            # Start docker container
            container = f"eval_{iid.split('-')[0][:4]}_{bl[:8]}".replace("__", "_")
            subprocess.run(["docker", "rm", "-f", container], capture_output=True)
            r = subprocess.run([
                "docker", "run", "--rm", "-d", "--name", container,
                "--platform", "linux/amd64", cfg["image"], "sleep", "3600",
            ], capture_output=True, text=True)
            if r.returncode != 0:
                report["eval_error"] = f"docker_start_failed: {r.stderr[:200]}"
                all_reports.append(report)
                print(f"    ERROR: docker start failed: {r.stderr[:200]}")
                continue

            try:
                eval_report = eval_patch_in_docker(
                    container, iid, patch_path, test_patch, f2p, p2p, cfg)
                report.update(eval_report)

                if report["resolved"]:
                    print(f"    -> RESOLVED!")
                elif report.get("eval_error"):
                    print(f"    -> ERROR: {report['eval_error'][:80]}")
                else:
                    f2p_str = f"{report['fail_to_pass_passed']}/{report['fail_to_pass_total']}"
                    p2p_str = str(report.get('pass_to_pass_regressed', '?'))
                    print(f"    -> FAILED (F2P={f2p_str}, P2P_reg={p2p_str})")

            finally:
                subprocess.run(["docker", "rm", "-f", container], capture_output=True)

            all_reports.append(report)

    # Print summary
    print(f"\n{'='*90}")
    print(f"SMOKE EVAL MATRIX")
    print(f"{'='*90}")
    header = (f"{'instance':<30s} {'baseline':<22s} {'final':<10s} "
              f"{'chars':>6s} {'apply':>6s} {'F2P':>8s} {'P2P_reg':>8s} {'result':>14s}")
    print(header)
    print("-" * 110)
    for r in all_reports:
        iid_short = r["instance_id"].split("__")[-1] if "__" in r["instance_id"] else r["instance_id"]
        f2p_str = f"{r['fail_to_pass_passed']}/{r['fail_to_pass_total']}"
        p2p_str = str(r.get('pass_to_pass_regressed', '?'))
        if r.get("eval_error"):
            result = f"ERR:{r['eval_error'][:30]}"
        elif r["resolved"]:
            result = "RESOLVED"
        elif r.get("crash_type"):
            result = r["crash_type"][:14]
        else:
            result = "FAILED"
        chars = r.get('patch_chars', 0)
        apply_str = "OK" if r["patch_apply_ok"] else "FAIL"
        print(f"{iid_short:<30s} {r['baseline']:<22s} {r['final_source']:<10s} "
              f"{chars:>6d} {apply_str:>6s} {f2p_str:>8s} {p2p_str:>8s} {result:>14s}")

    resolved = sum(1 for r in all_reports if r["resolved"])
    print(f"\nResolved: {resolved}/{len(all_reports)}")

    # Save CSV
    csv_path = OUT / "repair_smoke_eval_matrix.csv"
    fieldnames = [
        "instance_id", "baseline", "official_counted", "final_source",
        "patch_exists", "has_patch", "patch_bytes", "patch_chars", "patch_files",
        "strict_apply_ok", "normalized_apply_ok", "patch_apply_ok",
        "resolved", "fail_to_pass_passed", "fail_to_pass_total",
        "pass_to_pass_passed", "pass_to_pass_total", "pass_to_pass_regressed",
        "eval_error", "eval_used_patch", "normalization_status", "normalization_reason", "notes",
        "crash_type", "crash_excerpt",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in all_reports:
            # Flatten patch_files
            r_out = dict(r)
            if isinstance(r_out.get("patch_files"), list):
                r_out["patch_files"] = ";".join(r_out["patch_files"])
            w.writerow(r_out)
    print(f"Saved CSV: {csv_path}")

    # Also save JSON
    json_path = OUT / "repair_smoke_eval_matrix.json"
    json_path.write_text(json.dumps(all_reports, indent=2, ensure_ascii=False))
    print(f"Saved JSON: {json_path}")

    return all_reports

if __name__ == "__main__":
    main()
