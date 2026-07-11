"""Build task6_alpha_e1_eval_matrix.csv from swebench reports."""
import csv
import hashlib
import json
import re
from pathlib import Path

OUT_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_official_eval_django12125")
REPORTS_DIR = OUT_ROOT / "reports"
DETAILED_ROOT = Path("/home/swelite/condiag/logs/run_evaluation")
MATRIX_CSV = OUT_ROOT / "task6_alpha_e1_eval_matrix.csv"
INSTANCE_ID = "django__django-12125"

PATCH_SOURCES = {
    "plain_rerun":            "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke/miniswe/plain_rerun/django__django-12125/final/patch.diff",
    "feedback_retry":         "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke/miniswe/feedback_retry/django__django-12125/final/patch.diff",
    "broad_expansion":        "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke_rerun/miniswe/broad_expansion/django__django-12125/final/patch.diff",
    "condiag_retry_v2_alpha": "/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke_rerun/miniswe/condiag_retry_v2_alpha/django__django-12125/final/patch.diff",
}

BASELINES = ["plain_rerun", "feedback_retry", "broad_expansion", "condiag_retry_v2_alpha"]

rows = []
for bl in BASELINES:
    patch_path = Path(PATCH_SOURCES[bl])
    patch_text = patch_path.read_text(encoding="utf-8")
    patch_chars = len(patch_text)
    changed_files = sorted(set(re.findall(r"^diff --git a/(\S+) b/\S+$", patch_text, re.MULTILINE)))

    summary_path = REPORTS_DIR / f"condiag-task6-alpha-e1-{bl}.{bl}.json"
    detailed_path = DETAILED_ROOT / bl / f"condiag-task6-alpha-e1-{bl}" / INSTANCE_ID / "report.json"

    summary = json.loads(summary_path.read_text())
    detailed = json.loads(detailed_path.read_text())[INSTANCE_ID]

    ts = detailed["tests_status"]
    f2p = ts["FAIL_TO_PASS"]
    p2p = ts["PASS_TO_PASS"]
    f2p_passed = len(f2p["success"])
    f2p_total = len(f2p["success"]) + len(f2p["failure"])
    p2p_passed = len(p2p["success"])
    p2p_regressed = len(p2p["failure"])
    p2p_total = len(p2p["success"]) + len(p2p["failure"])

    if detailed["resolved"]:
        eval_status = "resolved"
    elif not detailed["patch_successfully_applied"]:
        eval_status = "patch_apply_failed"
    elif f2p_passed < f2p_total and p2p_regressed == 0:
        eval_status = "f2p_incomplete_no_regression"
    elif f2p_passed == f2p_total and p2p_regressed > 0:
        eval_status = "f2p_pass_p2p_regression"
    else:
        eval_status = "other_unresolved"

    rows.append({
        "instance_id": INSTANCE_ID,
        "baseline": bl,
        "patch_path": str(patch_path),
        "patch_chars": patch_chars,
        "changed_files": ",".join(changed_files),
        "changed_files_count": len(changed_files),
        "patch_apply_ok": detailed["patch_successfully_applied"],
        "resolved": detailed["resolved"],
        "fail_to_pass_passed": f2p_passed,
        "fail_to_pass_total": f2p_total,
        "pass_to_pass_passed": p2p_passed,
        "pass_to_pass_regressed": p2p_regressed,
        "pass_to_pass_total": p2p_total,
        "eval_status": eval_status,
        "eval_report_path": str(detailed_path),
        "eval_summary_path": str(summary_path),
        "method_version": "v1",
        "context_packet_version": "v2_alpha" if bl == "condiag_retry_v2_alpha" else ("not_used" if bl in ("plain_rerun", "feedback_retry") else "broad_v0"),
        "failure_witness_version": "v1" if bl != "plain_rerun" else "not_used",
        "api_navigation_version": "v1" if bl == "condiag_retry_v2_alpha" else "not_used",
        "eval_version": "swebench_4.1.0_verified_offline",
        "retry_runner_version": "host_agent_retry_runner_v0_with_v2alpha_mapping_r0_observability",
        "plan_version": "plan_v1.0_post_validation",
        "forensic_audit_version": "task6_alpha_forensic_v1",
        "e1_eval_version": "task6_alpha_e1_v1",
    })

with MATRIX_CSV.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

print(f"Wrote {MATRIX_CSV}")
print()
print(f"{'baseline':30s} {'resolved':9s} {'F2P':8s} {'P2P':12s} {'eval_status':30s}")
for r in rows:
    f2p_str = f"{r['fail_to_pass_passed']}/{r['fail_to_pass_total']}"
    p2p_str = f"{r['pass_to_pass_passed']}/{r['pass_to_pass_total']} ({r['pass_to_pass_regressed']}regr)"
    print(f"  {r['baseline']:28s} {str(r['resolved']):9s} {f2p_str:8s} {p2p_str:14s} {r['eval_status']}")
