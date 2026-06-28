"""ConDiag context packet builder — render selected_evidence into a markdown
packet ready to inject into a mini-SWE-Agent retry.

Sections:
  1. Diagnosis (pathology + 5R action + retry_intent)
  2. Runtime failure evidence (visible target fixes + visible regressions)
  3. Retrieved evidence (each evidence item with code snippet)
  4. Repair constraints (derived from retry_intent + relation types)
  5. Retry instruction

Reads source snippets from repo@base_commit via repository_index.read_span.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from . import repository_index as ri
from .schemas import ManualDiagnosis, NormalizedDiagnosis, RuntimeSignals


# Per-relation short labels shown next to each evidence item
_RELATION_LABEL = {
    "visible_regression_test": "Regression test",
    "target_fix_test": "Target fix test",
    "target_symbol_definition": "Target symbol definition",
    "enclosing_class_definition": "Enclosing class",
    "sibling_method_implementation": "Sibling implementation",
    "previously_seen_but_dropped": "Rehydrated (previously seen)",
    "neighbor_test_by_concept": "Neighbor test",
}


def _label(relation: str) -> str:
    return _RELATION_LABEL.get(relation, relation)


def _format_snippet(source: str, start: int) -> str:
    """Indent source lines and prefix with line numbers."""
    out = []
    for i, line in enumerate(source.splitlines()):
        out.append(f"L{start + i:>5} | {line}")
    return "\n".join(out)


def _repair_constraints(nd: NormalizedDiagnosis, selected: dict) -> List[str]:
    """Generate constraint bullets derived from retry_intent + relation types."""
    constraints: list[str] = []
    relations = {e["relation"] for e in selected.get("evidence", [])}

    if nd.primary_5r_action == "RECONCILE":
        constraints.append("Preserve the behavior required by the visible regression tests; do not introduce new failures.")
        constraints.append("Re-run the visible regression tests before submission.")
        if "sibling_method_implementation" in relations:
            constraints.append("Do not let the target class's override diverge from sibling classes that implement the same method.")
        if "enclosing_class_definition" in relations:
            constraints.append("Anchor the fix on the correct class so composition expressions (e.g. x*0, x-y) still resolve through the parent.")

    elif nd.primary_5r_action == "REHYDRATE":
        constraints.append("Use the previously seen but dropped spans as primary evidence.")
        constraints.append("Avoid re-exploring the same files; the answer is in the rehydrated context.")

    elif nd.primary_5r_action == "RETRIEVE":
        constraints.append("Audit sibling logic and parallel implementations; do not assume a single-class fix is enough.")
        constraints.append("Re-run neighbor tests after the patch.")

    elif nd.primary_5r_action == "RESTRAIN":
        constraints.append("Restrict the patch to files with direct failure/issue support.")
        constraints.append("Prune edits that lack independent evidence (issue mention, failing test, or stack trace).")

    # Always: avoid over-exploring
    constraints.append("Prefer a minimal patch.")

    return constraints


def build_context_packet_md(
    repo_root: Path,
    nd: NormalizedDiagnosis,
    md: ManualDiagnosis,
    rs: RuntimeSignals,
    selected: dict,
) -> str:
    """Render the final markdown context packet."""
    trigger = md.trigger_assessment or {}
    visible_target_fixes = trigger.get("visible_target_fixes", []) or []
    visible_regressions = trigger.get("visible_regressions", []) or []

    lines: list[str] = []

    # ----- 1. Header / Diagnosis -----
    lines.append("# ConDiag Context Packet")
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    lines.append(f"- **Instance**: `{nd.instance_id}`")
    lines.append(f"- **Pathology**: `{nd.pathology}`")
    lines.append(f"- **5R action**: `{nd.primary_5r_action}`  (action family: `{nd.action_family}`)")
    lines.append(f"- **Retry intent**: `{nd.retry_intent}`")
    lines.append(f"- **Confidence**: {nd.confidence}")
    lines.append(f"- **Repo**: `{repo_root}` @ base_commit (clean tree)")
    lines.append("")

    # ----- 2. Runtime Failure Evidence -----
    lines.append("## Runtime Failure Evidence")
    lines.append("")
    diag_text = {
        "REGRESSION_AFTER_PARTIAL_FIX":
            "The previous attempt appears to be a partial targeted fix that introduced visible regression failures.",
        "OVER_EXPLORE_OVER_EDIT":
            "The previous attempt over-explored and over-edited beyond the issue scope.",
        "EXPLORE_OK_EDIT_MISALIGNED":
            "The previous attempt explored relevant evidence but failed to align the final patch with it.",
        "UNDER_EDIT_PARTIAL_FIX":
            "The previous attempt was too narrow; sibling logic was not audited.",
    }.get(nd.pathology, "The previous attempt did not resolve the issue.")
    lines.append(diag_text)
    lines.append("")

    if visible_target_fixes:
        lines.append("**Target tests that the previous attempt did fix:**")
        for t in visible_target_fixes:
            lines.append(f"- `{t}`")
        lines.append("")
    if visible_regressions:
        lines.append("**Regressions introduced by the previous attempt:**")
        for t in visible_regressions:
            lines.append(f"- `{t}`")
        lines.append("")
    if rs.test_failures:
        lines.append("**Local test failures parsed from agent's own pytest output:**")
        for t in rs.test_failures[:10]:
            lines.append(f"- `{t}`")
        lines.append("")

    # ----- 3. Retrieved Evidence -----
    lines.append("## Retrieved Evidence")
    lines.append("")
    lines.append(f"_{len(selected.get('evidence', []))} evidence items selected "
                 f"({selected.get('selection_summary', {}).get('selected_lines_total', 0)} lines total)._")
    lines.append("")

    for ev in selected.get("evidence", []):
        rel = ev.get("relation", "")
        op = ev.get("operation", "")
        eid = ev.get("id", "?")
        path = ev.get("path", "?")
        start = int(ev.get("start_line", 0))
        end = int(ev.get("end_line", 0))
        symbol = ev.get("symbol", "")
        already = ev.get("already_seen")
        score = ev.get("score", 0)
        why = ev.get("why", "")

        lines.append(f"### {eid}. {_label(rel)}  `{symbol}`" if symbol else f"### {eid}. {_label(rel)}")
        lines.append("")
        lines.append(f"- **Operation**: `{op}`")
        lines.append(f"- **File**: `{path}`  lines {start}-{end}")
        if already:
            lines.append(f"- **Previously seen**: yes (re-activated by ConDiag)")
        lines.append(f"- **Score**: {score}")
        if why:
            lines.append(f"- **Why**: {why}")
        lines.append("")
        # Snippet
        snippet_src = ri.read_span(Path(repo_root), path, start, end, context=0)
        if snippet_src:
            lines.append("```python")
            lines.append(_format_snippet(snippet_src, start))
            lines.append("```")
            lines.append("")

    # ----- 4. Repair Constraints -----
    lines.append("## Repair Constraints")
    lines.append("")
    for c in _repair_constraints(nd, selected):
        lines.append(f"- {c}")
    lines.append("")

    # ----- 5. Retry Instruction -----
    lines.append("## Retry Instruction")
    lines.append("")
    if nd.context_packet_instruction:
        lines.append("```")
        lines.append(nd.context_packet_instruction)
        lines.append("```")
    else:
        lines.append(f"Apply the {nd.primary_5r_action} action plan to reconcile the previous patch with the constraints above.")
    lines.append("")

    return "\n".join(lines)


# ===== RESTRAIN / Scope Guard template =====

def _severity_emoji(sev: str) -> str:
    return {"high": "[HIGH]", "medium": "[MED ]", "low": "[LOW ]", "info": "[INFO]"}.get(sev, "[ ?? ]")


def build_context_packet_md_guard(
    repo_root: Path,
    nd: NormalizedDiagnosis,
    md: ManualDiagnosis,
    rs: RuntimeSignals,
    support_map: dict,
    patch_scope_report: dict,
    guard_results: list[dict],
    prune_report: dict,
) -> str:
    """Render the RESTRAIN / Scope Guard markdown packet.

    Sections:
      1. Diagnosis
      2. Runtime Failure Evidence (scope shape + validation status)
      3. Scope Guard Evidence (supported / weak / unsupported breakdown)
      4. Repair Constraints (derived from guard actions)
      5. Retry Instruction
    """
    lines: list[str] = []

    # ----- 1. Header / Diagnosis -----
    lines.append("# ConDiag Context Packet (Scope Guard)")
    lines.append("")
    lines.append("## Diagnosis")
    lines.append("")
    lines.append(f"- **Instance**: `{nd.instance_id}`")
    lines.append(f"- **Pathology**: `{nd.pathology}`")
    lines.append(f"- **5R action**: `{nd.primary_5r_action}`  (action family: `{nd.action_family}`)")
    lines.append(f"- **Retry intent**: `{nd.retry_intent}`")
    lines.append(f"- **Confidence**: {nd.confidence}")
    lines.append(f"- **Repo**: `{repo_root}` @ base_commit (clean tree)")
    lines.append("")

    # ----- 2. Runtime Failure Evidence -----
    lines.append("## Runtime Failure Evidence")
    lines.append("")
    diag_text = {
        "OVER_EXPLORE_OVER_EDIT":
            "The previous attempt likely over-generalized a broad replacement pattern across too many files without validation.",
        "UNDER_EDIT_PARTIAL_FIX":
            "The previous attempt was too narrow; sibling logic was not audited.",
        "EXPLORE_OK_EDIT_MISALIGNED":
            "The previous attempt explored relevant evidence but failed to align the final patch with it.",
    }.get(nd.pathology, "The previous attempt did not resolve the issue.")
    lines.append(diag_text)
    lines.append("")

    scope_score = patch_scope_report.get("scope_anomaly_score", 0)
    scope_level = patch_scope_report.get("scope_anomaly_level", "none")
    lines.append("**Patch shape (runtime-visible):**")
    lines.append(f"- Changed files: **{patch_scope_report.get('changed_files_count', 0)}**")
    lines.append(f"- Changed lines: +{patch_scope_report.get('changed_lines_added', 0)} / -{patch_scope_report.get('changed_lines_removed', 0)} (total {patch_scope_report.get('changed_lines_total', 0)})")
    lines.append(f"- Scope anomaly score: **{scope_score}** (level: **{scope_level}**)")
    lines.append(f"- Submitted without tests: **{patch_scope_report.get('submitted_without_tests', False)}**  (test runs: {patch_scope_report.get('test_runs_count', 0)})")
    lines.append("")

    patterns = patch_scope_report.get("repeated_edit_patterns", []) or []
    if patterns:
        lines.append("**Repeated edit patterns detected:**")
        for p in patterns[:4]:
            before = p.get("before", "")
            after = p.get("after", "")
            n = p.get("file_count", 0)
            display = f"`{before}` -> `{after}`" if before else f"`{after!r}` inserted"
            lines.append(f"- files={n}: {display}")
        lines.append("")

    # ----- 3. Scope Guard Evidence -----
    lines.append("## Scope Guard Evidence")
    lines.append("")
    supported = support_map.get("supported", []) or []
    weak = support_map.get("weak", []) or []
    unsupported = support_map.get("unsupported", []) or []
    files = support_map.get("files", []) or []
    lines.append(f"_{len(files)} edited files analyzed: "
                 f"{len(supported)} supported, {len(weak)} weak, {len(unsupported)} unsupported._")
    lines.append("")

    by_verdict = {"supported": [], "weak": [], "unsupported": []}
    for f in files:
        v = f.get("support", "unsupported")
        by_verdict.setdefault(v, []).append(f)

    for label in ("supported", "weak", "unsupported"):
        items = by_verdict.get(label, [])
        if not items:
            continue
        icon = {"supported": "[KEEP]", "weak": "[REV ]", "unsupported": "[DROP]"}[label]
        lines.append(f"### {icon} {label.capitalize()} ({len(items)})")
        lines.append("")
        for f in items:
            path = f.get("path", "?")
            sources = f.get("support_sources", []) or []
            anti = f.get("anti_support_signals", []) or []
            hints = f.get("matched_target_hints", []) or []
            reason = f.get("reason", "")
            bits = []
            if hints:
                bits.append(f"target_hints={hints}")
            if sources:
                bits.append(f"sources={sources}")
            if anti:
                bits.append(f"anti={anti[:2]}")
            lines.append(f"- `{path}`")
            if bits:
                lines.append(f"  - {'; '.join(bits)}")
            if reason:
                lines.append(f"  - {reason}")
        lines.append("")

    # Prune summary
    if prune_report:
        prune_ratio = prune_report.get("prune_ratio", 0)
        prune_list = prune_report.get("prune_candidates", []) or []
        review_list = prune_report.get("review_candidates", []) or []
        keep_list = prune_report.get("keep_candidates", []) or []
        lines.append(f"### Prune summary (ratio {prune_ratio})")
        lines.append("")
        lines.append(f"- **Drop**: {len(prune_list)} file(s)")
        lines.append(f"- **Review**: {len(review_list)} file(s)")
        lines.append(f"- **Keep**: {len(keep_list)} file(s) — anchor your retry patch here")
        if keep_list:
            lines.append("")
            lines.append("Anchor files:")
            for p in keep_list:
                lines.append(f"- `{p}`")
        lines.append("")

    # ----- 4. Repair Constraints -----
    lines.append("## Repair Constraints")
    lines.append("")
    for c in _repair_constraints(nd, {"evidence": []}):
        lines.append(f"- {c}")
    lines.append("")
    # Add guard-derived constraints
    for r in guard_results:
        if r.get("operation") != "SCOPE_CONSTRAIN":
            continue
        for cand in r.get("candidates", []) or []:
            sev = cand.get("severity", "medium")
            reason = cand.get("reason", "")
            suggestion = cand.get("suggestion", "")
            lines.append(f"- {_severity_emoji(sev)} {reason}")
            lines.append(f"  - Action: {suggestion}")
    lines.append("")

    # ----- 5. Retry Instruction -----
    lines.append("## Retry Instruction")
    lines.append("")
    if nd.context_packet_instruction:
        lines.append("```")
        lines.append(nd.context_packet_instruction)
        lines.append("```")
    else:
        lines.append(
            "Rebuild the patch from the supported edit set only. "
            "Drop or reconsider edits that are only justified by broad lexical matching. "
            "Run visible validation before submitting."
        )
    lines.append("")

    return "\n".join(lines)
