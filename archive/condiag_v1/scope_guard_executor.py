"""ConDiag Scope Guard Executor — runs control actions for RESTRAIN cases.

Control actions (taxonomy v0.2):
  - SCOPE_CONSTRAIN                  : derive restrain constraints from support map
  - PATCH_PRUNE_CANDIDATES           : delegate to patch_prune_suggester
  - RUN_RUNTIME_VISIBLE_VALIDATION   : run/replay visible tests (v0: skipped — no
                                       safe runtime validation command available)
  - REVALIDATE_EDIT_SCOPE            : re-run support check with relaxed criteria
                                       (v0: deferred — produces reminder note)

Each action produces an ActionResult analogous to retrieval_executor's:
  - status: done | skipped | no_candidates
  - candidates: list of constrained artifacts (constraint cards / prune items)
  - skipped_reason: when status != done
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import List

from . import patch_prune_suggester as pps
from .schemas import ManualDiagnosis, RuntimeSignals


@dataclass
class GuardCandidate:
    id: str
    operation: str
    kind: str               # "constraint" | "prune_candidate" | "review_candidate" | "keep_anchor"
    path: str = ""
    severity: str = ""      # "high" | "medium" | "low" | "info"
    reason: str = ""
    suggestion: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GuardActionResult:
    operation: str
    target: str
    budget: int
    status: str             # "done" | "skipped" | "no_candidates"
    candidates: List[GuardCandidate] = field(default_factory=list)
    skipped_reason: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "target": self.target,
            "budget": self.budget,
            "status": self.status,
            "candidate_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
            "skipped_reason": self.skipped_reason,
            "notes": self.notes,
        }


# ===== individual operations =====

def scope_constrain(
    support_map: dict,
    patch_scope_report: dict,
    rs: RuntimeSignals,
) -> GuardActionResult:
    """Derive high-level restrain constraints from the support map + scope report."""
    candidates: list[GuardCandidate] = []
    cid = 0

    # Constraint 1: cap file count
    edited_count = int(support_map.get("edited_files_count", 0))
    if edited_count >= 5:
        candidates.append(GuardCandidate(
            id=f"C{cid}",
            operation="SCOPE_CONSTRAIN",
            kind="constraint",
            severity="high",
            reason=f"Patch edits {edited_count} files. Issue scope typically requires <=3 files.",
            suggestion="Restrict the retry patch to files with direct issue/test/stack support.",
            extra={"edited_files_count": edited_count},
        ))
        cid += 1

    # Constraint 2: prune pattern-only files
    unsupported = list(support_map.get("unsupported", []) or [])
    if unsupported:
        candidates.append(GuardCandidate(
            id=f"C{cid}",
            operation="SCOPE_CONSTRAIN",
            kind="constraint",
            severity="high",
            reason=f"{len(unsupported)} of {edited_count} edited files are pattern-only or unsupported.",
            suggestion="Drop mechanical pattern edits before retry.",
            extra={"unsupported_files": unsupported},
        ))
        cid += 1

    # Constraint 3: force visible validation before submission
    if rs.submitted_without_tests:
        candidates.append(GuardCandidate(
            id=f"C{cid}",
            operation="SCOPE_CONSTRAIN",
            kind="constraint",
            severity="high",
            reason="Previous attempt submitted without running any visible tests.",
            suggestion="Run the project's visible test suite at least once before submitting.",
            extra={"submitted_without_tests": True},
        ))
        cid += 1

    # Constraint 4: repeated-edit-pattern ban
    patterns = patch_scope_report.get("repeated_edit_patterns", []) or []
    if patterns:
        top = sorted(patterns, key=lambda p: p.get("file_count", 0), reverse=True)[:3]
        candidates.append(GuardCandidate(
            id=f"C{cid}",
            operation="SCOPE_CONSTRAIN",
            kind="constraint",
            severity="medium",
            reason=f"Detected {len(patterns)} repeated edit pattern(s) across files.",
            suggestion="Avoid applying the same transformation broadly. Each edit must be justified independently.",
            extra={"top_patterns": [{"before": p.get("before"), "after": p.get("after"), "file_count": p.get("file_count")} for p in top]},
        ))
        cid += 1

    return GuardActionResult(
        operation="SCOPE_CONSTRAIN",
        target="restrain-constraint-set",
        budget=5,
        status="done" if candidates else "no_candidates",
        candidates=candidates,
        notes=[f"derived {len(candidates)} constraint(s) from support_map + scope_report"],
    )


def patch_prune(
    support_map: dict,
    instance_id: str,
    patch_scope_report: dict | None = None,
) -> tuple[GuardActionResult, dict]:
    """Delegate to patch_prune_suggester; return both ActionResult and full report."""
    prune_report = pps.suggest(instance_id, support_map, patch_scope_report)
    candidates: list[GuardCandidate] = []
    for i, s in enumerate(prune_report.suggestions):
        severity = {"drop": "high", "review": "medium", "keep": "info"}.get(s["action"], "low")
        kind = {"drop": "prune_candidate", "review": "review_candidate", "keep": "keep_anchor"}[s["action"]]
        candidates.append(GuardCandidate(
            id=f"P{i}",
            operation="PATCH_PRUNE_CANDIDATES",
            kind=kind,
            path=s["path"],
            severity=severity,
            reason=s["reason"],
            suggestion=s["suggestion"],
            extra={
                "current_verdict": s["current_verdict"],
                "anti_support_signals": s["anti_support_signals"],
                "matched_target_hints": s["matched_target_hints"],
            },
        ))
    result = GuardActionResult(
        operation="PATCH_PRUNE_CANDIDATES",
        target=f"{len(prune_report.prune_candidates)} prune / {len(prune_report.review_candidates)} review / {len(prune_report.keep_candidates)} keep",
        budget=5,
        status="done" if candidates else "no_candidates",
        candidates=candidates,
        notes=[
            f"prune_ratio={prune_report.prune_ratio}",
            f"keep_candidates={prune_report.keep_candidates}",
        ],
    )
    return result, prune_report.to_dict()


def run_runtime_visible_validation(rs: RuntimeSignals) -> GuardActionResult:
    """v0: skip — we cannot safely run official test commands from the
    manual-guard flow, and runtime_signals may not carry a sanitized
    visible-validation command. Surface the situation honestly rather
    than fake-running tests.
    """
    test_cmds = list(rs.test_commands or [])
    skipped_reason = (
        "No safe visible validation command available in runtime_signals "
        "(test_commands is empty)."
        if not test_cmds
        else "Visible validation deferred — would run: " + "; ".join(test_cmds[:2])
    )
    return GuardActionResult(
        operation="RUN_RUNTIME_VISIBLE_VALIDATION",
        target="runtime-visible-validation",
        budget=2,
        status="skipped",
        skipped_reason=skipped_reason,
        notes=["v0: deferred until runtime-visible validator module exists"],
    )


def revalidate_edit_scope() -> GuardActionResult:
    """v0: placeholder — produces a reminder to revisit after retry."""
    return GuardActionResult(
        operation="REVALIDATE_EDIT_SCOPE",
        target="post-retry-scope-revalidation",
        budget=2,
        status="skipped",
        skipped_reason="Revalidation happens after the retry attempt, not in this flow.",
        notes=["v0: deferred to post-retry hook"],
    )


# ===== dispatcher =====

OPERATIONS = {
    "SCOPE_CONSTRAIN": scope_constrain,
    "PATCH_PRUNE_CANDIDATES": patch_prune,
    "RUN_RUNTIME_VISIBLE_VALIDATION": run_runtime_visible_validation,
    "REVALIDATE_EDIT_SCOPE": revalidate_edit_scope,
}

SYNTHESIS_ACTIONS = {
    # control-side synthesis actions handled by context_packet_builder
    "SCOPE_CONSTRAIN_AND_REPAIR",
}


def execute_plan(
    retrieval_plan: list[dict],
    rs: RuntimeSignals,
    md: ManualDiagnosis,
    support_map: dict,
    patch_scope_report: dict,
    instance_id: str,
) -> tuple[list[GuardActionResult], dict]:
    """Run all control actions in retrieval_plan; return (results, prune_report_dict).

    Unknown / retrieval-side / synthesis operations are tolerated and recorded
    as 'skipped' with a reason — they belong to a different flow.
    """
    results: list[GuardActionResult] = []
    prune_report_dict: dict = {}

    for entry in retrieval_plan or []:
        op_name = entry.get("operation", "")
        if op_name in SYNTHESIS_ACTIONS:
            results.append(GuardActionResult(
                operation=op_name,
                target=entry.get("target", ""),
                budget=int(entry.get("budget", 0) or 0),
                status="done",
                candidates=[],
                notes=["synthesis action — handled by context_packet_builder; no scope-guard work needed"],
            ))
            continue

        fn = OPERATIONS.get(op_name)
        if fn is None:
            results.append(GuardActionResult(
                operation=op_name,
                target=entry.get("target", ""),
                budget=int(entry.get("budget", 0) or 0),
                status="skipped",
                skipped_reason=f"unknown_or_non_control_operation: {op_name}",
                notes=["Operation is a retrieval-side action or not in scope_guard_executor's enum."],
            ))
            continue

        if op_name == "SCOPE_CONSTRAIN":
            r = fn(support_map, patch_scope_report, rs)
        elif op_name == "PATCH_PRUNE_CANDIDATES":
            r, prune_report_dict = fn(support_map, instance_id, patch_scope_report)
        elif op_name == "RUN_RUNTIME_VISIBLE_VALIDATION":
            r = fn(rs)
        else:
            r = fn()
        results.append(r)

    return results, prune_report_dict
