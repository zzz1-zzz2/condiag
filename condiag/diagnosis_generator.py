"""ConDiag diagnosis generator v1 — failure signal → context deficiency type.

Replaces the old TRIGGER_TO_PATHOLOGY mapping with a rule-based diagnoser
that answers: "What type of context was the agent missing?"

Output: context_deficiency_type + differentiated retrieval_plan + compat 5R.

Architecture:
  trigger_result + runtime_signals + issue_text
  → _classify_deficiency() → context_deficiency_type
  → _plan_for_deficiency() → retrieval_plan (differentiated by type)
  → _compat_5r() → pathology / 5R (backward compat)

Context Deficiency Types (v1):
  RELATED_TEST_CONTEXT          — agent needs expected behavior from tests
  API_DEFINITION_CONTEXT        — agent needs symbol/API definition
  INTERFACE_CONSTRAINT_CONTEXT  — agent needs interface semantics/constraints
  CALLER_CALLEE_CONTEXT         — agent needs call chain context
  REGRESSION_CONSTRAINT_CONTEXT — agent needs P2P regression constraints
  EDIT_SCOPE_CONTEXT            — agent needs edit boundary constraints
  ROOT_CAUSE_RELOCALIZATION     — agent targeted wrong layer/abstraction

v1 rule-based: uses available signals (trigger_type, edited/viewed counts,
issue keywords, patch summary). Does NOT require test_failures or oracle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# Deficiency taxonomy (shared with schemas.NormalizedDiagnosis)
# ============================================================================

DEFICIENCY_TYPES = [
    "RELATED_TEST_CONTEXT",
    "API_DEFINITION_CONTEXT",
    "INTERFACE_CONSTRAINT_CONTEXT",
    "CALLER_CALLEE_CONTEXT",
    "REGRESSION_CONSTRAINT_CONTEXT",
    "EDIT_SCOPE_CONTEXT",
    "ROOT_CAUSE_RELOCALIZATION",
]

# 5R mapping (compat — each deficiency has a primary 5R action)
DEFICIENCY_TO_5R = {
    "RELATED_TEST_CONTEXT": "RETRIEVE",
    "API_DEFINITION_CONTEXT": "RETRIEVE",
    "INTERFACE_CONSTRAINT_CONTEXT": "RECONCILE",
    "CALLER_CALLEE_CONTEXT": "RELOCALIZE",
    "REGRESSION_CONSTRAINT_CONTEXT": "RECONCILE",
    "EDIT_SCOPE_CONTEXT": "RESTRAIN",
    "ROOT_CAUSE_RELOCALIZATION": "RELOCALIZE",
}

DEFICIENCY_TO_PATHOLOGY = {
    "RELATED_TEST_CONTEXT": "UNDER_EDIT_PARTIAL_FIX",
    "API_DEFINITION_CONTEXT": "UNDER_EDIT_PARTIAL_FIX",
    "INTERFACE_CONSTRAINT_CONTEXT": "EXPLORE_OK_EDIT_MISALIGNED",
    "CALLER_CALLEE_CONTEXT": "UNDER_EDIT_PARTIAL_FIX",
    "REGRESSION_CONSTRAINT_CONTEXT": "OVER_EXPLORE_OVER_EDIT",
    "EDIT_SCOPE_CONTEXT": "OVER_EXPLORE_OVER_EDIT",
    "ROOT_CAUSE_RELOCALIZATION": "MISS_CONTEXT_OR_WRONG_LOCALIZATION",
}

DEFICIENCY_TO_RETRY_INTENT = {
    "RELATED_TEST_CONTEXT": "TEST_CONTEXT_INFORMED_RETRY",
    "API_DEFINITION_CONTEXT": "SYMBOL_DEF_INFORMED_RETRY",
    "INTERFACE_CONSTRAINT_CONTEXT": "CONSTRAINT_INFORMED_RECONCILE",
    "CALLER_CALLEE_CONTEXT": "CALLCHAIN_INFORMED_RELOCALIZE",
    "REGRESSION_CONSTRAINT_CONTEXT": "REGRESSION_CONSTRAINED_RECONCILE",
    "EDIT_SCOPE_CONTEXT": "SCOPE_BOUNDED_RESTRAIN",
    "ROOT_CAUSE_RELOCALIZATION": "ROOT_CAUSE_RELOCALIZE",
}


# ============================================================================
# Signal extractors
# ============================================================================

def _issue_keyword_signals(issue: str) -> dict:
    """Extract signal features from issue text."""
    signals = {
        "mentions_override": bool(re.search(r'\b(override|subclass|inherit)\b', issue, re.I)),
        "mentions_subparser": bool(re.search(r'\bsubparser\b', issue, re.I)),
        "mentions_error_format": bool(re.search(r'\berror\b.*\b(format|message)\b', issue, re.I)),
        "mentions_regression": bool(re.search(r'\bregression\b', issue, re.I)),
        "mentions_type_error": bool(re.search(r'\bTypeError\b', issue, re.I)),
        "mentions_assertion": bool(re.search(r'\bAssertionError\b', issue, re.I)),
        "mentions_attribute": bool(re.search(r'\b(AttributeError|has no attribute)\b', issue, re.I)),
        "mentions_zero": bool(re.search(r'\bzero\b', issue, re.I)),
        "mentions_print": bool(re.search(r'\bprint(s|ed|ing)?\b', issue, re.I)),
        "mentions_bool": bool(re.search(r'\bbool\b|__bool__|__nonzero__', issue, re.I)),
        "mentions_inner_class": bool(re.search(r'\binner\b.*\bclass\b', issue, re.I)),
        "mentions_serialize": bool(re.search(r'\bserializ\w+\b', issue, re.I)),
        "mentions_pk": bool(re.search(r'\bpk\b', issue, re.I)),
        "mentions_ordering": bool(re.search(r'\border\w+\b', issue, re.I)),
        "mentions_suppress": bool(re.search(r'(?:__)?suppress(?:_context)?(?:__)?\b|\bsuppress\b', issue, re.I)),
    }
    return signals


def _patch_shape_signals(rs: dict) -> dict:
    """Extract patch shape signals from runtime_signals dict."""
    return {
        "edited_files_count": int(rs.get("edited_files_count") or 0),
        "changed_lines_total": int(rs.get("changed_lines_total") or 0),
        "viewed_files_count": int(rs.get("viewed_files_count") or 0),
        "viewed_but_not_final_count": int(rs.get("viewed_but_not_final_files_count") or 0),
        "test_runs_count": int(rs.get("test_runs_count") or 0),
        "test_failures_count": int(rs.get("test_failures_count") or 0),
    }


# ============================================================================
# Individual classification rules
# ============================================================================
# Each rule is a standalone function taking only the signals it needs.
# This makes them independently testable and composable.
# ============================================================================


def _rule_a_regression_constraint(
    trigger_type: str, shape: dict, issue_kws: dict
) -> tuple[str, float, str] | None:
    """F2P pass but P2P regression: detected by EVIDENCE_EDIT_MISMATCH + theme keywords."""
    if (trigger_type == "EVIDENCE_EDIT_MISMATCH"
            and shape.get("viewed_files_count", 0) >= 8
            and (issue_kws.get("mentions_subparser")
                 or issue_kws.get("mentions_override")
                 or issue_kws.get("mentions_error_format"))):
        return ("REGRESSION_CONSTRAINT_CONTEXT", 0.70,
                "viewed≥8 + EVIDENCE_EDIT_MISMATCH + override/subparser → regression risk")
    return None


def _rule_b_relocalization(
    trigger_type: str, shape: dict, issue_kws: dict
) -> tuple[str, float, str] | None:
    """Agent edited right file but at wrong abstraction/symptom layer."""
    if (trigger_type == "EVIDENCE_EDIT_MISMATCH"
            and shape.get("viewed_but_not_final_count", 0) >= 5
            and shape.get("edited_files_count", 0) <= 2
            and (issue_kws.get("mentions_serialize")
                 or issue_kws.get("mentions_inner_class")
                 or issue_kws.get("mentions_zero")
                 or issue_kws.get("mentions_bool")
                 or issue_kws.get("mentions_print"))):
        return ("ROOT_CAUSE_RELOCALIZATION", 0.65,
                "EVIDENCE_EDIT_MISMATCH + many dropped views + conceptual issue → wrong layer")
    return None


def _rule_c_interface_constraint(
    trigger_type: str, shape: dict, issue_kws: dict
) -> tuple[str, float, str] | None:
    """Agent didn't understand API semantics (TypeError, pk alias, suppress_context)."""
    del trigger_type  # not used by this rule
    if ((issue_kws.get("mentions_type_error")
         or issue_kws.get("mentions_pk")
         or issue_kws.get("mentions_ordering")
         or issue_kws.get("mentions_suppress")
         or issue_kws.get("mentions_attribute"))
            and shape.get("changed_lines_total", 0) <= 20):
        return ("INTERFACE_CONSTRAINT_CONTEXT", 0.60,
                "API semantics issue + small patch → interface understanding gap")
    return None


def _rule_d_edit_scope(
    trigger_type: str, shape: dict, issue_kws: dict
) -> tuple[str, float, str] | None:
    """Agent over-edited: large patch or multiple files changed."""
    del trigger_type, issue_kws  # not used by this rule
    if shape.get("changed_lines_total", 0) >= 20 and shape.get("edited_files_count", 0) >= 1:
        return ("EDIT_SCOPE_CONTEXT", 0.55,
                f"patch={shape['changed_lines_total']} lines → scope boundary may be needed")
    return None


def _rule_e_related_test(
    trigger_type: str, shape: dict, issue_kws: dict
) -> tuple[str, float, str] | None:
    """PARTIAL_FIX_SUSPICION + ran tests → needs test expected behavior."""
    del issue_kws  # not used by this rule
    if (trigger_type == "PARTIAL_FIX_SUSPICION"
            and shape.get("test_runs_count", 0) >= 3):
        return ("RELATED_TEST_CONTEXT", 0.55,
                "PARTIAL_FIX_SUSPICION + ran tests ≥3 → needs test expected behavior")
    return None


def _rule_f_api_definition(
    trigger_type: str, shape: dict, issue_kws: dict
) -> tuple[str, float, str] | None:
    """Small focused fix that needs symbol definitions."""
    del trigger_type, issue_kws  # not used by this rule
    if (shape.get("edited_files_count", 0) <= 2
            and shape.get("changed_lines_total", 0) <= 15
            and shape.get("viewed_files_count", 0) >= 3):
        return ("API_DEFINITION_CONTEXT", 0.50,
                "small focused patch + moderate viewing → symbol context helpful")
    return None


def _fallback_related_test() -> tuple[str, float, str]:
    """Fallback when no rule matches."""
    return ("RELATED_TEST_CONTEXT", 0.40,
            "fallback: no strong signal for other deficiency types")


# ============================================================================
# Orchestrator
# ============================================================================

_RULES = [
    _rule_a_regression_constraint,
    _rule_b_relocalization,
    _rule_c_interface_constraint,
    _rule_d_edit_scope,
    _rule_e_related_test,
    _rule_f_api_definition,
]


def classify_deficiency(
    trigger_type: str,
    trigger_reason: list[str],
    runtime_signals: dict,
    issue: str = "",
) -> tuple[str, list[str], float]:
    """Classify failure → context_deficiency_type.

    Runs all rules, picks the highest-scoring match.
    Returns (primary_type, secondary_types, confidence).
    """
    shape = _patch_shape_signals(runtime_signals)
    issue_kws = _issue_keyword_signals(issue)

    candidates: list[tuple[str, float, str]] = []
    for rule in _RULES:
        result = rule(trigger_type, shape, issue_kws)
        if result is not None:
            candidates.append(result)

    # Fallback always applies
    candidates.append(_fallback_related_test())

    # Sort by score descending, pick top
    candidates.sort(key=lambda c: -c[1])
    primary = candidates[0][0]
    confidence = candidates[0][1]
    secondaries = [c[0] for c in candidates[1:4] if c[0] != primary]

    return primary, secondaries, confidence


# ============================================================================
# Retrieval plan builder (table-driven)
# ============================================================================

RETRIEVAL_PLAN_TEMPLATES: dict[str, list[dict]] = {
    "ROOT_CAUSE_RELOCALIZATION": [
        {"operation": "FIND_SYMBOL_DEFINITION",
         "target": "symbols from issue text and viewed files — trace to root cause",
         "budget": 5},
        {"operation": "FIND_NEIGHBOR_TESTS",
         "target": "tests adjacent to edited files — verify root cause hypothesis",
         "budget": 3},
    ],
    "REGRESSION_CONSTRAINT_CONTEXT": [
        {"operation": "FIND_NEIGHBOR_TESTS",
         "target": "tests adjacent to edited files and changed symbols — find P2P tests",
         "budget": 5},
        {"operation": "FIND_SYMBOL_DEFINITION",
         "target": "symbols referenced in the patch and their callers",
         "budget": 4},
    ],
    "INTERFACE_CONSTRAINT_CONTEXT": [
        {"operation": "FIND_SYMBOL_DEFINITION",
         "target": "method/function signatures and interface definitions from issue",
         "budget": 5},
        {"operation": "FIND_NEIGHBOR_TESTS",
         "target": "tests showing expected interface behavior and constraints",
         "budget": 3},
    ],
    "EDIT_SCOPE_CONTEXT": [
        {"operation": "REHYDRATE_SEEN_EVIDENCE",
         "target": "viewed spans around edited code — understand scope",
         "budget": 4},
        {"operation": "FIND_NEIGHBOR_TESTS",
         "target": "tests bounding the edit scope",
         "budget": 3},
    ],
    "RELATED_TEST_CONTEXT": [
        {"operation": "FIND_FAILED_TEST",
         "target": "visible test failures from runtime_signals",
         "budget": 3},
        {"operation": "FIND_NEIGHBOR_TESTS",
         "target": "tests for related functionality from issue text",
         "budget": 4},
    ],
    "API_DEFINITION_CONTEXT": [
        {"operation": "FIND_SYMBOL_DEFINITION",
         "target": "key symbols from issue text and edited files",
         "budget": 5},
    ],
}


def build_retrieval_plan(
    deficiency_type: str,
    runtime_signals: dict,
    issue: str = "",
) -> list[dict]:
    """Build a differentiated retrieval_plan based on context_deficiency_type.

    Uses RETRIEVAL_PLAN_TEMPLATES table with conditional extras for viewed_spans.
    """
    plan_base = RETRIEVAL_PLAN_TEMPLATES.get(deficiency_type)
    if plan_base is None:
        # CALLER_CALLEE_CONTEXT or unknown fallback
        plan_base = [
            {"operation": "FIND_SYMBOL_DEFINITION",
             "target": "symbols in the call chain around edited code",
             "budget": 4},
            {"operation": "FIND_NEIGHBOR_TESTS",
             "target": "tests for caller/callee functions",
             "budget": 3},
        ]

    plan = list(plan_base)

    # Conditional: add REHYDRATE_SEEN_EVIDENCE if viewed_spans available
    if runtime_signals.get("viewed_spans"):
        rehydrate_entry = {
            "ROOT_CAUSE_RELOCALIZATION": {
                "operation": "REHYDRATE_SEEN_EVIDENCE",
                "target": "viewed spans in files related to root cause search",
                "budget": 3,
            },
            "REGRESSION_CONSTRAINT_CONTEXT": {
                "operation": "REHYDRATE_SEEN_EVIDENCE",
                "target": "viewed spans that may contain regression constraints",
                "budget": 2,
            },
            "INTERFACE_CONSTRAINT_CONTEXT": {
                "operation": "REHYDRATE_SEEN_EVIDENCE",
                "target": "viewed spans showing interface usage at call sites",
                "budget": 2,
            },
            "RELATED_TEST_CONTEXT": {
                "operation": "REHYDRATE_SEEN_EVIDENCE",
                "target": "viewed spans that may contain test expectations",
                "budget": 2,
            },
            "API_DEFINITION_CONTEXT": {
                "operation": "REHYDRATE_SEEN_EVIDENCE",
                "target": "viewed spans containing API usage patterns",
                "budget": 3,
            },
        }
        entry = rehydrate_entry.get(deficiency_type)
        if entry:
            plan.append(entry)

    return plan


# ============================================================================
# Convenience: build target_hints by deficiency type
# ============================================================================

HINT_STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have", "should",
    "when", "while", "into", "them", "then", "what", "where", "which",
    "django", "issue", "patch", "test", "tests", "description", "error",
    "raise", "raises", "raised", "expected", "actual", "traceback",
}


def build_target_hints(deficiency_type: str, issue: str, runtime_signals: dict) -> list[dict]:
    """Build targeted target_hints based on deficiency type + issue + signals.

    More targeted than the generic _auto_target_hints — tunes what kinds of
    hints to extract based on what the deficiency type needs.
    """
    hints: list[dict] = []
    seen: set[str] = set()

    def add(value: str, kind: str) -> None:
        v = (value or "").strip()
        if len(v) < 4 or v.lower() in HINT_STOPWORDS:
            return
        if v in seen:
            return
        seen.add(v)
        hints.append({"kind": kind, "value": v})

    # Common: file basenames from edited/viewed files
    from pathlib import Path
    for f in (runtime_signals.get("edited_files") or []):
        add(Path(str(f)).stem, "file")
    for f in (runtime_signals.get("viewed_files_in_order") or []):
        add(Path(str(f)).stem, "file")

    # Deficiency-specific hint extraction
    if deficiency_type == "ROOT_CAUSE_RELOCALIZATION":
        # Extract full dotted paths and cross-file references
        for sym in re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:\.[A-Z][a-zA-Z0-9]+)+\b", issue):
            add(sym, "symbol")
        for dotted in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]+(?:\.[A-Za-z_][A-Za-z0-9_]+)+\b", issue):
            add(dotted, "symbol")
        # Extract class names (for symbol lookup)
        for cls in re.findall(r"\b[A-Z][a-zA-Z0-9]+\b", issue):
            add(cls, "symbol")

    elif deficiency_type == "INTERFACE_CONSTRAINT_CONTEXT":
        # Extract method names and parameter patterns
        for func in re.findall(r"\b[a-z_]+\([^)]*\)", issue):
            name = func.split("(")[0]
            add(name, "symbol")
        # Extract dunder methods
        for dunder in re.findall(r"__\w+__", issue):
            add(dunder, "symbol")

    elif deficiency_type == "REGRESSION_CONSTRAINT_CONTEXT":
        # Extract method names (especially add_*, set_*, override patterns)
        for func in re.findall(r"\b(?:add_|set_|override_|get_)\w+\b", issue, re.I):
            add(func, "symbol")
        # Extract AssertionError/TypeError class names
        for err in re.findall(r"\b\w+Error\b", issue):
            add(err, "symbol")

    elif deficiency_type == "RELATED_TEST_CONTEXT":
        # Extract test-related terms and assertion keywords
        for test_kw in re.findall(r"\btest_\w+\b", issue, re.I):
            add(test_kw, "test")
        for assertion in re.findall(r"\b(?:assert|expect|should|must)\s+\w+", issue, re.I):
            add(assertion.split()[-1], "concept")

    else:
        # Generic: extract concept keywords and CamelCase names
        for tok in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", issue):
            add(tok, "concept")
        for sym in re.findall(r"\b[A-Z][a-zA-Z0-9]+(?:[A-Z][a-z]+)+\b", issue):
            add(sym, "symbol")

    return hints[:40]


# ============================================================================
# Main entry point
# ============================================================================

@dataclass
class DiagnosisResult:
    """Output of the diagnosis generator."""
    context_deficiency_type: str
    context_deficiency_secondary: list
    retrieval_plan: list
    target_hints: list
    pathology: str
    primary_5r_action: str
    retry_intent: str
    confidence: float
    action_family: str


def generate(
    trigger_type: str,
    trigger_reason: list[str],
    runtime_signals: dict,
    issue: str = "",
) -> DiagnosisResult:
    """Generate complete diagnosis: context_deficiency + plan + compat fields.

    This is the main entry point called by the experiment pipeline.
    Replaces the old TRIGGER_TO_PATHOLOGY mapping.
    """
    # 1. Classify deficiency
    primary_type, secondary_types, confidence = classify_deficiency(
        trigger_type, trigger_reason, runtime_signals, issue
    )

    # 2. Build retrieval plan (differentiated)
    retrieval_plan = build_retrieval_plan(primary_type, runtime_signals, issue)

    # 3. Build targeted hints
    target_hints = build_target_hints(primary_type, issue, runtime_signals)

    # 4. Compat fields (pathology, 5R, retry_intent)
    pathology = DEFICIENCY_TO_PATHOLOGY.get(primary_type, "INSUFFICIENT_RUNTIME_EVIDENCE")
    primary_5r = DEFICIENCY_TO_5R.get(primary_type, "ABSTAIN")
    retry_intent = DEFICIENCY_TO_RETRY_INTENT.get(primary_type, "")
    action_family = _action_family_for_5r(primary_5r)

    return DiagnosisResult(
        context_deficiency_type=primary_type,
        context_deficiency_secondary=secondary_types,
        retrieval_plan=retrieval_plan,
        target_hints=target_hints,
        pathology=pathology,
        primary_5r_action=primary_5r,
        retry_intent=retry_intent,
        confidence=confidence,
        action_family=action_family,
    )


def _action_family_for_5r(primary_5r: str) -> str:
    return {
        "RETRIEVE": "RECOVERY",
        "RECONCILE": "RECOVERY",
        "RELOCALIZE": "RECOVERY",
        "REHYDRATE": "RECOVERY",
        "RESTRAIN": "CONTROL",
        "NOOP": "NOOP",
    }.get(primary_5r, "ABSTAIN")
