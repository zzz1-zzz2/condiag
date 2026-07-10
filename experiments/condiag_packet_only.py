"""ConDiag packet_only baseline — context_deficiency_type v1 diagnosis.

This is the ONLY intervention baseline allowed to import ConDiag core
retrieval machinery. It produces typed/5R/diagnosis-style artifacts:

  - intervention/selected_evidence.json
  - intervention/recovery_report.json
  - intervention/executed_actions.json
  - intervention/context_packet.md   (ConDiag-flavored, with diagnosis section)
  - intervention/retry_trigger_result.json

Pipeline (v1):
  1. attempt_1/runtime_signals.json -> RuntimeSignals
  2. experiments.retry_trigger.classify(rs) -> RetryTriggerResult
  3. diagnosis_generator.generate() -> DiagnosisResult (context_deficiency_type)
  4. synthesize_manual_diagnosis() -> ManualDiagnosis
  5. diagnosis_normalizer.normalize(md, taxonomy) -> NormalizedDiagnosis
  6. Repo resolution
  7. retrieval_executor.execute_plan + evidence_selector.select
  8. context_packet_builder.build_context_packet_md -> packet
  9. Write artifacts

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
from pathlib import Path
from typing import Optional

from condiag import schemas as cschemas
from condiag.diagnosis_generator import generate as diagnose
from condiag.diagnosis_normalizer import normalize
from condiag.evidence_selector import select as select_evidence
from condiag.context_packet_builder import build_context_packet_md
from condiag.loader import load_taxonomy
from condiag.leakage_guard import check_runtime_signals


def synthesize_manual_diagnosis(
    trigger_result,
    rs: cschemas.RuntimeSignals,
    agent: str,
    model: str,
    issue: str = "",
) -> cschemas.ManualDiagnosis:
    """Build a synthetic ManualDiagnosis via diagnosis_generator.

    The diagnosis_generator.classify_deficiency() maps trigger_type + issue
    keywords + runtime signals to a context_deficiency_type (7-type taxonomy).
    This replaces the old TRIGGER_TO_PATHOLOGY mapping that only produced
    REHYDRATE for every non-NO_TRIGGER case.

    The optional ``issue`` is the SWE-bench Verified problem_statement; used
    for keyword signal extraction and target_hints. It is runtime-visible
    context the agent already sees — not gold context.
    """
    trig_type = trigger_result.trigger_type

    # NO_TRIGGER → no diagnosis needed; early return with NOOP
    if trig_type == "NO_TRIGGER":
        diagnosis = {
            "context_deficiency_type": "",
            "context_deficiency_secondary": [],
            "pathology": "LIKELY_CORRECT_NOOP",
            "action_family": "NOOP",
            "scope": "NOOP",
            "gap_kind": "",
            "primary_missing_context_type": "",
            "secondary_missing_context_types": [],
            "failure_mode": "",
            "confidence": 0.0,
            "abstain": True,
            "5r_action": "NOOP",
            "5r_framework_note": "NO_TRIGGER → no diagnosis needed",
        }
        trigger_assessment = {
            "exit_status": rs.exit_status,
            "trigger_type": trig_type,
            "trigger_reason": list(trigger_result.trigger_reason),
            "confidence_runtime": trigger_result.confidence,
            "should_retry": trigger_result.should_retry,
        }
        return cschemas.ManualDiagnosis(
            schema_version="condiag.manual_diagnosis.v1",
            instance_id=rs.instance_id,
            agent=agent,
            model=model,
            source="auto_diagnoser_v1_context_deficiency",
            mode="auto",
            mode_note="NO_TRIGGER → no diagnosis",
            trigger_assessment=trigger_assessment,
            runtime_evidence={},
            diagnosis=diagnosis,
            target_hints=[],
            retrieval_plan=[],
            retry_intent="NOOP_RETRY_NOT_NEEDED",
            context_packet_instruction="",
            gold_check={},
        )

    # Run diagnosis generator: trigger_type + issue keywords + runtime signals
    # → context_deficiency_type + differentiated retrieval_plan + target_hints
    rs_dict = rs.to_dict()
    dr = diagnose(
        trigger_type=trig_type,
        trigger_reason=list(trigger_result.trigger_reason),
        runtime_signals=rs_dict,
        issue=issue,
    )

    diagnosis = {
        "context_deficiency_type": dr.context_deficiency_type,
        "context_deficiency_secondary": dr.context_deficiency_secondary,
        "pathology": dr.pathology,
        "action_family": dr.action_family,
        "scope": "CONTEXT_RELATED",
        "gap_kind": "",
        "primary_missing_context_type": "",
        "secondary_missing_context_types": [],
        "failure_mode": "",
        "confidence": dr.confidence,
        "abstain": False,
        "5r_action": dr.primary_5r_action,
        "5r_framework_note": (
            f"auto-diagnosed from trigger_type={trig_type} "
            f"→ context_deficiency_type={dr.context_deficiency_type} "
            f"(rule-based v1; differentiated by deficiency type)"
        ),
    }

    trigger_assessment = {
        "exit_status": rs.exit_status,
        "trigger_type": trig_type,
        "trigger_reason": list(trigger_result.trigger_reason),
        "confidence_runtime": trigger_result.confidence,
        "should_retry": trigger_result.should_retry,
    }

    return cschemas.ManualDiagnosis(
        schema_version="condiag.manual_diagnosis.v1",
        instance_id=rs.instance_id,
        agent=agent,
        model=model,
        source="auto_diagnoser_v1_context_deficiency",
        mode="auto",
        mode_note=(
            f"rule-based context_deficiency_type={dr.context_deficiency_type}; "
            f"not human-written"
        ),
        trigger_assessment=trigger_assessment,
        runtime_evidence={},
        diagnosis=diagnosis,
        target_hints=dr.target_hints,
        retrieval_plan=dr.retrieval_plan,
        retry_intent=dr.retry_intent,
        context_packet_instruction="",
        gold_check={},
    )


# ============================================================================
# Main entry point
# ============================================================================

def run_packet_only(
    attempt_1_dir: Path,
    intervention_dir: Path,
    instance_id: str,
    agent: str,
    model: str,
    trigger_result,
    taxonomy_path: Optional[Path] = None,
    repo_path: Optional[Path] = None,
    base_commit: str = "",
    issue: str = "",
) -> dict:
    """Run ConDiag packet_only flow for one instance.

    Writes ConDiag-flavored artifacts under intervention_dir.
    Returns the recovery_report dict.
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
    rs_leak.raise_if_leak()

    # 3. Synthesize ManualDiagnosis (now uses diagnosis_generator internally)
    md = synthesize_manual_diagnosis(trigger_result, rs, agent=agent, model=model, issue=issue)

    # 4. Normalize
    nd = normalize(md, taxonomy)

    # 5. Repo resolution
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
                    context_deficiency_type=nd.context_deficiency_type or "",
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

    # 8. context_packet.md
    packet_md = build_context_packet_md(
        repo_root=Path(repo_path) if repo_path else attempt_1_dir,
        nd=nd,
        md=md,
        rs=rs,
        selected=selected,
    )
    (intervention_dir / "context_packet.md").write_text(packet_md, encoding="utf-8")

    # 9. recovery_report.json
    recovery_report = {
        "schema_version": "condiag.recovery_report.v1",
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
            "context_deficiency_type": nd.context_deficiency_type,
            "context_deficiency_secondary": nd.context_deficiency_secondary,
            "pathology": nd.pathology,
            "action_family": nd.action_family,
            "primary_5r_action": nd.primary_5r_action,
            "retry_intent": nd.retry_intent,
            "confidence": nd.confidence,
            "source": "auto_diagnoser_v1_context_deficiency",
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
