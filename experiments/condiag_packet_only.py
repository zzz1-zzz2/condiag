"""ConDiag packet_only baseline (D4-7).

This is the ONLY intervention baseline allowed to import ConDiag core
retrieval machinery. It produces typed/5R/diagnosis-style artifacts:

  - intervention/selected_evidence.json
  - intervention/recovery_report.json
  - intervention/executed_actions.json
  - intervention/context_packet.md   (ConDiag-flavored, with diagnosis section)
  - intervention/retry_trigger_result.json

Pipeline (v0):
  1. attempt_1/runtime_signals.json -> RuntimeSignals
  2. experiments.retry_trigger.classify(rs) -> RetryTriggerResult
     (uses experiments/retry_trigger.py — same trigger as other baselines)
  3. synthesize_manual_diagnosis(trigger_result, rs) -> ManualDiagnosis
     (rule-based mapping trigger_type -> pathology / 5r_action / retrieval_plan)
  4. diagnosis_normalizer.normalize(md, taxonomy) -> NormalizedDiagnosis
  5. Repo resolution (likely fails in v0 — no repo mounted)
  6. If repo OK: retrieval_executor.execute_plan + evidence_selector.select
     If repo missing: empty action_results + carryover-only selected_evidence
  7. context_packet_builder.build_context_packet_md -> packet
  8. Write artifacts

Forbid:
  - reading gold_check / gold_context
  - reading contextbench_metrics / official_eval / file_cov / line_cov / editloc
  - writing non-ConDiag-flavored packets

These are enforced by:
  - condiag/leakage_guard.py on RuntimeSignals + ManualDiagnosis
  - validator LEAKAGE_KEYWORDS on output files
"""
from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Optional

from condiag import schemas as cschemas
from condiag.diagnosis_normalizer import normalize
from condiag.evidence_selector import select as select_evidence
from condiag.context_packet_builder import build_context_packet_md
from condiag.loader import load_taxonomy
from condiag.leakage_guard import check_runtime_signals


# ============================================================================
# Trigger-type -> pathology / 5R mapping (auto-diagnoser v0)
# ============================================================================

# This is the rule-based auto-diagnoser. For seed cases (5 instances with
# manual_diagnosis.json), the human-written diagnosis is used instead via
# existing seed_regression flow. For arbitrary Batch2 instances, this mapping
# derives a best-effort diagnosis from trigger_type.

TRIGGER_TO_PATHOLOGY = {
    "HARD_FAILURE": {
        "pathology": "SUBMISSION_SHAPE_ANOMALY",
        "action_family": "CONTROL",
        "5r_action": "RECONCILE",
        "retry_intent": "VERIFY_SUBMISSION_SHAPE",
        "gap_kind": "TERMINATION_FAILURE",
        "missing_context_type": "SUBMISSION_PROTOCOL",
    },
    "RUNTIME_VALIDATION_FAILURE": {
        "pathology": "REGRESSION_AFTER_PARTIAL_FIX",
        "action_family": "RECOVERY",
        "5r_action": "RECONCILE",
        "retry_intent": "SYNTHESIZE_REGRESSION_CONSTRAINTS",
        "gap_kind": "CONSTRAINT_CONFLICT",
        "missing_context_type": "REGRESSION_CONSTRAINT",
    },
    "PATCH_SHAPE_ANOMALY": {
        "pathology": "OVER_EXPLORE_OVER_EDIT",
        "action_family": "CONTROL",
        "5r_action": "RESTRAIN",
        "retry_intent": "PRUNE_OVERBROAD_PATCH",
        "gap_kind": "NOISY_OVERBROAD",
        "missing_context_type": "SCOPE_BOUNDARY",
    },
    "EVIDENCE_EDIT_MISMATCH": {
        "pathology": "EXPLORE_OK_EDIT_MISALIGNED",
        "action_family": "RECOVERY",
        "5r_action": "REHYDRATE",
        "retry_intent": "REHYDRATE_SEEN_EVIDENCE",
        "gap_kind": "SEEN_BUT_DROPPED",
        "missing_context_type": "SEEN_EVIDENCE",
    },
    "PARTIAL_FIX_SUSPICION": {
        "pathology": "UNDER_EDIT_PARTIAL_FIX",
        "action_family": "RECOVERY",
        "5r_action": "REHYDRATE",
        "retry_intent": "REHYDRATE_SEEN_EVIDENCE",
        "gap_kind": "SEEN_BUT_DROPPED",
        "missing_context_type": "SEEN_EVIDENCE",
    },
    "NO_TRIGGER": {
        "pathology": "LIKELY_CORRECT_NOOP",
        "action_family": "NOOP",
        "5r_action": "NOOP",
        "retry_intent": "NOOP_RETRY_NOT_NEEDED",
        "gap_kind": "",
        "missing_context_type": "",
    },
}


def _build_retrieval_plan(trigger_type: str, rs: cschemas.RuntimeSignals) -> list[dict]:
    """Default retrieval_plan per trigger_type.

    Conservative v0: 2-3 ops that are safe to attempt when repo is available.
    Each op falls back gracefully if its required input is missing.
    """
    if trigger_type == "NO_TRIGGER":
        return []
    plan: list[dict] = []
    if rs.test_failures:
        plan.append({
            "operation": "FIND_FAILED_TEST",
            "target": "visible test failures from runtime_signals",
            "budget": 3,
        })
    if rs.viewed_spans:
        plan.append({
            "operation": "REHYDRATE_SEEN_EVIDENCE",
            "target": "viewed spans dropped from final patch context",
            "budget": 3,
        })
    if rs.edited_files:
        plan.append({
            "operation": "FIND_NEIGHBOR_TESTS",
            "target": "tests adjacent to edited files",
            "budget": 2,
        })
    return plan


def synthesize_manual_diagnosis(
    trigger_result,  # experiments.retry_trigger.RetryTriggerResult (ducktyped)
    rs: cschemas.RuntimeSignals,
    agent: str,
    model: str,
    issue: str = "",
) -> cschemas.ManualDiagnosis:
    """Build a synthetic ManualDiagnosis from trigger result + runtime signals.

    This is the auto-diagnoser (vs manual_diagnosis.json for seed cases).

    The optional `issue` is the SWE-bench Verified problem_statement; used only
    to mine concept keywords for target_hints (D4-7.1). It is runtime-visible
    context the agent already sees — not gold context.
    """
    trig_type = trigger_result.trigger_type
    mapping = TRIGGER_TO_PATHOLOGY.get(trig_type) or {}

    pathology = mapping.get("pathology") or "INSUFFICIENT_RUNTIME_EVIDENCE"
    action_family = mapping.get("action_family") or "ABSTAIN"
    primary_5r = mapping.get("5r_action") or "NOOP"
    retry_intent = mapping.get("retry_intent") or "ABSTAIN_INSUFFICIENT_EVIDENCE"
    gap_kind = mapping.get("gap_kind", "")
    missing_ctx = mapping.get("missing_context_type", "")

    retrieval_plan = _build_retrieval_plan(trig_type, rs)
    target_hints = _auto_target_hints(rs, issue) if trig_type != "NO_TRIGGER" else []

    diagnosis = {
        "pathology": pathology,
        "action_family": action_family,
        "scope": "CONTEXT_RELATED" if trig_type != "NO_TRIGGER" else "NOOP",
        "gap_kind": gap_kind,
        "primary_missing_context_type": missing_ctx,
        "secondary_missing_context_types": [],
        "failure_mode": "",
        "confidence": 0.55 if trig_type != "NO_TRIGGER" else 0.0,
        "abstain": trig_type == "NO_TRIGGER",
        "5r_action": primary_5r,
        "5r_framework_note": (
            f"auto-diagnosed from trigger_type={trig_type} "
            f"(rule-based v0; not human-written)"
        ),
    }

    trigger_assessment = {
        "exit_status": rs.exit_status,
        "trigger_type": trig_type,
        "trigger_reason": list(trigger_result.trigger_reason),
        "runtime_gap_status": trigger_result.runtime_gap_status,
        "confidence": trigger_result.confidence,
        "should_trigger_condiag_v0": trigger_result.should_retry,
    }

    return cschemas.ManualDiagnosis(
        schema_version="condiag.manual_diagnosis.v0",
        instance_id=rs.instance_id,
        agent=agent,
        model=model,
        source="auto_diagnoser_v0",
        mode="auto",
        mode_note="rule-based trigger_type -> pathology mapping; not human-written",
        trigger_assessment=trigger_assessment,
        runtime_evidence={},
        diagnosis=diagnosis,
        target_hints=target_hints,
        retrieval_plan=retrieval_plan,
        retry_intent=retry_intent,
        context_packet_instruction="",
        gold_check={},
    )


# ============================================================================
# target_hints auto-extractor (D4-7.1 Step 2c)
# ============================================================================

# Stopwords shared with broad_expansion._extract_issue_keyword_queries so the
# two baselines use a consistent lexical notion of "concept keyword".
_HINT_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have", "should",
    "when", "while", "into", "them", "then", "what", "where", "which",
    "django", "issue", "patch", "test", "tests", "description", "error",
    "raise", "raises", "raised", "expected", "actual", "traceback",
}


def _auto_target_hints(rs: cschemas.RuntimeSignals, issue: str = "") -> list[dict]:
    """Mine target_hints from runtime-visible signals (D4-7.1).

    Sources (all leakage-safe; no gold):
      - edited_files / viewed_files basenames        (kind=file)
      - identifiers extracted from problem_statement (kind=concept)
      - test_failures test names                     (kind=test)
      - error-shaped class names from last_user_messages_tail (kind=symbol)

    retrieval_executor matches these against span content + path to score
    which viewed_spans / neighbor tests to pull in. Without target_hints,
    REHYDRATE_SEEN_EVIDENCE and FIND_NEIGHBOR_TESTS skip with "no target_hints".
    """
    hints: list[dict] = []
    seen: set[str] = set()

    def add(value: str, kind: str) -> None:
        v = (value or "").strip()
        # drop very short / pure-digit / stopword tokens
        if len(v) < 4 or v.lower() in _HINT_STOPWORDS:
            return
        if v in seen:
            return
        seen.add(v)
        hints.append({"kind": kind, "value": v})

    # 1. file basenames (edited > viewed; both kind=file)
    for f in (rs.edited_files or []):
        add(Path(str(f)).stem, "file")
    for f in (rs.viewed_files_in_order or []):
        add(Path(str(f)).stem, "file")

    # 2. concept keywords from problem_statement
    if issue:
        for tok in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", issue):
            add(tok, "concept")

    # 3. visible test names from test_failures (kind=test)
    for entry in (rs.test_failures or []):
        if isinstance(entry, str):
            add(entry, "test")
        elif isinstance(entry, dict):
            add(entry.get("test") or entry.get("name") or "", "test")

    # 4. error-shaped class names from last_user_messages_tail (kind=symbol)
    tail_text = "\n".join(str(m) for m in (rs.last_user_messages_tail or []))
    if tail_text:
        for m in re.findall(r"\b([A-Z][a-zA-Z]+(?:Error|Exception|Warning))\b", tail_text):
            add(m, "symbol")

    return hints[:40]  # cap; retrieval_executor iterates these per span


# ============================================================================
# Main entry point
# ============================================================================

def run_packet_only(
    attempt_1_dir: Path,
    intervention_dir: Path,
    instance_id: str,
    agent: str,
    model: str,
    trigger_result,  # experiments.retry_trigger.RetryTriggerResult
    taxonomy_path: Optional[Path] = None,
    repo_path: Optional[Path] = None,
    base_commit: str = "",
    issue: str = "",
) -> dict:
    """Run ConDiag packet_only flow for one instance.

    Writes ConDiag-flavored artifacts under intervention_dir.
    Returns the recovery_report dict.

    `issue` is the SWE-bench Verified problem_statement; mined for target_hints
    so REHYDRATE / FIND_NEIGHBOR_TESTS can match against span content (D4-7.1).
    """
    attempt_1_dir = Path(attempt_1_dir)
    intervention_dir = Path(intervention_dir)
    intervention_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load runtime_signals
    rs_dict = json.loads((attempt_1_dir / "runtime_signals.json").read_text(encoding="utf-8"))
    rs = cschemas.RuntimeSignals.from_dict(rs_dict)

    # 2. Run leakage guard on runtime_signals (must pass before proceeding)
    if taxonomy_path is None:
        taxonomy_path = Path("/mnt/d/condiag-artifacts/condiag/v0/pathology_taxonomy.json")
    taxonomy = load_taxonomy(taxonomy_path)
    rs_leak = check_runtime_signals(rs, taxonomy)
    rs_leak.raise_if_leak()  # raises if gold/eval fields leaked into rs

    # 3. Synthesize ManualDiagnosis
    md = synthesize_manual_diagnosis(trigger_result, rs, agent=agent, model=model, issue=issue)

    # 4. Normalize
    nd = normalize(md, taxonomy)

    # 5. Repo resolution (likely fails in v0 — no repo mounted)
    repo_status = "no_repo_provided"
    repo_resolution_dict = None
    action_results: list = []
    selected: dict = {"evidence": [], "selection_summary": {}}

    if repo_path and Path(repo_path).is_dir():
        try:
            from condiag import repo_resolver
            from condiag import repository_index as ri
            from condiag.retrieval_executor import execute_plan

            resolution = repo_resolver.resolve(repo_path, instance_id, base_commit)
            repo_resolution_dict = resolution.to_dict()
            if resolution.ok:
                repo_status = "resolved"
                idx = ri.build_index(Path(resolution.repo_path))
                action_results = execute_plan(
                    retrieval_plan=md.retrieval_plan,
                    idx=idx,
                    runtime_signals=rs,
                    manual_diagnosis=md,
                )
                selected = select_evidence(
                    action_results=action_results,
                    retry_intent=nd.retry_intent,
                    instance_id=instance_id,
                )
            else:
                repo_status = f"resolution_failed:{resolution.source}"
        except Exception as e:
            repo_status = f"resolution_error:{type(e).__name__}:{e}"

    # 6. Always write executed_actions.json (even if empty / all skipped)
    executed_actions_payload = {
        "actions": [r.to_dict() for r in action_results],
        "summary": {
            "action_count": len(action_results),
            "done": sum(1 for r in action_results if r.status == "done"),
            "skipped": sum(1 for r in action_results if r.status == "skipped"),
            "no_candidates": sum(1 for r in action_results if r.status == "no_candidates"),
            "candidates_total": sum(len(r.candidates) for r in action_results),
        },
        "repo_status": repo_status,
        "repo_resolution": repo_resolution_dict,
    }
    (intervention_dir / "executed_actions.json").write_text(
        json.dumps(executed_actions_payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 7. selected_evidence.json
    (intervention_dir / "selected_evidence.json").write_text(
        json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 8. context_packet.md (always — packet content varies by what's available)
    # build_context_packet_md expects a repo_root Path; pass attempt_1 as placeholder
    # when no repo (the function only uses it for display).
    packet_md = build_context_packet_md(
        repo_root=Path(repo_path) if repo_path else attempt_1_dir,
        nd=nd,
        md=md,
        rs=rs,
        selected=selected,
    )
    (intervention_dir / "context_packet.md").write_text(packet_md, encoding="utf-8")

    # 9. recovery_report.json (the ConDiag analog of intervention_report)
    recovery_report = {
        "schema_version": "condiag.recovery_report.v0",
        "instance_id": instance_id,
        "agent": agent,
        "baseline": "condiag_packet_only",
        "mode": "packet_only",
        "should_retry": trigger_result.should_retry,
        "trigger_type": trigger_result.trigger_type,
        "trigger_reason": list(trigger_result.trigger_reason),
        "runtime_gap_status": trigger_result.runtime_gap_status,
        "trigger_confidence": trigger_result.confidence,
        "diagnosis": {
            "pathology": nd.pathology,
            "action_family": nd.action_family,
            "primary_5r_action": nd.primary_5r_action,
            "retry_intent": nd.retry_intent,
            "confidence": nd.confidence,
            "source": "auto_diagnoser_v0",
        },
        "retrieval_plan_size": len(md.retrieval_plan),
        "executed_actions_summary": executed_actions_payload["summary"],
        "selected_evidence_count": len(selected.get("evidence") or []),
        "repo_status": repo_status,
        "has_context_packet": True,
        "context_packet_kind": "condiag_typed_recovery",
        "context_packet_chars": len(packet_md),
        "guarantees": {
            "did_not_read_gold_check": True,
            "did_not_read_official_eval": True,
            "did_not_read_contextbench_metrics": True,
            "used_repo_at_base_commit": repo_status == "resolved",
        },
        "created_at": _now_iso(),
    }
    (intervention_dir / "recovery_report.json").write_text(
        json.dumps(recovery_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return recovery_report


def _now_iso() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
