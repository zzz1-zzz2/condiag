"""Step 7a: Patch apply recovery pipeline + docker eval.

For each baseline:
  1. Clean markdown wrapping
  2. Try strict git apply
  3. If fail, run norm_v3 (deletion-line-based normalizer)
  4. Apply normalized changes, produce git diff
  5. Run Django F2P + P2P tests
  6. Output patch_apply_report.json

Lightweight options (--recount, --whitespace, trailing newline) tested and don't work.
--recount is not a valid git-apply flag. norm_v3 is the only working approach.
"""
import json, re, subprocess, time
from pathlib import Path

RUNS = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")

# ---- Load instance metadata ----
from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")

INSTANCES = {}
for row in ds:
    iid = row.get("instance_id", "")
    if iid in ("django__django-13513", "sympy__sympy-19954", "django__django-11099"):
        f2p = row.get("FAIL_TO_PASS") or []
        p2p = row.get("PASS_TO_PASS") or []
        if isinstance(f2p, str): f2p = json.loads(f2p)
        if isinstance(p2p, str): p2p = json.loads(p2p)
        img = row.get("image_name") or ""
        if not img:
            id_docker = iid.replace("__", "_1776_")
            img = f"docker.io/swebench/sweb.eval.x86_64.{id_docker}:latest".lower()
        INSTANCES[iid] = {
            "image": img, "f2p": f2p, "p2p": p2p,
            "test_patch": row.get("test_patch") or "",
        }


def swb_to_django(test_id: str) -> str:
    """'test_name (module.path.ClassName)' -> 'module.path.ClassName.test_name'"""
    m = re.match(r"(.+?)\s+\((.+?)\)", test_id)
    if not m: return test_id
    return f"{m.group(2).strip()}.{m.group(1).strip()}"


def run_django_tests(container_name, test_list, timeout=600):
    if not test_list: return {"ran": 0, "failed": 0, "error": 0, "output": ""}
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


def eval_one(instance_id, baseline, patch_path, inst_info):
    """Run full eval for one (instance, baseline) pair. Returns report dict."""
    inst = inst_info[instance_id]
    report = {
        "instance_id": instance_id,
        "baseline": baseline,
        "attempt": "attempt_2",
        "patch_exists": patch_path.is_file(),
        "strict_apply_ok": False,
        "recount_apply_ok": False,  # --recount not a valid git-apply flag
        "newline_recount_apply_ok": False,
        "normalizer_used": False,
        "normalized_apply_ok": False,
        "eval_used_patch": None,
        "normalization_status": None,
        "normalization_reason": "",
        "used_gold": False,
        "patch_apply_ok": False,
        "resolved": False,
        "fail_to_pass_passed": 0,
        "fail_to_pass_total": len(inst["f2p"]),
        "pass_to_pass_passed": 0,
        "pass_to_pass_total": len(inst["p2p"]),
        "pass_to_pass_regressed": 0,
        "eval_error": None,
        "official_counted": True,
        "final_source": "norm_v3",
        "notes": "",
    }

    if not patch_path.is_file():
        report["eval_error"] = "patch_not_found"
        return report

    raw = patch_path.read_text(encoding="utf-8").strip()
    lines = [l for l in raw.split("\n") if not l.startswith("```")]
    patch_text = "\n".join(lines)
    report["patch_chars"] = len(patch_text)

    f2p_tests = [swb_to_django(t) for t in inst["f2p"]]
    p2p_tests = [swb_to_django(t) for t in inst["p2p"]]

    container = f"ev_{instance_id[:8]}_{baseline[:6]}".replace("__", "_").replace(".", "")
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
    subprocess.run([
        "docker", "run", "--rm", "-d", "--name", container,
        "--platform", "linux/amd64", inst["image"], "sleep", "3600",
    ], capture_output=True)

    try:
        # Apply test_patch
        test_patch = inst["test_patch"]
        Path("/tmp/_test.diff").write_text(test_patch, encoding="utf-8")
        subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
        r = subprocess.run([
            "docker", "exec", container, "bash", "-lc",
            "cd /testbed && git apply /tmp/test.diff 2>&1"
        ], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            report["eval_error"] = f"test_patch_apply_failed: {(r.stdout+r.stderr)[:200]}"
            return report

        # Step 1: Strict apply
        Path("/tmp/_model.diff").write_text(patch_text, encoding="utf-8")
        subprocess.run(["docker", "cp", "/tmp/_model.diff", f"{container}:/tmp/model.diff"], capture_output=True)
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
            # Step 2: Normalize via norm_v3
            report["normalizer_used"] = True
            report["normalization_reason"] = (r.stdout + r.stderr)[:300]

            # Reset to test_patch state
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

            # Parse normalizer report
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

            if diff_grew:
                report["normalized_apply_ok"] = True
                report["patch_apply_ok"] = True
                report["eval_used_patch"] = "normalized_via_norm_v3"
                report["normalization_status"] = "norm_v3_applied"

                # Generate clean normalized diff
                norm_diff = diff_after.replace(diff_before, "").strip()
                if norm_diff:
                    Path("/tmp/_norm.diff").write_text(norm_diff, encoding="utf-8")
                    subprocess.run(["docker", "cp", "/tmp/_norm.diff", f"{container}:/tmp/norm.diff"], capture_output=True)
            elif any_applied:
                report["normalized_apply_ok"] = True
                report["patch_apply_ok"] = True
                report["eval_used_patch"] = "normalized_via_norm_v3_inline"
                report["normalization_status"] = "norm_v3_inline_edit"
            else:
                report["normalized_apply_ok"] = False
                report["patch_apply_ok"] = False
                report["eval_error"] = "patch_apply_failed_no_hunks_matched"
                report["normalization_status"] = "norm_v3_no_match"
                report["notes"] = "; ".join(
                    f"{h.get('status','?')}" for f in norm_data
                    if isinstance(f, dict)
                    for h in f.get("hunks", [{}])
                )
                return report

        # Step 3: Run tests
        f2p_res = run_django_tests(container, f2p_tests)
        report["fail_to_pass_passed"] = f2p_res["ran"] - f2p_res["failed"] - f2p_res["error"]

        f2p_all_pass = (f2p_res["failed"] == 0 and f2p_res["error"] == 0
                        and f2p_res["ran"] >= len(f2p_tests))

        p2p_res = {"ran": 0, "failed": 0, "error": 0}
        if report["patch_apply_ok"]:
            p2p_res = run_django_tests(container, p2p_tests, timeout=1200)
        report["pass_to_pass_passed"] = p2p_res["ran"] - p2p_res["failed"] - p2p_res["error"]
        report["pass_to_pass_regressed"] = p2p_res["failed"] + p2p_res["error"]

        p2p_no_regression = (p2p_res["failed"] == 0 and p2p_res["error"] == 0)

        if f2p_all_pass and p2p_no_regression:
            report["resolved"] = True

        report["eval_error"] = None

    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)

    return report


def main():
    # Phase 1: django-13513 x 4
    iid = "django__django-13513"
    print(f"Step 7a: Patch apply recovery + docker eval")
    print(f"Instance: {iid}")
    print(f"Image: {INSTANCES[iid]['image']}")
    print(f"F2P: {len(INSTANCES[iid]['f2p'])} tests")
    print(f"P2P: {len(INSTANCES[iid]['p2p'])} tests")
    print()

    baselines = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]
    all_reports = []

    for bl in baselines:
        print(f"[{bl}] ", end="", flush=True)
        pp = RUNS / bl / iid / "attempt_2" / "patch.diff"
        report = eval_one(iid, bl, pp, INSTANCES)
        all_reports.append(report)

        status = (
            "RESOLVED" if report["resolved"]
            else "APPLY_FAIL" if report.get("eval_error")
            else "FAILED"
        )
        print(f"strict={report['strict_apply_ok']} norm={report['normalized_apply_ok']} "
              f"apply={report['patch_apply_ok']} F2P={report['fail_to_pass_passed']}/{report['fail_to_pass_total']} "
              f"P2P_reg={report['pass_to_pass_regressed']} -> {status}")

    # Save reports
    out_path = OUT / "patch_apply_report.json"
    out_path.write_text(json.dumps(all_reports, indent=2, ensure_ascii=False))
    print(f"\nReports saved: {out_path}")

    # Print matrix
    print(f"\n{'='*90}")
    print("EVAL MATRIX: django-13513 x 4 baselines")
    print(f"{'='*90}")
    header = (f"{'baseline':<25s} {'strict':>7s} {'norm':>7s} {'apply':>7s} "
              f"{'F2P':>8s} {'P2P_reg':>8s} {'result':>12s}")
    print(header)
    print("-" * 90)
    for r in all_reports:
        f2p_str = f"{r['fail_to_pass_passed']}/{r['fail_to_pass_total']}"
        p2p_str = str(r['pass_to_pass_regressed'])
        if r.get("eval_error"):
            result = f"ERR:{r['eval_error'][:20]}"
        elif r["resolved"]:
            result = "RESOLVED"
        else:
            result = "FAILED"
        print(f"{r['baseline']:<25s} {str(r['strict_apply_ok']):>7s} "
              f"{str(r['normalized_apply_ok']):>7s} {str(r['patch_apply_ok']):>7s} "
              f"{f2p_str:>8s} {p2p_str:>8s} {result:>12s}")

    resolved = sum(1 for r in all_reports if r["resolved"])
    print(f"\nResolved: {resolved}/{len(all_reports)}")

    return all_reports


if __name__ == "__main__":
    main()
