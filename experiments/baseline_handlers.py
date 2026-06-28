"""Baseline handlers (D4-3 scaffolding + D4-4 real base_miniswe).

Each handler is the per-instance body of a baseline run.

    base_miniswe          -> D4-4 (IMPLEMENTED, from_existing_traj mode)
    feedback_retry        -> D4-5 (stub)
    broad_expansion       -> D4-6 (stub)
    condiag_packet_only   -> D4-7 (stub)
    condiag_retry         -> reserved (stub)

A handler's contract:

    def handle_xxx(run_dir: Path, instance_id: str, mode: str,
                   adapter, config: dict) -> dict:

    Returns a small dict with at least:
        {"handled": bool, "reason": str, ...}

`mode` is one of:
    "dry-run"  : create skeleton only, no real agent invocation
    "smoke"    : real agent run, but smoke-sized (1-2 instances)
    "full"     : real agent run, full instance set

`config` keys consumed by base_miniswe:
    manifest        : dict[str, dict]  {instance_id -> manifest row}
                      (required for smoke/full; built by manifest_builder)
    selected_attempt: int              (always 1 for base_miniswe)
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


HandlerFn = Callable[[Path, str, str, Any, dict], dict]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def handle_base_miniswe(
    run_dir: Path,
    instance_id: str,
    mode: str,
    adapter,
    config: dict,
) -> dict:
    """Base mini-SWE handler (from_existing_traj mode).

    Flow:
      1. Resolve traj_path via manifest (config["manifest"][instance_id])
      2. adapter.build_case_bundle(traj_path -> attempt_1/)
      3. Copy attempt_1/patch.diff -> final/patch.diff
      4. Write attempt_1/attempt_report.json (summary)
      5. Write final/final_report.json (selected_attempt=1)
      6. Write cost.json via cost_extractor

    In dry-run mode: do nothing, return stub marker.
    """
    if mode == "dry-run":
        return {
            "handled": False,
            "reason": "dry_run_skeleton_only",
            "mode": mode,
            "instance_id": instance_id,
        }

    manifest = config.get("manifest") or {}
    row = manifest.get(instance_id)
    if row is None:
        return {
            "handled": False,
            "reason": f"instance_id {instance_id!r} not in manifest",
            "mode": mode,
            "instance_id": instance_id,
        }

    traj_path = Path(row["traj_path"])
    if not traj_path.is_file():
        return {
            "handled": False,
            "reason": f"traj_path missing: {traj_path}",
            "mode": mode,
            "instance_id": instance_id,
        }

    run_dir = Path(run_dir)
    attempt_1 = run_dir / "attempt_1"
    final = run_dir / "final"
    attempt_1.mkdir(parents=True, exist_ok=True)
    final.mkdir(parents=True, exist_ok=True)

    # 1. build_case_bundle writes raw_trajectory.json / runtime_signals.json /
    #    patch.diff / final_patch_context.json / local_test_outputs.md / build_report.json
    bundle = adapter.build_case_bundle(
        raw_run_dir=traj_path.parent,
        instance_id=instance_id,
        out_dir=attempt_1,
    )

    # 2. attempt_report.json (handler-level summary of attempt_1)
    attempt_report = {
        "schema_version": "condiag.attempt_report.v0",
        "attempt": "attempt_1",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "base_miniswe",
        "source_traj": str(traj_path),
        "source_batch": row.get("source_batch", ""),
        "model": row.get("model", ""),
        "exit_status": row.get("exit_status", ""),
        "patch_chars": (attempt_1 / "patch.diff").stat().st_size if (attempt_1 / "patch.diff").is_file() else 0,
        "has_patch": (attempt_1 / "patch.diff").is_file() and (attempt_1 / "patch.diff").stat().st_size > 0,
        "build_report": bundle,
    }
    (attempt_1 / "attempt_report.json").write_text(
        json.dumps(attempt_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 3. final/* = copy of attempt_1/* for base_miniswe (no retry, so final = attempt_1)
    #    per spec: patch.diff + runtime_signals.json + (contextbench_metrics.json if exists)
    src_patch = attempt_1 / "patch.diff"
    dst_patch = final / "patch.diff"
    if src_patch.is_file():
        shutil.copyfile(src_patch, dst_patch)

    src_rs = attempt_1 / "runtime_signals.json"
    dst_rs = final / "runtime_signals.json"
    if src_rs.is_file():
        shutil.copyfile(src_rs, dst_rs)

    # contextbench_metrics.json: only copy if it already exists in attempt_1/
    # (ContextBench eval runs as a separate stage after the handler)
    src_cbm = attempt_1 / "contextbench_metrics.json"
    dst_cbm = final / "contextbench_metrics.json"
    cbm_status = "pending_evaluation"
    if src_cbm.is_file():
        shutil.copyfile(src_cbm, dst_cbm)
        cbm_status = "evaluated"

    # 4. final/final_report.json
    final_report = {
        "schema_version": "condiag.final_report.v0",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "base_miniswe",
        "selected_attempt": 1,
        "selected_attempt_dir": "attempt_1",
        "selection_reason": "base_miniswe: single attempt, no retry",
        "has_final_patch": dst_patch.is_file() and dst_patch.stat().st_size > 0,
        "final_patch_chars": dst_patch.stat().st_size if dst_patch.is_file() else 0,
        "contextbench_metrics_status": cbm_status,
        "finalized_at": _now_iso(),
    }
    (final / "final_report.json").write_text(
        json.dumps(final_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 5. cost.json via cost_extractor
    from .cost_extractor import extract_cost_from_traj
    cost_data = extract_cost_from_traj(
        traj_path=traj_path,
        instance_id=instance_id,
        agent=adapter.name,
    )
    (run_dir / "cost.json").write_text(
        json.dumps(cost_data, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return {
        "handled": True,
        "reason": "from_existing_traj",
        "mode": mode,
        "instance_id": instance_id,
        "traj_path": str(traj_path),
        "attempt_1": str(attempt_1),
        "final": str(final),
        "cost_chars": (run_dir / "cost.json").stat().st_size,
        # fields consumed by baseline_runner to update run_report.json
        "attempt_1_status": "completed",
        "final_source": "attempt_1",
    }


def handle_feedback_retry(
    run_dir: Path,
    instance_id: str,
    mode: str,
    adapter,
    config: dict,
) -> dict:
    """Feedback Retry handler — packet_only mode (D4-5a).

    Flow:
      1. Resolve base_miniswe attempt_1 (config["base_run_root"] or fallback to traj)
      2. Copy attempt_1/* to this run's attempt_1/
      3. retry_trigger.classify(runtime_signals) -> RetryTriggerResult
      4. Always write intervention/retry_trigger_result.json + intervention_report.json
      5. If should_retry=True: build intervention/context_packet.md (FEEDBACK ONLY,
         no ConDiag evidence, no gold/official/contextbench_metrics)
      6. If should_retry=False: intervention_report.status = "skipped_no_retry"
      7. final/* = attempt_1/* (packet_only mode; no attempt_2)
      8. cost.json = copy from base_miniswe (no new agent cost)

    config keys consumed:
        base_run_root : Path or str  (root containing miniswe/base_miniswe/<instance>/)
        manifest      : dict         (fallback if base_miniswe attempt_1 missing)
    """
    if mode == "dry-run":
        return {
            "handled": False,
            "reason": "dry_run_skeleton_only",
            "mode": mode,
            "instance_id": instance_id,
        }

    run_dir = Path(run_dir)
    attempt_1 = run_dir / "attempt_1"
    intervention = run_dir / "intervention"
    final = run_dir / "final"
    attempt_1.mkdir(parents=True, exist_ok=True)
    intervention.mkdir(parents=True, exist_ok=True)
    final.mkdir(parents=True, exist_ok=True)

    # 1. Resolve base_miniswe attempt_1
    base_run_root = Path(config.get("base_run_root") or run_dir.parent.parent.parent)
    base_attempt_1 = base_run_root / "miniswe" / "base_miniswe" / instance_id / "attempt_1"
    base_cost = base_run_root / "miniswe" / "base_miniswe" / instance_id / "cost.json"

    base_exists = base_attempt_1.is_dir() and (base_attempt_1 / "runtime_signals.json").is_file()

    if base_exists:
        # 2. Copy attempt_1/* from base_miniswe
        for src in base_attempt_1.iterdir():
            if src.is_file():
                shutil.copyfile(src, attempt_1 / src.name)
        rs = json.loads((attempt_1 / "runtime_signals.json").read_text(encoding="utf-8"))
        traj_source = "base_miniswe_attempt_1"
    else:
        # Fallback: build attempt_1 from manifest traj (same as base_miniswe)
        manifest = config.get("manifest") or {}
        row = manifest.get(instance_id)
        if row is None:
            return {
                "handled": False,
                "reason": f"neither base_miniswe attempt_1 nor manifest row for {instance_id!r}",
                "mode": mode, "instance_id": instance_id,
            }
        traj_path = Path(row["traj_path"])
        adapter.build_case_bundle(
            raw_run_dir=traj_path.parent,
            instance_id=instance_id,
            out_dir=attempt_1,
        )
        rs = json.loads((attempt_1 / "runtime_signals.json").read_text(encoding="utf-8"))
        traj_source = f"manifest_fallback:{traj_path}"

    # 3. retry_trigger.classify
    from .retry_trigger import classify
    trigger_result = classify(rs)
    trigger_dict = trigger_result.to_dict()

    # Always write the trigger result for inspection / compare matrix
    (intervention / "retry_trigger_result.json").write_text(
        json.dumps(trigger_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 4. Build context_packet (only if should_retry=True) and intervention_report
    packet_path = intervention / "context_packet.md"
    patch_summary = _summarize_previous_patch(attempt_1)
    test_feedback = _extract_test_feedback(attempt_1)

    if trigger_result.should_retry:
        packet_md = _build_feedback_packet(
            instance_id=instance_id,
            trigger_result=trigger_result,
            patch_summary=patch_summary,
            test_feedback=test_feedback,
        )
        packet_path.write_text(packet_md, encoding="utf-8")
        intervention_status = "feedback_packet_built"
        packet_mode = "feedback_only"
    else:
        # No packet — intervention_report records why no retry
        intervention_status = "skipped_no_retry"
        packet_mode = "skipped_no_retry"

    intervention_report = {
        "schema_version": "condiag.intervention_report.v0",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "feedback_retry",
        "mode": "packet_only",
        "status": intervention_status,
        "packet_mode": packet_mode,
        "should_retry": trigger_result.should_retry,
        "trigger_type": trigger_result.trigger_type,
        "trigger_reason": trigger_result.trigger_reason,
        "runtime_gap_status": trigger_result.runtime_gap_status,
        "confidence": trigger_result.confidence,
        "alternative_trigger_types": trigger_result.alternative_trigger_types,
        "has_context_packet": packet_path.is_file(),
        "context_packet_path": str(packet_path) if packet_path.is_file() else None,
        "context_packet_kind": "feedback_only" if packet_path.is_file() else None,
        "attempts_planned": 1 if not trigger_result.should_retry else 2,
        "attempt_2_executed": False,  # packet_only mode
        "source_attempt_1": str(base_attempt_1) if base_exists else traj_source,
        "created_at": _now_iso(),
    }
    (intervention / "intervention_report.json").write_text(
        json.dumps(intervention_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 5. final/* = attempt_1/* (packet_only: no real retry)
    for sub in ["patch.diff", "runtime_signals.json"]:
        src = attempt_1 / sub
        if src.is_file():
            shutil.copyfile(src, final / sub)

    cbm_status = "pending_evaluation"
    final_report = {
        "schema_version": "condiag.final_report.v0",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "feedback_retry",
        "mode": "packet_only",
        "selected_attempt": 1,
        "selected_attempt_dir": "attempt_1",
        "selection_reason": (
            "feedback_retry packet_only: context packet built but not yet "
            "consumed by a real attempt_2; final = attempt_1"
        ),
        "has_final_patch": (final / "patch.diff").is_file() and (final / "patch.diff").stat().st_size > 0,
        "contextbench_metrics_status": cbm_status,
        "trigger_type": trigger_result.trigger_type,
        "should_retry": trigger_result.should_retry,
        "finalized_at": _now_iso(),
    }
    (final / "final_report.json").write_text(
        json.dumps(final_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 6. cost.json — inherit from base_miniswe (packet_only: no new agent cost)
    if base_cost.is_file():
        shutil.copyfile(base_cost, run_dir / "cost.json")
    else:
        # inline fallback: build cost.json from manifest traj
        manifest = config.get("manifest") or {}
        row = manifest.get(instance_id)
        if row:
            from .cost_extractor import extract_cost_from_traj
            cost_data = extract_cost_from_traj(
                traj_path=Path(row["traj_path"]),
                instance_id=instance_id,
                agent=adapter.name,
            )
            (run_dir / "cost.json").write_text(
                json.dumps(cost_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    return {
        "handled": True,
        "reason": "feedback_retry_packet_only",
        "mode": mode,
        "instance_id": instance_id,
        "trigger_type": trigger_result.trigger_type,
        "should_retry": trigger_result.should_retry,
        "intervention_status": intervention_status,
        "has_context_packet": packet_path.is_file(),
        "source_attempt_1": str(base_attempt_1) if base_exists else traj_source,
        # fields consumed by baseline_runner to update run_report.json
        "attempt_1_status": "completed",
        "attempt_2_status": "skipped_packet_only_mode",
        "final_source": "attempt_1",
    }


# ===== feedback retry helpers (NO ConDiag retrieval, NO gold/eval fields) =====

def _summarize_previous_patch(attempt_1: Path) -> dict:
    """Build a runtime-visible summary of attempt_1's patch.

    Reads patch.diff to count files / added / removed lines.
    Does NOT call ContextBench, does NOT compute coverage metrics.
    """
    patch_path = attempt_1 / "patch.diff"
    if not patch_path.is_file():
        return {"has_patch": False, "files": [], "added_lines": 0, "removed_lines": 0}
    text = patch_path.read_text(encoding="utf-8", errors="ignore")
    files: list[str] = []
    added = removed = 0
    cur_file = None
    for line in text.splitlines():
        if line.startswith("diff --git"):
            parts = line.split()
            if len(parts) >= 4:
                cur_file = parts[-1].lstrip("b/")
                files.append(cur_file)
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return {
        "has_patch": True,
        "files": files,
        "files_count": len(files),
        "added_lines": added,
        "removed_lines": removed,
        "patch_chars": len(text),
    }


def _extract_test_feedback(attempt_1: Path) -> dict:
    """Extract runtime-visible test feedback from local_test_outputs.md.

    Returns first ~60 lines of the file (capped) plus a count.
    No gold/eval parsing — just the raw agent-visible test output.
    """
    lt_path = attempt_1 / "local_test_outputs.md"
    if not lt_path.is_file():
        return {"has_output": False, "excerpt": "", "lines_total": 0}
    text = lt_path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    excerpt = "\n".join(lines[:60])
    return {
        "has_output": True,
        "excerpt": excerpt,
        "lines_total": len(lines),
        "lines_excerpt": min(60, len(lines)),
    }


def _build_feedback_packet(
    instance_id: str,
    trigger_result,
    patch_summary: dict,
    test_feedback: dict,
) -> str:
    """Build the feedback-only context packet (NO ConDiag retrieval, NO gold).

    Template follows the user spec:
        - Previous Attempt Summary
        - Runtime Feedback (local test output)
        - Previous Patch Summary (file list + line counts)
        - Retry Instruction (no targeted retrieval)
    """
    trigger_lines = "\n".join(f"- {r}" for r in trigger_result.trigger_reason) or "- (no specific reason recorded)"

    patch_section = (
        f"- edited files: {patch_summary.get('files_count', 0)} "
        f"({', '.join(patch_summary.get('files', [])[:5]) or 'none'})"
        if patch_summary.get("has_patch")
        else "- no patch produced"
    )

    test_section = (
        f"```\n{test_feedback['excerpt']}\n```"
        if test_feedback.get("has_output")
        else "- (no local test output recorded)"
    )

    return f"""# Feedback Retry Packet

instance: `{instance_id}`
baseline: feedback_retry (packet_only)
trigger: {trigger_result.trigger_type} (confidence: {trigger_result.confidence})
runtime_gap_status: {trigger_result.runtime_gap_status}

## Previous Attempt Summary

The previous attempt produced a patch, but runtime-visible validation signals
indicate the repair may still be incomplete or incorrect. The trigger reasons
recorded by retry_trigger are:

{trigger_lines}

## Runtime Feedback

The following is the local test output seen by the agent during attempt_1.
No additional retrieval has been performed.

{test_section}

## Previous Patch Summary

{patch_section}
- added lines: {patch_summary.get('added_lines', 0)}
- removed lines: {patch_summary.get('removed_lines', 0)}

## Retry Instruction

Revise the previous patch using **only** the runtime feedback above.

Constraints:
- Do NOT broaden the repair scope unless the failure output directly supports it.
- Do NOT introduce edits to files that are not referenced by the failure output.
- Address the specific failing tests / errors visible in the runtime feedback.

(Note: this packet contains no ConDiag selected evidence, no gold context, and
no ContextBench metrics. It is a strict feedback-only baseline.)
"""


def handle_broad_expansion(
    run_dir: Path,
    instance_id: str,
    mode: str,
    adapter,
    config: dict,
) -> dict:
    """Broad Expansion handler — packet_only mode (D4-6).

    Flow:
      1. Resolve base_miniswe attempt_1 (same convention as feedback_retry)
      2. Copy attempt_1/* to this run's attempt_1/
      3. retry_trigger.classify -> RetryTriggerResult
      4. Always write retry_trigger_result.json
      5. If should_retry=True:
         - broad_expansion.expand_context(attempt_1, instance_id) -> candidates + report
         - write intervention/broad_candidates.jsonl
         - write intervention/expansion_report.json
         - write intervention/context_packet.md (generic template, NO diagnosis terms)
      6. If should_retry=False: intervention_report.status = "skipped_no_retry"
      7. final/* = attempt_1/* (packet_only)
      8. cost.json inherits from base

    config keys consumed:
        base_run_root        : Path or str
        manifest             : dict (fallback)
        expansion_budget     : dict (optional override)
        instance_metadata    : dict (optional, for issue keywords)
    """
    if mode == "dry-run":
        return {
            "handled": False,
            "reason": "dry_run_skeleton_only",
            "mode": mode,
            "instance_id": instance_id,
        }

    run_dir = Path(run_dir)
    attempt_1 = run_dir / "attempt_1"
    intervention = run_dir / "intervention"
    final = run_dir / "final"
    attempt_1.mkdir(parents=True, exist_ok=True)
    intervention.mkdir(parents=True, exist_ok=True)
    final.mkdir(parents=True, exist_ok=True)

    # 1. Resolve base_miniswe attempt_1 (same convention as feedback_retry)
    base_run_root = Path(config.get("base_run_root") or run_dir.parent.parent.parent)
    base_attempt_1 = base_run_root / "miniswe" / "base_miniswe" / instance_id / "attempt_1"
    base_cost = base_run_root / "miniswe" / "base_miniswe" / instance_id / "cost.json"

    base_exists = base_attempt_1.is_dir() and (base_attempt_1 / "runtime_signals.json").is_file()

    if base_exists:
        for src in base_attempt_1.iterdir():
            if src.is_file():
                shutil.copyfile(src, attempt_1 / src.name)
        rs = json.loads((attempt_1 / "runtime_signals.json").read_text(encoding="utf-8"))
        traj_source = "base_miniswe_attempt_1"
    else:
        manifest = config.get("manifest") or {}
        row = manifest.get(instance_id)
        if row is None:
            return {
                "handled": False,
                "reason": f"neither base_miniswe attempt_1 nor manifest row for {instance_id!r}",
                "mode": mode, "instance_id": instance_id,
            }
        traj_path = Path(row["traj_path"])
        adapter.build_case_bundle(
            raw_run_dir=traj_path.parent,
            instance_id=instance_id,
            out_dir=attempt_1,
        )
        rs = json.loads((attempt_1 / "runtime_signals.json").read_text(encoding="utf-8"))
        traj_source = f"manifest_fallback:{traj_path}"

    # 2. retry_trigger.classify
    from .retry_trigger import classify
    trigger_result = classify(rs)
    trigger_dict = trigger_result.to_dict()
    (intervention / "retry_trigger_result.json").write_text(
        json.dumps(trigger_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 3. Run generic expansion (always — even on NO_TRIGGER, for inspection)
    # but only build context_packet.md if should_retry=True
    from . import broad_expansion as be
    from .manifest_builder import get_problem_statement
    manifest_row = (config.get("manifest") or {}).get(instance_id) or {}
    repo_base_str = manifest_row.get("repo_base_path") or ""
    repo_base = Path(repo_base_str) if repo_base_str and Path(repo_base_str).is_dir() else None

    # Build instance_metadata: prefer explicit config (test fixtures), fall
    # back to SWE-bench Verified problem_statement so RG_ISSUE_KEYWORD_SEARCH
    # has real keywords to run.
    inst_meta = dict(config.get("instance_metadata") or {})
    if not inst_meta.get("issue"):
        issue = get_problem_statement(instance_id)
        if issue:
            inst_meta["issue"] = issue

    expansion = be.expand_context(
        attempt_1_dir=attempt_1,
        instance_id=instance_id,
        budget=config.get("expansion_budget"),
        instance_metadata=inst_meta,
        repo_base=repo_base,
    )
    candidates = expansion["candidates"]
    expansion_report = expansion["expansion_report"]

    be.write_candidates_jsonl(candidates, intervention / "broad_candidates.jsonl")
    (intervention / "expansion_report.json").write_text(
        json.dumps(expansion_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 4. context_packet.md only if should_retry=True
    packet_path = intervention / "context_packet.md"
    if trigger_result.should_retry:
        patch_summary = _summarize_previous_patch(attempt_1)
        test_feedback = _extract_test_feedback(attempt_1)
        packet_md = be.build_broad_packet(
            instance_id=instance_id,
            trigger_result=trigger_result,
            candidates=candidates,
            expansion_report=expansion_report,
            patch_summary=patch_summary,
            test_feedback=test_feedback,
        )
        packet_path.write_text(packet_md, encoding="utf-8")
        intervention_status = "expansion_packet_built"
    else:
        intervention_status = "skipped_no_retry"

    # packet_mode: distinguishes real-rg runs from broad_no_repo
    rg_executed = bool(expansion_report.get("rg_executed"))
    if trigger_result.should_retry:
        packet_mode = "broad_rg" if rg_executed else "broad_no_repo"
    else:
        packet_mode = "skipped_no_retry"

    intervention_report = {
        "schema_version": "condiag.intervention_report.v0",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "broad_expansion",
        "mode": "packet_only",
        "status": intervention_status,
        "packet_mode": packet_mode,
        "should_retry": trigger_result.should_retry,
        "trigger_type": trigger_result.trigger_type,
        "trigger_reason": trigger_result.trigger_reason,
        "runtime_gap_status": trigger_result.runtime_gap_status,
        "confidence": trigger_result.confidence,
        "alternative_trigger_types": trigger_result.alternative_trigger_types,
        "has_context_packet": packet_path.is_file(),
        "context_packet_path": str(packet_path) if packet_path.is_file() else None,
        "context_packet_kind": "generic_lexical_expansion" if packet_path.is_file() else None,
        "candidates_count": len(candidates),
        "sources_run": expansion_report.get("sources_run") or [],
        "by_source": expansion_report.get("by_source") or {},
        "rg_executed": rg_executed,
        "rg_hits_total": expansion_report.get("rg_hits_total") or 0,
        "repo_base": str(repo_base) if repo_base else None,
        "attempts_planned": 1 if not trigger_result.should_retry else 2,
        "attempt_2_executed": False,
        "source_attempt_1": str(base_attempt_1) if base_exists else traj_source,
        "created_at": _now_iso(),
    }
    (intervention / "intervention_report.json").write_text(
        json.dumps(intervention_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 5. final/* = attempt_1/* (packet_only)
    for sub in ["patch.diff", "runtime_signals.json"]:
        src = attempt_1 / sub
        if src.is_file():
            shutil.copyfile(src, final / sub)

    final_report = {
        "schema_version": "condiag.final_report.v0",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "broad_expansion",
        "mode": "packet_only",
        "selected_attempt": 1,
        "selected_attempt_dir": "attempt_1",
        "selection_reason": (
            "broad_expansion packet_only: context packet built but not yet "
            "consumed by a real attempt_2; final = attempt_1"
        ),
        "has_final_patch": (final / "patch.diff").is_file() and (final / "patch.diff").stat().st_size > 0,
        "contextbench_metrics_status": "pending_evaluation",
        "trigger_type": trigger_result.trigger_type,
        "should_retry": trigger_result.should_retry,
        "candidates_count": len(candidates),
        "finalized_at": _now_iso(),
    }
    (final / "final_report.json").write_text(
        json.dumps(final_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 6. cost.json inherits from base
    if base_cost.is_file():
        shutil.copyfile(base_cost, run_dir / "cost.json")
    else:
        manifest = config.get("manifest") or {}
        row = manifest.get(instance_id)
        if row:
            from .cost_extractor import extract_cost_from_traj
            cost_data = extract_cost_from_traj(
                traj_path=Path(row["traj_path"]),
                instance_id=instance_id,
                agent=adapter.name,
            )
            (run_dir / "cost.json").write_text(
                json.dumps(cost_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    return {
        "handled": True,
        "reason": "broad_expansion_packet_only",
        "mode": mode,
        "instance_id": instance_id,
        "trigger_type": trigger_result.trigger_type,
        "should_retry": trigger_result.should_retry,
        "intervention_status": intervention_status,
        "has_context_packet": packet_path.is_file(),
        "candidates_count": len(candidates),
        "sources_run": expansion_report.get("sources_run") or [],
        "source_attempt_1": str(base_attempt_1) if base_exists else traj_source,
        "attempt_1_status": "completed",
        "attempt_2_status": "skipped_packet_only_mode",
        "final_source": "attempt_1",
    }


def handle_condiag_packet_only(
    run_dir: Path,
    instance_id: str,
    mode: str,
    adapter,
    config: dict,
) -> dict:
    """ConDiag packet_only handler (D4-7).

    The ONLY intervention baseline allowed to import ConDiag core retrieval
    machinery. Produces typed/5R/diagnosis-style artifacts.

    Flow:
      1. Resolve base_miniswe attempt_1 (same convention as feedback_retry)
      2. Copy attempt_1/* to this run's attempt_1/
      3. retry_trigger.classify -> RetryTriggerResult
      4. Always write retry_trigger_result.json
      5. condiag_packet_only.run_packet_only():
         - synthesize_manual_diagnosis (auto-diagnoser v0)
         - normalize via diagnosis_normalizer
         - (optional) execute retrieval_plan if repo available
         - write executed_actions.json
         - write selected_evidence.json
         - write context_packet.md (ConDiag-flavored)
         - write recovery_report.json
      6. Write intervention/intervention_report.json (baseline-flavored wrapper)
      7. final/* = attempt_1/* (packet_only)
      8. cost.json inherits from base

    config keys consumed:
        base_run_root   : Path or str
        manifest        : dict (fallback if base_miniswe attempt_1 missing)
        taxonomy_path   : Path or str (default /mnt/d/.../pathology_taxonomy.json)
        repo_path       : Path or str (optional; v0 default = no repo)
        base_commit     : str (optional)
    """
    if mode == "dry-run":
        return {
            "handled": False,
            "reason": "dry_run_skeleton_only",
            "mode": mode,
            "instance_id": instance_id,
        }

    run_dir = Path(run_dir)
    attempt_1 = run_dir / "attempt_1"
    intervention = run_dir / "intervention"
    final = run_dir / "final"
    attempt_1.mkdir(parents=True, exist_ok=True)
    intervention.mkdir(parents=True, exist_ok=True)
    final.mkdir(parents=True, exist_ok=True)

    # 1. Resolve base_miniswe attempt_1
    base_run_root = Path(config.get("base_run_root") or run_dir.parent.parent.parent)
    base_attempt_1 = base_run_root / "miniswe" / "base_miniswe" / instance_id / "attempt_1"
    base_cost = base_run_root / "miniswe" / "base_miniswe" / instance_id / "cost.json"

    base_exists = base_attempt_1.is_dir() and (base_attempt_1 / "runtime_signals.json").is_file()

    if base_exists:
        for src in base_attempt_1.iterdir():
            if src.is_file():
                shutil.copyfile(src, attempt_1 / src.name)
        rs = json.loads((attempt_1 / "runtime_signals.json").read_text(encoding="utf-8"))
        traj_source = "base_miniswe_attempt_1"
    else:
        manifest = config.get("manifest") or {}
        row = manifest.get(instance_id)
        if row is None:
            return {
                "handled": False,
                "reason": f"neither base_miniswe attempt_1 nor manifest row for {instance_id!r}",
                "mode": mode, "instance_id": instance_id,
            }
        traj_path = Path(row["traj_path"])
        adapter.build_case_bundle(
            raw_run_dir=traj_path.parent,
            instance_id=instance_id,
            out_dir=attempt_1,
        )
        rs = json.loads((attempt_1 / "runtime_signals.json").read_text(encoding="utf-8"))
        traj_source = f"manifest_fallback:{traj_path}"

    # 2. retry_trigger.classify
    from .retry_trigger import classify
    trigger_result = classify(rs)
    trigger_dict = trigger_result.to_dict()
    (intervention / "retry_trigger_result.json").write_text(
        json.dumps(trigger_dict, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 3. ConDiag packet_only flow (writes executed_actions / selected_evidence /
    #    context_packet.md / recovery_report.json)
    from . import condiag_packet_only as cpo
    from .manifest_builder import get_problem_statement

    taxonomy_path = Path(
        config.get("taxonomy_path")
        or "/mnt/d/condiag-artifacts/condiag/v0/pathology_taxonomy.json"
    )
    # Repo path / base_commit: prefer explicit config, then manifest row
    manifest_row = (config.get("manifest") or {}).get(instance_id) or {}
    repo_base_str = config.get("repo_path") or manifest_row.get("repo_base_path") or ""
    repo_path = Path(repo_base_str) if repo_base_str and Path(repo_base_str).is_dir() else None
    base_commit = config.get("base_commit") or manifest_row.get("base_commit") or ""

    # Issue statement: prefer explicit config, then SWE-bench Verified lookup.
    # Mined for target_hints so REHYDRATE / FIND_NEIGHBOR_TESTS can match span
    # content against concept keywords (D4-7.1).
    issue = (config.get("instance_metadata") or {}).get("issue") or get_problem_statement(instance_id)

    model = manifest_row.get("model") or ""

    recovery_report = cpo.run_packet_only(
        attempt_1_dir=attempt_1,
        intervention_dir=intervention,
        instance_id=instance_id,
        agent=adapter.name,
        model=model,
        trigger_result=trigger_result,
        taxonomy_path=taxonomy_path,
        repo_path=repo_path,
        base_commit=base_commit,
        issue=issue,
    )

    # 4. Baseline-flavored intervention_report.json wrapper
    packet_path = intervention / "context_packet.md"
    intervention_status = (
        "condiag_packet_built" if trigger_result.should_retry
        else "condiag_packet_built_no_trigger"
    )

    # packet_mode: 6-class taxonomy (D4-8.5 Step 1)
    #   condiag_noop                         — NO_TRIGGER (nothing to do)
    #   condiag_abstain                      — trigger fired but auto-diagnoser
    #                                           fell back to ABSTAIN (insufficient
    #                                           evidence to act)
    #   condiag_diagnostic_only_no_repo      — repo_path missing / not resolved
    #   condiag_diagnostic_only_no_actions   — repo resolved but all actions
    #                                           skipped (done=0); the django-10880
    #                                           smoke case currently lives here
    #   condiag_retrieval                    — REHYDRATE / RETRIEVE / RELOCALIZE
    #                                           family action produced evidence
    #   condiag_guard                        — RESTRAIN family action executed
    #                                           (reserved; not produced in v0)
    diag = recovery_report.get("diagnosis") or {}
    ea_summary = recovery_report.get("executed_actions_summary") or {}
    packet_mode = _classify_condiag_packet_mode(
        trigger_type=trigger_result.trigger_type,
        pathology=diag.get("pathology", ""),
        action_family=diag.get("action_family", ""),
        primary_5r_action=diag.get("primary_5r_action", ""),
        repo_status=recovery_report.get("repo_status", ""),
        done_count=int(ea_summary.get("done") or 0),
    )

    intervention_report = {
        "schema_version": "condiag.intervention_report.v0",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "condiag_packet_only",
        "mode": "packet_only",
        "status": intervention_status,
        "packet_mode": packet_mode,
        "should_retry": trigger_result.should_retry,
        "trigger_type": trigger_result.trigger_type,
        "trigger_reason": trigger_result.trigger_reason,
        "runtime_gap_status": trigger_result.runtime_gap_status,
        "confidence": trigger_result.confidence,
        "alternative_trigger_types": trigger_result.alternative_trigger_types,
        "has_context_packet": packet_path.is_file(),
        "context_packet_path": str(packet_path) if packet_path.is_file() else None,
        "context_packet_kind": "condiag_typed_recovery" if packet_path.is_file() else None,
        "diagnosis": recovery_report.get("diagnosis") or {},
        "executed_actions_summary": recovery_report.get("executed_actions_summary") or {},
        "selected_evidence_count": recovery_report.get("selected_evidence_count") or 0,
        "repo_status": recovery_report.get("repo_status") or "no_repo_provided",
        "repo_base": str(repo_path) if repo_path else None,
        "recovery_report_path": str(intervention / "recovery_report.json"),
        "attempts_planned": 1 if not trigger_result.should_retry else 2,
        "attempt_2_executed": False,
        "source_attempt_1": str(base_attempt_1) if base_exists else traj_source,
        "created_at": _now_iso(),
    }
    (intervention / "intervention_report.json").write_text(
        json.dumps(intervention_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 5. final/* = attempt_1/* (packet_only)
    for sub in ["patch.diff", "runtime_signals.json"]:
        src = attempt_1 / sub
        if src.is_file():
            shutil.copyfile(src, final / sub)

    final_report = {
        "schema_version": "condiag.final_report.v0",
        "instance_id": instance_id,
        "agent": adapter.name,
        "baseline": "condiag_packet_only",
        "mode": "packet_only",
        "selected_attempt": 1,
        "selected_attempt_dir": "attempt_1",
        "selection_reason": (
            "condiag_packet_only: typed ContextPacket built but not yet "
            "consumed by a real attempt_2; final = attempt_1"
        ),
        "has_final_patch": (final / "patch.diff").is_file() and (final / "patch.diff").stat().st_size > 0,
        "contextbench_metrics_status": "pending_evaluation",
        "trigger_type": trigger_result.trigger_type,
        "should_retry": trigger_result.should_retry,
        "pathology": (recovery_report.get("diagnosis") or {}).get("pathology"),
        "primary_5r_action": (recovery_report.get("diagnosis") or {}).get("primary_5r_action"),
        "finalized_at": _now_iso(),
    }
    (final / "final_report.json").write_text(
        json.dumps(final_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 6. cost.json inherits from base
    if base_cost.is_file():
        shutil.copyfile(base_cost, run_dir / "cost.json")
    else:
        manifest = config.get("manifest") or {}
        row = manifest.get(instance_id)
        if row:
            from .cost_extractor import extract_cost_from_traj
            cost_data = extract_cost_from_traj(
                traj_path=Path(row["traj_path"]),
                instance_id=instance_id,
                agent=adapter.name,
            )
            (run_dir / "cost.json").write_text(
                json.dumps(cost_data, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    return {
        "handled": True,
        "reason": "condiag_packet_only",
        "mode": mode,
        "instance_id": instance_id,
        "trigger_type": trigger_result.trigger_type,
        "should_retry": trigger_result.should_retry,
        "intervention_status": intervention_status,
        "has_context_packet": packet_path.is_file(),
        "pathology": (recovery_report.get("diagnosis") or {}).get("pathology"),
        "primary_5r_action": (recovery_report.get("diagnosis") or {}).get("primary_5r_action"),
        "selected_evidence_count": recovery_report.get("selected_evidence_count") or 0,
        "repo_status": recovery_report.get("repo_status") or "no_repo_provided",
        "source_attempt_1": str(base_attempt_1) if base_exists else traj_source,
        "attempt_1_status": "completed",
        "attempt_2_status": "skipped_packet_only_mode",
        "final_source": "attempt_1",
    }


def handle_condiag_retry(
    run_dir: Path,
    instance_id: str,
    mode: str,
    adapter,
    config: dict,
) -> dict:
    """ConDiag retry handler (reserved; starts after D4-8 smoke passes)."""
    return {
        "handled": False,
        "reason": "reserved_after_D4_8",
        "mode": mode,
        "instance_id": instance_id,
    }


# ============================================================================
# ConDiag packet_mode classifier (6-class taxonomy, D4-8.5 Step 1)
# ============================================================================

# Operation prefixes considered "retrieval" family.
_RETRIEVAL_5R_ACTIONS = {"REHYDRATE", "RETRIEVE", "RELOCALIZE", "RECONCILE"}
# Operation prefixes considered "guard" family (RESTRAIN / scope guard).
_GUARD_5R_ACTIONS = {"RESTRAIN"}


def _classify_condiag_packet_mode(
    trigger_type: str,
    pathology: str,
    action_family: str,
    primary_5r_action: str,
    repo_status: str,
    done_count: int,
) -> str:
    """Classify a condiag_packet_only run into one of 6 packet_mode labels.

    Priority order matters — see docstring in handle_condiag_packet_only.
    """
    # 1. NO_TRIGGER → noop
    if trigger_type == "NO_TRIGGER":
        return "condiag_noop"

    # 2. ABSTAIN family / fallback pathology → abstain (signals insufficient)
    if action_family == "ABSTAIN" or pathology == "INSUFFICIENT_RUNTIME_EVIDENCE":
        return "condiag_abstain"

    # 3. repo missing or not resolved → no_repo
    if repo_status != "resolved":
        return "condiag_diagnostic_only_no_repo"

    # 4. at least one action produced evidence → split by family
    if done_count > 0:
        if primary_5r_action in _GUARD_5R_ACTIONS:
            return "condiag_guard"
        # REHYDRATE / RETRIEVE / RELOCALIZE / RECONCILE (and unknown) → retrieval
        return "condiag_retrieval"

    # 5. repo resolved but no action produced evidence → no_actions
    return "condiag_diagnostic_only_no_actions"


BASELINE_HANDLERS: dict[str, HandlerFn] = {
    "base_miniswe": handle_base_miniswe,
    "feedback_retry": handle_feedback_retry,
    "broad_expansion": handle_broad_expansion,
    "condiag_packet_only": handle_condiag_packet_only,
    "condiag_retry": handle_condiag_retry,
}


def get_handler(name: str) -> HandlerFn:
    if name not in BASELINE_HANDLERS:
        raise KeyError(
            f"unknown baseline '{name}'. registered: {sorted(BASELINE_HANDLERS.keys())}"
        )
    return BASELINE_HANDLERS[name]
