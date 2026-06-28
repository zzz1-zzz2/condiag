"""ConDiag report — write dry-run outputs.

Per-case outputs (under <out>/<instance>/):
    load_report.json              loader/leakage/taxonomy check summary
    trigger_result.json           TriggerResult (auto-classified)
    normalized_diagnosis.json     ManualDiagnosis normalized + action_family
    action_plan.json              retrieval vs control actions
    context_packet_skeleton.md    natural-language packet template

Aggregate outputs (under <out>/):
    pilot4_case_matrix.csv        one-row-per-case summary
    dry_run_summary.json          run manifest (timing, counts, status)
"""
from __future__ import annotations

import csv
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Dict, List

from .schemas import (
    ActionPlan,
    ManualDiagnosis,
    NormalizedDiagnosis,
    PathologyTaxonomy,
    RuntimeSignals,
    TriggerResult,
)
from .leakage_guard import LeakageReport


# ===== per-case =====


def write_leakage_report(
    out_dir: Path,
    instance_id: str,
    rs_leak: LeakageReport,
    md_leak: LeakageReport,
) -> Path:
    """Detailed leakage report — separate from load_report.

    Lists every forbidden field and location found by leakage_guard.
    """
    payload = {
        "instance_id": instance_id,
        "ok": rs_leak.ok and md_leak.ok,
        "runtime_signals": {
            "ok": rs_leak.ok,
            "forbidden_fields_seen": rs_leak.forbidden_fields_seen,
            "forbidden_locations": rs_leak.forbidden_locations,
            "notes": rs_leak.notes,
        },
        "manual_diagnosis": {
            "ok": md_leak.ok,
            "forbidden_fields_seen": md_leak.forbidden_fields_seen,
            "forbidden_locations": md_leak.forbidden_locations,
            "notes": md_leak.notes,
        },
    }
    p = out_dir / instance_id / "leakage_report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return p


def write_load_report(
    out_dir: Path,
    instance_id: str,
    rs: RuntimeSignals,
    md: ManualDiagnosis,
    rs_leak: LeakageReport,
    md_leak: LeakageReport,
    taxonomy_check_ok: bool,
    notes: List[str],
) -> Path:
    payload = {
        "instance_id": instance_id,
        "runtime_signals": {
            "schema_version": rs.schema_version,
            "loaded": True,
            "exit_status": rs.exit_status,
            "viewed_files_count": rs.viewed_files_count,
            "edited_files_count": rs.edited_files_count,
            "test_runs_count": rs.test_runs_count,
            "git_checkout_count": rs.git_checkout_count,
            "changed_files_count": rs.changed_files_count,
            "changed_lines_total": rs.changed_lines_total,
            "submitted_without_tests": rs.submitted_without_tests,
        },
        "manual_diagnosis": {
            "schema_version": md.schema_version,
            "loaded": True,
            "mode": md.mode,
            "pathology": md.diagnosis.get("pathology"),
            "5r_action": md.diagnosis.get("5r_action"),
            "retry_intent": md.retry_intent,
        },
        "leakage_summary": {
            "runtime_signals_ok": rs_leak.ok,
            "manual_diagnosis_ok": md_leak.ok,
            "see": "leakage_report.json",
        },
        "taxonomy_check_ok": taxonomy_check_ok,
        "notes": notes,
    }
    p = out_dir / instance_id / "load_report.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return p


def write_trigger_result(out_dir: Path, tr: TriggerResult) -> Path:
    p = out_dir / tr.instance_id / "trigger_result.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(tr), indent=2, ensure_ascii=False, default=str))
    return p


def write_normalized_diagnosis(out_dir: Path, nd: NormalizedDiagnosis) -> Path:
    p = out_dir / nd.instance_id / "normalized_diagnosis.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(nd), indent=2, ensure_ascii=False, default=str))
    return p


def write_action_plan(out_dir: Path, ap: ActionPlan) -> Path:
    p = out_dir / ap.instance_id / "action_plan.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "instance_id": ap.instance_id,
        "pathology": ap.pathology,
        "action_family": ap.action_family,
        "primary_5r_action": ap.primary_5r_action,
        "retrieval_actions": ap.retrieval_actions,
        "control_actions": ap.control_actions,
        "unknown_operations": ap.unknown_operations,
        "summary": {
            "retrieval_count": len(ap.retrieval_actions),
            "control_count": len(ap.control_actions),
            "unknown_count": len(ap.unknown_operations),
        },
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return p


def write_context_packet_skeleton(
    out_dir: Path,
    nd: NormalizedDiagnosis,
    tr: TriggerResult,
    rs: RuntimeSignals,
) -> Path:
    """Render a natural-language context packet template.

    Real ConDiag v0 will fill in concrete evidence snippets; this skeleton
    contains the trigger + diagnosis + instruction block, leaving evidence
    slots marked as <...>.
    """
    p = out_dir / nd.instance_id / "context_packet_skeleton.md"
    p.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f"# Context Packet — {nd.instance_id}\n")
    lines.append(f"**Pathology**: `{nd.pathology}`  ")
    lines.append(f"**Action family**: `{nd.action_family}`  ")
    lines.append(f"**5R action**: `{nd.primary_5r_action}`  ")
    lines.append(f"**Retry intent**: `{nd.retry_intent}`  ")
    lines.append(f"**Confidence**: {nd.confidence}  ")
    lines.append(f"**Trigger type**: `{tr.trigger_type}` ({tr.trigger_layer})  ")
    lines.append(f"**Scope anomaly score**: {tr.scope_anomaly_score} ")
    lines.append(f"(warning>={tr.scope_anomaly_threshold_warning}, "
                 f"strong>={tr.scope_anomaly_threshold_strong})\n")

    lines.append("## Trigger reasons\n")
    for r in tr.trigger_reasons:
        lines.append(f"- {r}")
    lines.append("")

    lines.append("## Runtime evidence summary\n")
    lines.append(f"- viewed_files_count: {rs.viewed_files_count}")
    lines.append(f"- edited_files_count: {rs.edited_files_count}")
    lines.append(f"- changed_lines_total: {rs.changed_lines_total}")
    lines.append(f"- test_runs_count: {rs.test_runs_count}")
    lines.append(f"- test_failures_count: {rs.test_failures_count}")
    lines.append(f"- git_checkout_count: {rs.git_checkout_count}")
    lines.append(f"- submitted_without_tests: {rs.submitted_without_tests}")
    lines.append(f"- repeated_edit_patterns_count: {len(rs.repeated_edit_patterns)}")
    lines.append("")

    if rs.test_failures:
        lines.append("### Local test failures (parsed from agent output)\n")
        for f in rs.test_failures:
            lines.append(f"- `{f}`")
        lines.append("")

    if rs.possible_regression_failures:
        lines.append("### Possible regression candidates (PASSED→FAILED)\n")
        for r in rs.possible_regression_failures:
            lines.append(f"- `{r}`")
        lines.append("")

    lines.append("## Target hints (from manual diagnosis)\n")
    for h in nd.target_hints:
        if isinstance(h, dict):
            lines.append(f"- [{h.get('kind', '?')}] `{h.get('value', '')}`")
        else:
            lines.append(f"- `{h}`")
    lines.append("")

    lines.append("## Instruction to agent\n")
    lines.append("``")
    lines.append(nd.context_packet_instruction or "<no instruction provided in manual_diagnosis>")
    lines.append("``\n")

    lines.append("## Evidence slots (to be filled by Retrieval Executor)\n")
    lines.append("- `<FAILED_TEST_EXCERPT>`: pytest output of the most informative failure")
    lines.append("- `<SYMBOL_DEFINITION_SPAN>`: target symbol definition (file:line-range)")
    lines.append("- `<CALL_SITE_EXAMPLE>`: representative caller site")
    lines.append("- `<REGRESSION_CONSTRAINT>`: failing sibling test expectation")

    p.write_text("\n".join(lines), encoding="utf-8")
    return p


# ===== aggregate =====

def _runtime_evidence_strength(tr: TriggerResult, rs: RuntimeSignals) -> str:
    """Bucket evidence strength per user's expected CSV column."""
    if tr.trigger_type == "RUNTIME_VALIDATION_FAILURE" and rs.test_failures_count > 0:
        return "high"
    if tr.trigger_type == "PATCH_SHAPE_ANOMALY" and tr.scope_anomaly_score >= 4:
        return "high"
    if tr.trigger_type == "EVIDENCE_EDIT_MISMATCH":
        return "medium"
    if tr.trigger_type == "PARTIAL_FIX_SUSPICION":
        return "low/medium"
    if tr.triggered:
        return "medium"
    return "low"


def _next_module(nd: NormalizedDiagnosis) -> str:
    return {
        "RELOCALIZE": "Relocalization",
        "RETRIEVE":   "Sibling Audit + Retrieval",
        "REHYDRATE":  "Rehydrate + Alignment Checker",
        "RESTRAIN":   "Scope Guard",
        "RECONCILE":  "Regression Constraint Pack",
    }.get(nd.primary_5r_action or "", "—")


def write_case_matrix(
    out_dir: Path,
    rows: List[dict],
    filename: str = "pilot_case_matrix.csv",
) -> Path:
    p = out_dir / filename
    fieldnames = [
        "instance_id",
        "runtime_trigger",
        "manual_pathology",
        "runtime_pathology_candidate",
        "action_family",
        "5r_action",
        "confidence_manual",
        "confidence_runtime",
        "runtime_evidence_strength",
        "next_module",
        "mode",
        "ok",
    ]
    with p.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    return p


def write_summary(
    out_dir: Path,
    started_at: float,
    ended_at: float,
    rows: List[dict],
    case_root: Path,
) -> Path:
    p = out_dir / "dry_run_summary.json"
    payload = {
        "started_at_unix": started_at,
        "ended_at_unix": ended_at,
        "duration_seconds": round(ended_at - started_at, 3),
        "case_root": str(case_root),
        "case_count": len(rows),
        "ok_count": sum(1 for r in rows if r.get("ok")),
        "failures": [r for r in rows if not r.get("ok")],
    }
    p.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
    return p
