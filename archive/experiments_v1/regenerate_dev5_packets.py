"""Regenerate Dev-5 context packets after refactoring.

Goal: verify the refactored pipeline produces correct output on real instances.
This test focuses on the core diagnosis_generator (the main refactoring target).

Usage: cd ~/condiag && python3 experiments/regenerate_dev5_packets.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")

ARTIFACTS = Path("/mnt/d/condiag-artifacts/condiag/v0")
INSTANCES = [
    "django__django-11820",
    "django__django-12125",
    "django__django-13513",
    "django__django-16454",
    "sympy__sympy-20428",
]

passed = 0
failed = 0

def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")

def _find_runtime_signals(inst):
    for root in [
        ARTIFACTS / "pilot50" / "case_bundles",
        ARTIFACTS / "case_bundles",
    ]:
        p = root / inst / "runtime_signals.json"
        if p.exists():
            return json.loads(p.read_text())
    return None

def _load_issue_text(inst):
    fw_path = ARTIFACTS / "failure_witness" / inst / "failure_witness.json"
    if fw_path.exists():
        fw = json.loads(fw_path.read_text())
        return fw.get("error_message", "")[:500]
    return ""

from condiag.diagnosis_generator import (
    generate, classify_deficiency, build_retrieval_plan, build_target_hints,
    _rule_a_regression_constraint, _rule_b_relocalization,
    _rule_c_interface_constraint, _rule_d_edit_scope,
    _rule_e_related_test, _rule_f_api_definition, _fallback_related_test,
    DiagnosisResult, DEFICIENCY_TYPES, RETRIEVAL_PLAN_TEMPLATES,
)

for inst in INSTANCES:
    short = inst.split("__", 1)[-1]
    print(f"\n{'='*60}")
    print(f"  {inst}")
    print(f"{'='*60}")

    rs = _find_runtime_signals(inst)
    if rs is None:
        print(f"  SKIP: no runtime_signals found")
        continue

    # Derive trigger signals from runtime_signals
    trigger_type = "PARTIAL_FIX_SUSPICION"
    trigger_reason = []
    edited = rs.get("edited_files_count", 0)
    viewed = rs.get("viewed_files_count", 0)
    test_runs = rs.get("test_runs_count", 0)
    changed = rs.get("changed_lines_total", 0)
    viewed_not_final = rs.get("viewed_but_not_final_files_count", 0)
    test_fails = rs.get("test_failures_count", 0)
    repeated_edit = rs.get("repeated_edit_pattern_detected", False)

    # Build trigger_reason from available signals
    bits = []
    if edited <= 2 and changed <= 20:
        bits.append(f"small patch ({edited}f, {changed}L)")
    if viewed >= 8:
        bits.append(f"high exploration ({viewed}f)")
    if test_runs >= 3:
        bits.append(f"{test_runs} test runs, {test_fails} failures")
    if viewed_not_final >= 3:
        bits.append(f"dropped {viewed_not_final} viewed files")
        trigger_type = "EVIDENCE_EDIT_MISMATCH"
    if repeated_edit:
        bits.append("repeated edit pattern")
    trigger_reason = [", ".join(bits)] if bits else ["no clear signal"]

    issue_text = _load_issue_text(inst)

    # ── Full diagnosis generation ──
    diagnosis = generate(trigger_type, trigger_reason, rs, issue_text)

    check("generates DiagnosisResult", isinstance(diagnosis, DiagnosisResult))
    check("deficiency_type non-empty", bool(diagnosis.context_deficiency_type))
    check("type in DEFICIENCY_TYPES",
          diagnosis.context_deficiency_type in DEFICIENCY_TYPES,
          diagnosis.context_deficiency_type)
    check("retrieval_plan non-empty", len(diagnosis.retrieval_plan) > 0)
    check("target_hints non-empty", len(diagnosis.target_hints) > 0)
    check("pathology non-empty", bool(diagnosis.pathology))
    check("5r_action non-empty", bool(diagnosis.primary_5r_action))
    check("retry_intent non-empty", bool(diagnosis.retry_intent))
    check("confidence > 0", diagnosis.confidence > 0)
    check("action_family in RECOVERY/CONTROL/NOOP",
          diagnosis.action_family in ("RECOVERY", "CONTROL", "NOOP", "ABSTAIN"))

    # ── Individual rule tests ──
    shape = {
        "edited_files_count": edited,
        "changed_lines_total": changed,
        "viewed_files_count": viewed,
        "viewed_but_not_final_count": viewed_not_final,
        "test_runs_count": test_runs,
        "test_failures_count": test_fails,
    }
    issue_kws = {}
    # Check classify_deficiency returns the same primary type
    primary, secondaries, conf = classify_deficiency(trigger_type, trigger_reason, rs, issue_text)
    check("classify_deficiency matches generate",
          primary == diagnosis.context_deficiency_type,
          f"classify={primary} generate={diagnosis.context_deficiency_type}")

    # ── Compare with existing diagnosis from v2_alpha ──
    old_path = ARTIFACTS / "context_packet_v2_alpha" / short / "recovery_report.json"
    if old_path.exists():
        old_report = json.loads(old_path.read_text())
        old_type = old_report.get("diagnosis", {}).get("context_deficiency_type", "")
        if old_type:
            check(f"type matches v2_alpha ({old_type})",
                  primary == old_type,
                  f"new={primary} old={old_type}")

print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed, {passed+failed} total")
print(f"{'='*50}")
sys.exit(0 if failed == 0 else 1)
