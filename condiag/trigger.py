"""ConDiag trigger — automatic trigger classification from runtime_signals.

Layered triggers per design拍板 1 (2026-06-27):
    Trigger-0: hard failure (timeout, format error, patch apply fail, no patch, runtime error)
    Trigger-1: validation failure (agent self-ran tests + ConDiag visible failures)
    Trigger-2: patch-shape anomaly / evidence-edit mismatch / partial-fix suspicion

v0 implemented here: Trigger-1, Trigger-2 (no Trigger-0; Agent wrapper handles
hard failures before ConDiag enters).

Output: TriggerResult.
"""
from __future__ import annotations

from typing import List

from .schemas import PathologyTaxonomy, RuntimeSignals, TriggerResult
from .scope_guard import score_scope


# ===== helpers =====

def _has_runtime_validation_failure(rs: RuntimeSignals) -> tuple[bool, list[str]]:
    """Return (any_failure, reasons)."""
    reasons: list[str] = []
    if rs.test_failures_count > 0:
        reasons.append(f"{rs.test_failures_count} failing test(s) parsed from local output")
    if rs.submitted_without_tests is False and rs.test_runs_count > 0 and rs.test_failures_count == 0:
        # agent ran tests, none parsed as failing — but exit_status != resolved
        # this is weak; we still surface it but do not claim hard failure
        pass
    if rs.git_checkout_count >= 2 and rs.test_runs_count >= 3:
        reasons.append(
            f"agent cycling: git_checkout_count={rs.git_checkout_count} "
            f"with test_runs_count={rs.test_runs_count} indicates repeated backtracking"
        )
    return (len(reasons) > 0), reasons


def _has_evidence_edit_mismatch(rs: RuntimeSignals) -> tuple[bool, float, list[str]]:
    """EXPLORE_OK_EDIT_MISALIGNED signal.

    Strong: edited a file that was never viewed (edited_but_not_viewed > 0).
    Medium: many viewed files (>=3) dropped from final PATCH_CONTEXT.
    Weak/none: 1-2 viewed-but-not-final is normal and does NOT trigger.
    """
    reasons: list[str] = []
    confidence = 0.0
    if rs.edited_but_not_viewed_files_count > 0:
        reasons.append(
            f"{rs.edited_but_not_viewed_files_count} edited file(s) were never viewed "
            f"(strong evidence-edit mismatch)"
        )
        confidence = 0.7
    elif rs.viewed_but_not_final_files_count >= 3:
        reasons.append(
            f"{rs.viewed_but_not_final_files_count} viewed file(s) dropped from final PATCH_CONTEXT "
            f"(>= 3 medium-strength mismatch)"
        )
        confidence = 0.6
    return (confidence > 0, confidence, reasons)


def _has_partial_fix_suspicion(rs: RuntimeSignals, scope_score: int) -> tuple[bool, float, list[str]]:
    """UNDER_EDIT_PARTIAL_FIX signal: tiny edit + agent ran some validation
    but no positive correctness signal.

    Heuristic:
      edited_files <= 2 AND changed_lines <= 50 AND
      test_runs >= 1 AND scope_anomaly_score <= 1 (no over-edit) AND
      not submitted_without_tests
    Stronger confidence if test_failures_count > 0.
    """
    reasons: list[str] = []
    confidence = 0.0
    if (
        1 <= rs.edited_files_count <= 2
        and rs.changed_lines_total <= 50
        and rs.test_runs_count >= 1
        and not rs.submitted_without_tests
        and scope_score <= 1
    ):
        if rs.test_failures_count > 0:
            confidence = 0.6
            reasons.append(
                f"small patch ({rs.edited_files_count} file(s), "
                f"{rs.changed_lines_total} lines) but {rs.test_failures_count} local "
                f"test failures remain — partial-fix candidate"
            )
        else:
            confidence = 0.5
            reasons.append(
                f"small patch ({rs.edited_files_count} file(s), "
                f"{rs.changed_lines_total} lines), {rs.test_runs_count} validation run(s), "
                f"no positive correctness signal — partial-fix suspicion (low confidence)"
            )
    return (confidence > 0, confidence, reasons)


def _has_regression_signal(rs: RuntimeSignals) -> tuple[bool, float, list[str]]:
    """REGRESSION_AFTER_PARTIAL_FIX signal.

    Per design:
      test_runs >= 3 + git_checkout >= 2 + edited_files <= 2
      + visible target fixes + visible regressions
    """
    reasons: list[str] = []
    confidence = 0.0
    if (
        rs.test_runs_count >= 3
        and rs.git_checkout_count >= 2
        and rs.edited_files_count <= 2
    ):
        # Stronger when there are parsed failures + agent cycling
        if rs.test_failures_count > 0:
            confidence = 0.88
        else:
            confidence = 0.75
        reasons.append(
            f"test_runs={rs.test_runs_count}, git_checkout={rs.git_checkout_count}, "
            f"edited_files={rs.edited_files_count} — regression-after-partial-fix pattern"
        )
        return True, confidence, reasons
    return False, 0.0, reasons


# ===== main =====

def classify(rs: RuntimeSignals, taxonomy: PathologyTaxonomy) -> TriggerResult:
    """Run all trigger detectors and produce a TriggerResult.

    The result lists candidate pathologies (with confidence_runtime) derived
    from runtime signals alone. It is later reconciled with manual_diagnosis
    by the dry-run report.
    """
    res = TriggerResult(instance_id=rs.instance_id)

    # 1. Scope Guard (Trigger-2)
    sg = score_scope(rs)
    res.scope_anomaly_score = sg.score
    res.scope_anomaly_threshold_warning = sg.threshold_warning
    res.scope_anomaly_threshold_strong = sg.threshold_strong
    res.scope_signals = sg.to_dict()

    # 2. Runtime validation signals (Trigger-1)
    val_fail, val_reasons = _has_runtime_validation_failure(rs)
    reg_signal, reg_conf, reg_reasons = _has_regression_signal(rs)
    res.runtime_validation_signals = {
        "test_runs_count": rs.test_runs_count,
        "test_failures_count": rs.test_failures_count,
        "test_failures": rs.test_failures,
        "git_checkout_count": rs.git_checkout_count,
        "submitted_without_tests": rs.submitted_without_tests,
        "runtime_validation_failure_detected": val_fail,
        "regression_signal_detected": reg_signal,
        "regression_confidence": reg_conf,
        "regression_reasons": reg_reasons,
    }

    # 3. Evidence-edit mismatch (Trigger-2)
    mismatch, mismatch_conf, mismatch_reasons = _has_evidence_edit_mismatch(rs)

    # 4. Partial-fix suspicion (Trigger-2)
    partial, partial_conf, partial_reasons = _has_partial_fix_suspicion(rs, sg.score)

    # Build candidate list — every detector that fires contributes a candidate.
    # Priority for "top candidate" is by confidence_runtime descending so the
    # most specific / strongest signal wins. Each candidate carries full info
    # so downstream consumers can still see all matches.
    candidates: list[dict] = []

    if reg_signal:
        candidates.append({
            "pathology": "REGRESSION_AFTER_PARTIAL_FIX",
            "trigger_type": "RUNTIME_VALIDATION_FAILURE",
            "trigger_layer": "Trigger-1",
            "confidence_runtime": reg_conf,
            "action_family": "RECOVERY",
            "5r_action": "RECONCILE",
            "reasons": reg_reasons + val_reasons,
        })

    if sg.triggered_strong:
        candidates.append({
            "pathology": "OVER_EXPLORE_OVER_EDIT",
            "trigger_type": "PATCH_SHAPE_ANOMALY",
            "trigger_layer": "Trigger-2",
            "confidence_runtime": 0.9 if sg.score >= 4 else 0.75,
            "action_family": "GUARD",
            "5r_action": "RESTRAIN",
            "reasons": [f"scope_anomaly_score={sg.score} (>= {sg.threshold_strong} strong threshold)"],
        })
    elif sg.triggered_warning:
        candidates.append({
            "pathology": "OVER_EXPLORE_OVER_EDIT",
            "trigger_type": "PATCH_SHAPE_ANOMALY",
            "trigger_layer": "Trigger-2",
            "confidence_runtime": 0.5,
            "action_family": "GUARD",
            "5r_action": "RESTRAIN",
            "reasons": [f"scope_anomaly_score={sg.score} (>= {sg.threshold_warning} warning threshold)"],
        })

    if mismatch:
        candidates.append({
            "pathology": "EXPLORE_OK_EDIT_MISALIGNED",
            "trigger_type": "EVIDENCE_EDIT_MISMATCH",
            "trigger_layer": "Trigger-2",
            "confidence_runtime": mismatch_conf,
            "action_family": "RECOVERY",
            "5r_action": "REHYDRATE",
            "reasons": mismatch_reasons,
        })

    if partial:
        candidates.append({
            "pathology": "UNDER_EDIT_PARTIAL_FIX",
            "trigger_type": "PARTIAL_FIX_SUSPICION",
            "trigger_layer": "Trigger-2",
            "confidence_runtime": partial_conf,
            "action_family": "RECOVERY",
            "5r_action": "RETRIEVE",
            "reasons": partial_reasons,
        })

    # Sort candidates by confidence descending (stable: ties keep insertion order)
    candidates.sort(key=lambda c: -c["confidence_runtime"])

    if candidates:
        top = candidates[0]
        res.triggered = True
        res.trigger_type = top["trigger_type"]
        res.trigger_layer = top["trigger_layer"]
        res.inferred_pathology_candidates = candidates
        res.inferred_action_family = top["action_family"]
        res.confidence_runtime = top["confidence_runtime"]
        # Trigger reasons = top candidate's reasons + any additional validation reasons
        reasons = list(top["reasons"])
        if val_fail:
            for r in val_reasons:
                if r not in reasons:
                    reasons.append(r)
        res.trigger_reasons = reasons
    else:
        # No trigger — ConDiag should not fire
        res.triggered = False
        res.trigger_type = "NO_TRIGGER"
        res.trigger_layer = None
        res.inferred_pathology_candidates = []
        res.inferred_action_family = "NOOP"
        res.confidence_runtime = 0.0
        res.trigger_reasons = ["no runtime failure signal matched v0 thresholds"]

    return res
