"""ConDiag context packet builder — render selected_evidence into a markdown
packet ready to inject into a mini-SWE-Agent retry.

v1 (2026-06-29): Compression, taxonomy removal, evidence filtering,
Primary Edit Target, and concrete retry instructions.

Reads source snippets from repo@base_commit via repository_index.read_span.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from . import repository_index as ri
from .schemas import ManualDiagnosis, NormalizedDiagnosis, RuntimeSignals

# ---------------------------------------------------------------------------
# v1 compression constants
# ---------------------------------------------------------------------------
MAX_PACKET_CHARS = 5000
MAX_EVIDENCE_LINES_PER_ITEM = 40
MAX_TOTAL_CODE_LINES = 120
MIN_EVIDENCE_SCORE = 0.85

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

# Internal taxonomy / instruction terms that must NOT appear in agent-facing output
_BAD_INSTRUCTION_TERMS = [
    "REHYDRATE", "RETRIEVE", "RECONCILE", "RESTRAIN", "RELOCALIZE",
    "5R", "action plan", "action family",
    "EXPLORE_OK_EDIT_MISALIGNED", "OVER_EXPLORE_OVER_EDIT",
    "UNDER_EDIT_PARTIAL_FIX", "REGRESSION_AFTER_PARTIAL_FIX",
    "REHYDRATE_SEEN_EVIDENCE", "RETRIEVE_SIBLING_LOGIC",
    "RESTRAIN_OVER_EDIT", "RECONCILE_REGRESSION",
]


def _is_internal_instruction(text: str) -> bool:
    """Return True if text contains internal taxonomy/instruction terms."""
    up = text.upper()
    return any(t.upper() in up for t in _BAD_INSTRUCTION_TERMS)


def _label(relation: str) -> str:
    return _RELATION_LABEL.get(relation, relation)


def _format_snippet(source: str, start: int) -> str:
    """Indent source lines and prefix with line numbers."""
    out = []
    for i, line in enumerate(source.splitlines()):
        out.append(f"L{start + i:>5} | {line}")
    return "\n".join(out)


def _filter_evidence(evidence: list, min_score: float = MIN_EVIDENCE_SCORE) -> list:
    """Filter out low-score and false-positive evidence items.

    Rules:
    - Score must be >= min_score (0.85 by default)
    - neighbor_test_by_concept must have a meaningful "why" field (keyword match)
    """
    filtered = []
    for ev in evidence:
        score = ev.get("score", 0)
        if score < min_score:
            continue
        relation = ev.get("relation", "")
        if relation == "neighbor_test_by_concept":
            why = ev.get("why", "")
            symbol = ev.get("symbol", "")
            # Require at least one signal beyond raw score: a "why" reason
            # that references issue keywords, or a non-empty symbol
            if not why and not symbol:
                continue
            if why and len(why) < 5:
                continue
        filtered.append(ev)
    return filtered


def _clean_retry_intent(raw: str) -> str:
    """Convert taxonomy enum values to plain language; filter out internal terms.

    Order: enum mapping first (so REHYDRATE_SEEN_EVIDENCE becomes plain language),
    then check if the RESULT still contains internal terms.
    """
    if not raw:
        return ""
    # Step 1: convert known taxonomy enums to plain language
    mapping = {
        "REHYDRATE_SEEN_EVIDENCE":
            "Re-apply the key evidence the previous attempt saw but did not use in the final patch.",
        "RECONCILE_REGRESSION":
            "Fix the regression failures while preserving the partial fix that already passes.",
        "RETRIEVE_SIBLING_LOGIC":
            "Audit and apply sibling implementations to cover all related code paths.",
        "RESTRAIN_OVER_EDIT":
            "Narrow the patch to only the changes with direct evidence support.",
        "NOOP":
            "No changes needed.",
    }
    if raw in mapping:
        return mapping[raw]
    # Step 2: for other ALL_CAPS_UNDERSCORES strings, do basic cleanup
    if raw.isupper() and "_" in raw:
        result = raw.replace("_", " ").capitalize()
    else:
        result = raw
    # Step 3: reject if the RESULT contains internal taxonomy terms
    if _is_internal_instruction(result):
        return ""
    return result


def _extract_symbols_from_span(
    repo_root: Path, path: str, start: int, end: int
) -> list[str]:
    """Scan a source span for class/function definitions.

    Returns a list of symbol names found, ordered by line position.
    """
    snippet = ri.read_span(repo_root, path, start, end, context=0)
    if not snippet:
        return []
    symbols = []
    for line in snippet.splitlines():
        stripped = line.strip()
        for prefix in ("class ", "def "):
            if stripped.startswith(prefix):
                name = stripped[len(prefix):].split("(")[0].split(":")[0].strip()
                if name and not name.startswith("_"):
                    symbols.append(name)
    return symbols


def _primary_target_score(
    ev: dict,
    deficiency_type: str,
    target_hints: list,
    edited_set: set,
) -> float:
    """Compute primary edit target score for a single evidence item.

    Factors:
      + relation type bonus (per diagnosis type)
      + narrow span bonus (shorter = more precise)
      - imports region penalty (lines < 50, likely boilerplate)
      - generic symbol penalty (Exception/Warning/Error)
      + patch relevance (path was in attempt_1 edited_files)
      - old symptom penalty (ROOT_CAUSE_RELOC + previously seen)
      + issue keyword overlap (path stem matches target_hints)
    """
    rel = ev.get("relation", "")
    start = int(ev.get("start_line", 0))
    end = int(ev.get("end_line", 0))
    span_width = abs(end - start)
    path = ev.get("path", "")
    symbol = ev.get("symbol", "") or ""

    ts = 0.0

    # --- Relation type bonus per deficiency type ---
    type_bonus_map = {
        "ROOT_CAUSE_RELOCALIZATION": {
            "target_symbol_definition": 0.40,
            "enclosing_class_definition": 0.25,
            "sibling_method_implementation": 0.25,
            "neighbor_test_by_concept": 0.15,
            "previously_seen_but_dropped": -0.30,
        },
        "INTERFACE_CONSTRAINT_CONTEXT": {
            "target_symbol_definition": 0.40,
            "sibling_method_implementation": 0.20,
            "enclosing_class_definition": 0.20,
            "previously_seen_but_dropped": -0.15,
        },
        "REGRESSION_CONSTRAINT_CONTEXT": {
            "visible_regression_test": 0.40,
            "target_fix_test": 0.30,
            "target_symbol_definition": 0.20,
            "previously_seen_but_dropped": -0.10,
        },
        "RELATED_TEST_CONTEXT": {
            "target_fix_test": 0.35,
            "neighbor_test_by_concept": 0.30,
            "visible_regression_test": 0.25,
            "previously_seen_but_dropped": -0.10,
        },
        "EDIT_SCOPE_CONTEXT": {
            "previously_seen_but_dropped": 0.20,
            "target_symbol_definition": -0.10,
        },
        "API_DEFINITION_CONTEXT": {
            "target_symbol_definition": 0.40,
            "enclosing_class_definition": 0.25,
            "previously_seen_but_dropped": -0.20,
        },
        "CALLER_CALLEE_CONTEXT": {
            "target_symbol_definition": 0.30,
            "enclosing_class_definition": 0.25,
            "previously_seen_but_dropped": -0.20,
        },
    }
    ts += type_bonus_map.get(deficiency_type, {}).get(rel, 0.0)

    # --- Narrow span bonus ---
    if span_width <= 15:
        ts += 0.25
    elif span_width <= 30:
        ts += 0.15
    elif span_width <= 60:
        ts += 0.05

    # --- Broad span penalty ---
    if span_width > 100:
        ts -= 0.20
    elif span_width > 60:
        ts -= 0.10

    # --- Imports/header region penalty ---
    if start < 50 and span_width <= 50:
        ts -= 0.30

    # --- Generic symbol penalty ---
    if symbol and any(
        symbol.endswith(suffix) for suffix in ("Exception", "Warning", "Error")
    ):
        ts -= 0.35

    # --- Patch relevance ---
    if path in edited_set:
        ts += 0.15

    # --- Old symptom penalty (relocalization + previously_seen) ---
    if deficiency_type == "ROOT_CAUSE_RELOCALIZATION" and rel == "previously_seen_but_dropped":
        ts -= 0.40

    # --- Issue keyword overlap ---
    if target_hints and path:
        path_stem = Path(path).stem.lower()
        for h in target_hints:
            hint_val = ""
            if isinstance(h, dict):
                hint_val = h.get("value", "")
            elif isinstance(h, str):
                hint_val = h
            else:
                continue
            if hint_val and hint_val.lower() in path_stem:
                ts += 0.10

    return ts


def _primary_edit_target(
    nd: NormalizedDiagnosis, selected: dict, repo_root: Path,
    edited_files: Optional[list] = None,
) -> Optional[dict]:
    """Derive the primary edit target using a composite target_score.

    Unlike evidence_selector which picks top-k evidence for the packet,
    this function computes an independent target_score for each evidence
    item so the primary edit target isn't dominated by REHYDRATE items.

    Returns a dict with {file, symbol, start_line, end_line, what} or None.
    """
    evidence = selected.get("evidence", []) or []
    target_hints = nd.target_hints or []
    deficiency_type = nd.context_deficiency_type or ""
    edited_set = set(str(f) for f in (edited_files or []))

    # 1. Score all non-test evidence items
    scored: list[tuple[float, dict]] = []
    for ev in evidence:
        if "test" in ev.get("relation", ""):
            continue
        ts = _primary_target_score(ev, deficiency_type, target_hints, edited_set)
        scored.append((ts, ev))

    if not scored:
        return None

    # 2. Rank by score, pick best
    scored.sort(key=lambda x: -x[0])
    best_ts, best_ev = scored[0]
    del best_ts  # unused beyond debug

    # 3. Build target dict from best evidence
    file = best_ev.get("path", "")
    evidence_symbol = best_ev.get("symbol") or ""

    # 4. Symbol fallback: scan source span for class/def names
    if not evidence_symbol:
        path = best_ev.get("path", "")
        start = int(best_ev.get("start_line", 0))
        end = int(best_ev.get("end_line", 0))
        span_symbols = _extract_symbols_from_span(repo_root, path, start, end)
        hint_keywords = []
        for h in target_hints:
            if isinstance(h, dict):
                val = h.get("value", "")
            elif isinstance(h, str):
                val = h
            else:
                continue
            if val:
                hint_keywords.append(val.lower())
        if hint_keywords and span_symbols:
            matched = [s for s in span_symbols
                       if any(kw in s.lower() for kw in hint_keywords)]
            if matched:
                evidence_symbol = matched[0]
        if not evidence_symbol and span_symbols:
            classes = [s for s in span_symbols if s[0].isupper()]
            evidence_symbol = classes[0] if classes else span_symbols[0]

    target = {
        "file": file,
        "symbol": evidence_symbol,
        "start_line": best_ev.get("start_line", 0),
        "end_line": best_ev.get("end_line", 0),
    }

    # 5. Build plain-language "what to do" from retry_intent and hints
    cleaned_intent = _clean_retry_intent(nd.retry_intent or "")
    if cleaned_intent:
        target["what"] = cleaned_intent
    elif target_hints:
        hint_strs = []
        for h in target_hints[:3]:
            if isinstance(h, dict):
                hint_strs.append(str(h.get("value", h)))
            else:
                hint_strs.append(str(h))
        target["what"] = "; ".join(hint_strs)
    elif evidence_symbol:
        target["what"] = (
            f"Inspect `{evidence_symbol}` in `{file}` "
            f"and apply the necessary fix."
        )
    else:
        target["what"] = f"Inspect `{file}` and apply the necessary fix."

    return target




_DEFICIENCY_INSTRUCTION = {
    "ROOT_CAUSE_RELOCALIZATION": (
        "The root cause is at a different abstraction layer than where "
        "the previous attempt edited. Trace the call chain from the "
        "edited code to find the correct layer for the fix."
    ),
    "INTERFACE_CONSTRAINT_CONTEXT": (
        "Review the API contract and interface semantics before editing. "
        "Check the parent class or interface definition for the correct "
        "signature and behavior expectations."
    ),
    "REGRESSION_CONSTRAINT_CONTEXT": (
        "Preserve existing passing behavior. "
        "The fix must not break the tests that already pass."
    ),
    "EDIT_SCOPE_CONTEXT": (
        "Limit changes to the minimal set of files with direct evidence. "
        "Drop edits that lack support from the issue or test failures."
    ),
    "RELATED_TEST_CONTEXT": (
        "Review the expected behavior in the test files first, "
        "then align the implementation with those expectations."
    ),
    "API_DEFINITION_CONTEXT": (
        "Look up the API definition and understand the expected "
        "interface contract before making changes."
    ),
    "CALLER_CALLEE_CONTEXT": (
        "Trace the call chain from the edited code to identify "
        "where the issue actually originates."
    ),
}


def _build_concrete_instruction(
    nd: NormalizedDiagnosis, edit_target: Optional[dict], selected: dict
) -> str:
    """Build a concrete, actionable retry instruction without taxonomy jargon."""
    # If manual diagnosis already provided a good instruction, use it
    existing = (nd.context_packet_instruction or "").strip()
    if existing and len(existing) > 30 and not _is_internal_instruction(existing):
        return existing

    parts = []

    # File + symbol at function level
    if edit_target and edit_target.get("file"):
        file = edit_target["file"]
        symbol = edit_target.get("symbol", "")
        if symbol:
            parts.append(
                f"In `{file}`, inspect `{symbol}` "
                f"(lines {edit_target.get('start_line', '?')}-{edit_target.get('end_line', '?')})."
            )
        else:
            parts.append(f"In `{file}`, inspect the relevant code shown above.")
    else:
        evidence = selected.get("evidence", []) or []
        files = list(dict.fromkeys(e.get("path") for e in evidence if e.get("path")))
        if files:
            parts.append(f"Focus on: {', '.join(f'`{f}`' for f in files[:3])}.")

    # What to do — prefer concrete guidance over generic statements
    cleaned_intent = _clean_retry_intent(nd.retry_intent or "")
    if cleaned_intent:
        parts.append(cleaned_intent.rstrip(".") + ".")
    elif edit_target and edit_target.get("what"):
        parts.append(edit_target["what"].rstrip(".") + ".")

    # Guidance tailored to context_deficiency_type (instead of hardcoded template)
    deficiency_type = nd.context_deficiency_type or ""
    if deficiency_type in _DEFICIENCY_INSTRUCTION:
        parts.append(_DEFICIENCY_INSTRUCTION[deficiency_type])
    parts.append("Make a minimal, local edit.")

    # Verification
    test_evidence = [e for e in (selected.get("evidence") or [])
                     if "test" in e.get("relation", "")]
    if test_evidence:
        test_names = [e.get("symbol", "") for e in test_evidence[:3] if e.get("symbol")]
        if test_names:
            parts.append(f"Run `{', '.join(test_names)}` to verify the fix.")

    if not parts:
        return "Examine the evidence above and apply a minimal, targeted fix. Run the failing tests before submitting."

    return " ".join(parts)


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


def _plain_diagnosis(nd: NormalizedDiagnosis, rs: RuntimeSignals) -> str:
    """Return a plain-language diagnosis sentence with no taxonomy terms.

    Uses context_deficiency_type as the primary axis, falls back to
    pathology-based templates for backward compatibility.
    """
    cdt = nd.context_deficiency_type or ""

    deficiency_templates = {
        "REGRESSION_CONSTRAINT_CONTEXT":
            "The previous attempt partially fixed the issue but may have introduced "
            "regressions. The fix needs to preserve existing passing behavior.",
        "ROOT_CAUSE_RELOCALIZATION":
            "The previous attempt edited at the wrong abstraction layer. "
            "The root cause is in a different module or function than where the edit was made.",
        "INTERFACE_CONSTRAINT_CONTEXT":
            "The previous attempt misunderstood the API contract or interface semantics. "
            "Review the parent class or interface definition for the correct behavior.",
        "EDIT_SCOPE_CONTEXT":
            "The previous attempt changed too many files beyond what the issue requires. "
            "Narrow the fix to only the essential changes.",
        "RELATED_TEST_CONTEXT":
            "The previous attempt missed expected behavior defined in related tests. "
            "Review the test expectations before making changes.",
        "API_DEFINITION_CONTEXT":
            "The previous attempt did not have the correct symbol or API definition. "
            "Look up the expected interface before editing.",
        "CALLER_CALLEE_CONTEXT":
            "The previous attempt missed context from the call chain. "
            "Trace caller-callee relationships to find the correct fix location.",
    }
    if cdt in deficiency_templates:
        return deficiency_templates[cdt]

    # Fallback: pathology-based templates (legacy)
    pathology = nd.pathology or ""
    templates = {
        "REGRESSION_AFTER_PARTIAL_FIX":
            "The previous attempt partially fixed the issue but introduced new test failures. "
            "The fix needs to preserve existing behavior while addressing the root cause.",
        "OVER_EXPLORE_OVER_EDIT":
            "The previous attempt changed too many files beyond what the issue requires. "
            "Narrow the fix to only the essential changes.",
        "EXPLORE_OK_EDIT_MISALIGNED":
            "The previous attempt looked at the right code but the patch didn't correctly apply the findings. "
            "The evidence below shows what was discovered — align the fix with it.",
        "UNDER_EDIT_PARTIAL_FIX":
            "The previous attempt was too narrow and missed related code that needs the same fix. "
            "Check sibling implementations and parallel logic.",
    }
    return templates.get(pathology, "The previous attempt did not resolve the issue. "
                         "Use the evidence below to apply a corrected fix.")


def build_context_packet_md(
    repo_root: Path,
    nd: NormalizedDiagnosis,
    md: ManualDiagnosis,
    rs: RuntimeSignals,
    selected: dict,
) -> str:
    """Render the final markdown context packet (v1 — compressed, no taxonomy)."""
    trigger = md.trigger_assessment or {}
    visible_target_fixes = trigger.get("visible_target_fixes", []) or []
    visible_regressions = trigger.get("visible_regressions", []) or []

    # v1: filter evidence before rendering
    raw_evidence = selected.get("evidence", []) or []
    evidence = _filter_evidence(raw_evidence)
    edit_target = _primary_edit_target(
        nd, {"evidence": evidence}, repo_root,
        edited_files=rs.edited_files or [],
    )

    lines: list[str] = []

    # ----- 1. Header -----
    lines.append("# Retry Context")
    lines.append("")

    # ----- 2. What went wrong (plain language, no taxonomy) -----
    lines.append("## What Went Wrong")
    lines.append("")
    diag_text = _plain_diagnosis(nd, rs)
    lines.append(diag_text)
    lines.append("")

    if visible_target_fixes:
        lines.append("**Tests that passed after the previous attempt:**")
        for t in visible_target_fixes:
            lines.append(f"- `{t}`")
        lines.append("")
    if visible_regressions:
        lines.append("**Tests that failed (regressions):**")
        for t in visible_regressions:
            lines.append(f"- `{t}`")
        lines.append("")
    if rs.test_failures:
        lines.append("**Remaining test failures:**")
        for t in rs.test_failures[:8]:
            lines.append(f"- `{t}`")
        lines.append("")

    # ----- 3. Primary Edit Target (v1: explicit guidance) -----
    if edit_target:
        lines.append("## Primary Edit Target")
        lines.append("")
        lines.append(f"- **File**: `{edit_target['file']}`")
        if edit_target.get("symbol"):
            lines.append(f"- **Target**: `{edit_target['symbol']}` "
                         f"(lines {edit_target.get('start_line', '?')}-{edit_target.get('end_line', '?')})")
        if edit_target.get("what"):
            lines.append(f"- **Goal**: {edit_target['what']}")
        lines.append("")

    # ----- 4. Relevant Code (v1: filtered + line-limited) -----
    if evidence:
        lines.append("## Relevant Code")
        lines.append("")
        total_code_lines = 0
        shown = 0
        for ev in evidence:
            if total_code_lines >= MAX_TOTAL_CODE_LINES:
                break
            rel = ev.get("relation", "")
            eid = ev.get("id", "?")
            path = ev.get("path", "?")
            start = int(ev.get("start_line", 0))
            end = int(ev.get("end_line", 0))
            symbol = ev.get("symbol", "")
            why = ev.get("why", "")

            label = _label(rel)
            title = f"### {label}: `{symbol}`" if symbol else f"### {label}"
            lines.append(title)
            lines.append("")
            lines.append(f"- **File**: `{path}` (lines {start}-{end})")
            if why:
                lines.append(f"- **Relevance**: {why}")
            lines.append("")

            # Snippet with line limit
            remaining = MAX_TOTAL_CODE_LINES - total_code_lines
            limit = min(MAX_EVIDENCE_LINES_PER_ITEM, remaining)
            snippet_src = ri.read_span(Path(repo_root), path, start, end, context=0)
            if snippet_src:
                snippet_lines = snippet_src.splitlines()
                if len(snippet_lines) > limit:
                    snippet_src = "\n".join(snippet_lines[:limit])
                    truncated = True
                else:
                    truncated = False
                lines.append("```python")
                lines.append(_format_snippet(snippet_src, start))
                if truncated:
                    lines.append(f"# ... (truncated, {len(snippet_lines)} lines total)")
                lines.append("```")
                lines.append("")
                total_code_lines += min(len(snippet_lines), limit)
            shown += 1

    # ----- 5. Guidelines (v1: minimal, no taxonomy constraints) -----
    lines.append("## Guidelines")
    lines.append("")
    lines.append("- Make the smallest change that fixes the issue.")
    lines.append("- Do not modify files unrelated to the evidence above.")
    lines.append("- Run the failing tests to verify before submitting.")
    lines.append("")

    # ----- 6. Retry Instruction (v1: concrete, no taxonomy) -----
    lines.append("## Retry Instruction")
    lines.append("")
    instruction = _build_concrete_instruction(nd, edit_target, {"evidence": evidence})
    lines.append(instruction)
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
