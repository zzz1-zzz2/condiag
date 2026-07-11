"""ConDiag baseline runner retry trigger (D4-2).

Four retry-style baselines (Feedback Retry / Broad Expansion / ConDiag
packet_only / ConDiag retry) share this module to decide whether attempt_2
should fire. Base mini-SWE does NOT call this (it's single-attempt control).

Hard rule: this module only reads runtime-visible signals. It must never read
gold_check / contextbench_metrics / official_eval / FAIL_TO_PASS / PASS_TO_PASS
/ resolved / file_cov / line_cov / EditLoc. Use `assert_no_leakage` to enforce.

Six-rule priority cascade (top match wins):

    1. HARD_FAILURE              exit_status in fatal set
    2. RUNTIME_VALIDATION_FAILURE regression-after-partial-fix pattern
    3. PATCH_SHAPE_ANOMALY        over-explore / over-edit
    4. EVIDENCE_EDIT_MISMATCH     viewed-evidence dropped from final context
    5. PARTIAL_FIX_SUSPICION      tiny patch + ran validation + still suspicious
    6. NO_TRIGGER                 nothing fired; ConDiag should not intervene

Calibrated against 4 seed cases (5th, django-13195, has no case_bundle yet):
    sympy-16597    -> RUNTIME_VALIDATION_FAILURE
    sympy-13877    -> PATCH_SHAPE_ANOMALY
    astropy-13398  -> EVIDENCE_EDIT_MISMATCH
    django-11400   -> PARTIAL_FIX_SUSPICION
    django-13195   -> NO_TRIGGER (expected; TODO validate once case_bundle built)
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


# ===== public schema =====

@dataclass
class RetryTriggerResult:
    should_retry: bool
    trigger_type: str            # one of the 6 trigger types above
    trigger_reason: list[str]    # human-readable bullets
    runtime_gap_status: str      # axis-2a gap label
    confidence: str              # "high" | "medium" | "low"
    evidence: dict               # numeric snapshot of inputs that fired
    alternative_trigger_types: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ===== thresholds (override via config arg) =====

DEFAULT_CONFIG = {
    "hard_failure_statuses": {
        "timeout", "Timeout",
        "format_error", "FormatError",
        "no_patch", "NoPatch",
        "patch_apply_failure", "PatchApplyFailure",
        "errored", "Errored",
        "error", "Error",
    },
    "runtime_validation_failure": {
        "test_failures_count_ge": 1,
        "test_runs_count_ge": 3,
        "git_checkout_count_ge": 2,
        "edited_files_count_le": 2,
    },
    "patch_shape_anomaly": {
        "scope_anomaly_score_ge": 2,        # mirror v0.2 scope_guard warning threshold
        "edited_files_count_ge": 5,
        "changed_lines_total_ge": 200,
        "repeated_edit_pattern_detected": True,
        "submitted_without_tests_and_edited_files_count_ge": 3,
    },
    "evidence_edit_mismatch": {
        "edited_but_not_viewed_files_count_ge": 1,   # strong signal
        "viewed_but_not_final_files_count_ge": 3,    # medium signal
    },
    "partial_fix_suspicion": {
        "edited_files_count_le": 2,
        "changed_lines_total_le": 50,
        "test_runs_count_ge": 1,
    },
}


# ===== forbidden (leakage guard) =====

FORBIDDEN_FIELDS_LOWER = {
    "gold_check", "gold_patch", "gold_context",
    "contextbench_metrics",
    "file_coverage", "file_cov",
    "symbol_coverage", "symbol_cov",
    "line_coverage", "line_cov",
    "editloc_recall", "editloc_precision",
    "auc_file", "redundancy_file",
    "official_eval",
    "fail_to_pass", "pass_to_pass",
    "resolved",
}


def assert_no_leakage(runtime_signals: dict) -> None:
    """Raise if runtime_signals contains evaluation-only / gold fields.

    Baseline retry_trigger must not consume ContextBench metrics or official
    SWE-bench eval labels — only runtime-visible agent artifacts.
    """
    leaked = [k for k in runtime_signals.keys() if k.lower() in FORBIDDEN_FIELDS_LOWER]
    if leaked:
        raise ValueError(
            f"retry_trigger leak guard violated: forbidden fields present: {leaked}"
        )


# ===== entry =====

def classify(runtime_signals: dict,
             patch_summary: Optional[dict] = None,
             config: Optional[dict] = None) -> RetryTriggerResult:
    """Run the 6-rule cascade. Returns RetryTriggerResult.

    `patch_summary` reserved for future use (e.g. parse_warnings from a stricter
    patch parser); currently unused. `config` deep-merges over DEFAULT_CONFIG.
    """
    assert_no_leakage(runtime_signals)

    cfg = _merge_config(DEFAULT_CONFIG, config or {})
    evidence = _snapshot_evidence(runtime_signals)

    # collect ALL hits for alternative_trigger_types
    all_hits: list[tuple[str, list[str], str]] = []  # (trigger_type, reasons, runtime_gap)

    # Rule 1: HARD_FAILURE
    hit, reasons = _check_hard_failure(runtime_signals, cfg["hard_failure_statuses"])
    if hit:
        all_hits.append(("HARD_FAILURE", reasons, "TERMINATION_FAILURE"))

    # Rule 2: RUNTIME_VALIDATION_FAILURE (regression pattern)
    hit, reasons = _check_runtime_validation_failure(
        runtime_signals, cfg["runtime_validation_failure"])
    if hit:
        all_hits.append(("RUNTIME_VALIDATION_FAILURE", reasons, "CONSTRAINT_CONFLICT"))

    # Rule 3: PATCH_SHAPE_ANOMALY
    hit, reasons = _check_patch_shape_anomaly(
        runtime_signals, cfg["patch_shape_anomaly"])
    if hit:
        all_hits.append(("PATCH_SHAPE_ANOMALY", reasons, "NOISY_OVERBROAD"))

    # Rule 4: EVIDENCE_EDIT_MISMATCH
    hit, reasons = _check_evidence_edit_mismatch(
        runtime_signals, cfg["evidence_edit_mismatch"])
    if hit:
        all_hits.append(("EVIDENCE_EDIT_MISMATCH", reasons, "SEEN_BUT_DROPPED"))

    # Rule 5: PARTIAL_FIX_SUSPICION
    hit, reasons = _check_partial_fix_suspicion(
        runtime_signals, cfg["partial_fix_suspicion"])
    if hit:
        all_hits.append(("PARTIAL_FIX_SUSPICION", reasons, "UNSEEN_CANDIDATE"))

    if not all_hits:
        return RetryTriggerResult(
            should_retry=False,
            trigger_type="NO_TRIGGER",
            trigger_reason=["no runtime-visible signal warrants retry"],
            runtime_gap_status="SUFFICIENT_OR_NOOP",
            confidence="high",
            evidence=evidence,
            alternative_trigger_types=[],
        )

    # priority cascade: HARD_FAILURE > RUNTIME_VALIDATION_FAILURE >
    # PATCH_SHAPE_ANOMALY > EVIDENCE_EDIT_MISMATCH > PARTIAL_FIX_SUSPICION
    priority = {
        "HARD_FAILURE": 1,
        "RUNTIME_VALIDATION_FAILURE": 2,
        "PATCH_SHAPE_ANOMALY": 3,
        "EVIDENCE_EDIT_MISMATCH": 4,
        "PARTIAL_FIX_SUSPICION": 5,
    }
    all_hits.sort(key=lambda h: priority[h[0]])
    top_type, top_reasons, top_gap = all_hits[0]
    alt_types = [h[0] for h in all_hits[1:]]

    confidence = _confidence_for(top_type)

    return RetryTriggerResult(
        should_retry=True,
        trigger_type=top_type,
        trigger_reason=top_reasons,
        runtime_gap_status=top_gap,
        confidence=confidence,
        evidence=evidence,
        alternative_trigger_types=alt_types,
    )


# ===== rule implementations =====

def _check_hard_failure(rs: dict, statuses: set) -> tuple[bool, list[str]]:
    status = (rs.get("exit_status") or "").strip()
    if status in statuses:
        return True, [f"exit_status={status!r} indicates hard failure"]
    return False, []


def _check_runtime_validation_failure(rs: dict, cfg: dict) -> tuple[bool, list[str]]:
    """Regression-after-partial-fix pattern.

    Simple `test_failures_count > 0` is NOT enough — astropy-13398 has 1 test
    failure but is correctly classified as EVIDENCE_EDIT_MISMATCH. The
    regression pattern requires agent cycling (test_runs>=3, git_checkout>=2)
    on a small patch (edited_files<=2) WITH parsed failures.
    """
    tfc = int(rs.get("test_failures_count") or 0)
    trc = int(rs.get("test_runs_count") or 0)
    gcc = int(rs.get("git_checkout_count") or 0)
    efc = int(rs.get("edited_files_count") or 0)

    if (tfc >= cfg["test_failures_count_ge"]
            and trc >= cfg["test_runs_count_ge"]
            and gcc >= cfg["git_checkout_count_ge"]
            and efc <= cfg["edited_files_count_le"]):
        reasons = [
            f"test_runs={trc}, git_checkout={gcc}, edited_files={efc} "
            f"— regression-after-partial-fix pattern",
            f"{tfc} failing test(s) parsed from local output",
        ]
        return True, reasons
    return False, []


def _check_patch_shape_anomaly(rs: dict, cfg: dict) -> tuple[bool, list[str]]:
    efc = int(rs.get("edited_files_count") or 0)
    clt = int(rs.get("changed_lines_total") or 0)
    rep = bool(rs.get("repeated_edit_pattern_detected"))
    swt = bool(rs.get("submitted_without_tests"))
    sas = int(rs.get("scope_anomaly_score") or 0)

    reasons: list[str] = []
    if sas >= cfg["scope_anomaly_score_ge"]:
        reasons.append(f"scope_anomaly_score={sas} >= {cfg['scope_anomaly_score_ge']}")
    if efc >= cfg["edited_files_count_ge"]:
        reasons.append(f"edited_files_count={efc} >= {cfg['edited_files_count_ge']}")
    if clt >= cfg["changed_lines_total_ge"]:
        reasons.append(f"changed_lines_total={clt} >= {cfg['changed_lines_total_ge']}")
    if rep and cfg["repeated_edit_pattern_detected"]:
        reasons.append("repeated_edit_pattern_detected=true")
    if swt and efc >= cfg["submitted_without_tests_and_edited_files_count_ge"]:
        reasons.append(f"submitted_without_tests with edited_files_count={efc}")

    return bool(reasons), reasons


def _check_evidence_edit_mismatch(rs: dict, cfg: dict) -> tuple[bool, list[str]]:
    """Strong: edited a file never viewed. Medium: >=3 viewed-dropped."""
    ebnv = int(rs.get("edited_but_not_viewed_files_count") or 0)
    vbnf = int(rs.get("viewed_but_not_final_files_count") or 0)

    if ebnv >= cfg["edited_but_not_viewed_files_count_ge"]:
        return True, [
            f"{ebnv} edited file(s) were never viewed (strong evidence-edit mismatch)"
        ]
    if vbnf >= cfg["viewed_but_not_final_files_count_ge"]:
        return True, [
            f"{vbnf} viewed file(s) dropped from final PATCH_CONTEXT "
            f"(>= {cfg['viewed_but_not_final_files_count_ge']} medium-strength mismatch)"
        ]
    return False, []


def _check_partial_fix_suspicion(rs: dict, cfg: dict) -> tuple[bool, list[str]]:
    efc = int(rs.get("edited_files_count") or 0)
    clt = int(rs.get("changed_lines_total") or 0)
    trc = int(rs.get("test_runs_count") or 0)
    swt = bool(rs.get("submitted_without_tests"))
    tfc = int(rs.get("test_failures_count") or 0)

    if (1 <= efc <= cfg["edited_files_count_le"]
            and clt <= cfg["changed_lines_total_le"]
            and trc >= cfg["test_runs_count_ge"]
            and not swt):
        suffix = (
            f" but {tfc} local test failure(s) remain — partial-fix candidate"
            if tfc > 0
            else f", {trc} validation run(s), no positive correctness signal — partial-fix suspicion"
        )
        return True, [
            f"small patch ({efc} file(s), {clt} lines){suffix}"
        ]
    return False, []


# ===== helpers =====

def _confidence_for(trigger_type: str) -> str:
    return {
        "HARD_FAILURE": "high",
        "RUNTIME_VALIDATION_FAILURE": "high",
        "PATCH_SHAPE_ANOMALY": "high",
        "EVIDENCE_EDIT_MISMATCH": "medium",
        "PARTIAL_FIX_SUSPICION": "medium",
    }.get(trigger_type, "low")


_EVIDENCE_FIELDS = [
    "exit_status",
    "test_runs_count", "test_failures_count", "git_checkout_count",
    "submitted_without_tests",
    "edited_files_count", "changed_files_count", "changed_lines_total",
    "repeated_edit_pattern_detected",
    "viewed_files_count", "viewed_but_not_final_files_count",
    "edited_but_not_viewed_files_count",
    "final_patch_context_files_count",
    "scope_anomaly_score",
    "api_calls",
]


def _snapshot_evidence(rs: dict) -> dict:
    return {k: rs.get(k) for k in _EVIDENCE_FIELDS if k in rs}


def _merge_config(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = {**out[k], **v}
        else:
            out[k] = v
    return out
