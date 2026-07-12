"""Context Deficiency Diagnoser — scoring-based CDType classification.

Maps error_type + trajectory signals + patch behavior to a 7-type
Context Deficiency Diagnosis.

Pipeline position:
  FailureWitness → TrajectorySignals → ContextDeficiencyDiagnosis
    → DiagnosticSearchContract → Attempt-2 Agent

Design (plan_version=plan_v2.0_search_contract):
  score = prior × 0.4 + signal_evidence × 0.4 + patch_behavior × 0.2

  - Prior: canonical failure family → weak CDType candidates
  - Signal evidence: trajectory signals adjust scores
  - Patch behavior: empty/multi-file edit patterns
  - Confidence: computed from margin + evidence coverage + direct signal,
    NOT from absolute score threshold alone

  No hard keyword→CDType mapping. No gold leakage.
  Gold leakage guard: uses only error_type + trajectory signals + patch behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from condiag.trajectory_signals import RuntimeSignals


# =====================================================================
# Dataclasses
# =====================================================================


@dataclass
class PatchBehavior:
    """Summary of the Attempt-1 patch for CDType scoring."""
    has_edit: bool = False
    files_edited_count: int = 0
    multi_file_edit: bool = False
    patch_size: int = 0  # lines added + removed


@dataclass
class ContextDeficiencyDiagnosis:
    """Result of CDType diagnosis.

    primary_cdtype:
        The highest-scoring CDType, or "unknown" if confidence < threshold.
    cdtype_scores:
        All 7 types with 0.0-1.0 scores (keys are CDTYPE_* constants).
    diagnosis_rationale:
        Concise explanation of why this type was chosen.
    diagnosis_version:
        Schema version, incremented on breaking changes.
    confidence:
        Diagnostic confidence score 0.0-1.0.
    confidence_factors:
        Breakdown of confidence components.
    prior_scores:
        Prior component before weighting (for audit trace).
    signal_evidence:
        Signal evidence component before weighting (for audit trace).
    """
    primary_cdtype: str = "unknown"
    cdtype_scores: dict[str, float] = field(default_factory=dict)
    diagnosis_rationale: str = ""
    diagnosis_version: str = "1.1-dev"
    diagnoser_version: str = "1.1-dev"
    failure_family_version: str = "1.0"
    confidence_version: str = "2.0"
    confidence: float = 0.0
    confidence_factors: dict[str, float] = field(default_factory=dict)
    prior_scores: dict[str, float] = field(default_factory=dict)
    signal_evidence: dict[str, float] = field(default_factory=dict)
    patch_scores: dict[str, float] = field(default_factory=dict)
    failure_family: str = ""
    error_type: str = ""


# =====================================================================
# CDType taxonomy (7 types)
# =====================================================================

CDTYPE_API_DEFINITION = "API_DEFINITION_CONTEXT"
CDTYPE_INTERFACE_CONSTRAINT = "INTERFACE_CONSTRAINT_CONTEXT"
CDTYPE_RELATED_TEST = "RELATED_TEST_CONTEXT"
CDTYPE_CALLER_CALLEE = "CALLER_CALLEE_CONTEXT"
CDTYPE_ROOT_CAUSE = "ROOT_CAUSE_RELOCALIZATION"
CDTYPE_REGRESSION_CONSTRAINT = "REGRESSION_CONSTRAINT_CONTEXT"
CDTYPE_DEPENDENCY = "DEPENDENCY_CONTEXT"

_ALL_CDTYPES = [
    CDTYPE_API_DEFINITION,
    CDTYPE_INTERFACE_CONSTRAINT,
    CDTYPE_RELATED_TEST,
    CDTYPE_CALLER_CALLEE,
    CDTYPE_ROOT_CAUSE,
    CDTYPE_REGRESSION_CONSTRAINT,
    CDTYPE_DEPENDENCY,
]


# =====================================================================
# Canonical failure families (cross-language)
#
# Maps raw failure_type values to semantic families that span languages
# and frameworks. Each family provides weak prior candidates — trajectory
# signals do the actual differentiation.
#
# Design principles:
# 1. Families are semantic, not syntactic (not raw error string → CDType)
# 2. Family priors are weak (0.2-0.3) — they generate candidates, not answers
# 3. Known Python exception types keep existing typed priors (stronger)
# 4. build_or_infra_failure is disambiguated via error_message content
# 5. GENERIC_TEST_FAILURE has NO strong prior — relies entirely on trajectory
# =====================================================================

FAMILY_ASSERTION = "ASSERTION_FAILURE"
FAMILY_TYPE_INTERFACE = "TYPE_OR_INTERFACE_FAILURE"
FAMILY_API_SYMBOL = "API_OR_SYMBOL_FAILURE"
FAMILY_COMPILE_BUILD = "COMPILE_OR_BUILD_FAILURE"
FAMILY_INFRASTRUCTURE = "INFRASTRUCTURE_FAILURE"
FAMILY_IMPORT_DEPENDENCY = "IMPORT_OR_DEPENDENCY_FAILURE"
FAMILY_RUNTIME = "RUNTIME_EXCEPTION"
FAMILY_GENERIC_TEST = "GENERIC_TEST_FAILURE"

# Raw failure_type → canonical family
# build_or_infra_failure is handled by _disambiguate_build_or_infra()
_FAMILY_MAP: dict[str, str] = {
    # Python exceptions
    "AssertionError": FAMILY_ASSERTION,
    "assertion_error": FAMILY_ASSERTION,
    "TypeError": FAMILY_TYPE_INTERFACE,
    "type_error": FAMILY_TYPE_INTERFACE,
    "ValueError": FAMILY_TYPE_INTERFACE,
    "value_error": FAMILY_TYPE_INTERFACE,
    "KeyError": FAMILY_TYPE_INTERFACE,
    "key_error": FAMILY_TYPE_INTERFACE,
    "IndexError": FAMILY_TYPE_INTERFACE,
    "index_error": FAMILY_TYPE_INTERFACE,
    "AttributeError": FAMILY_API_SYMBOL,
    "attribute_error": FAMILY_API_SYMBOL,
    "NameError": FAMILY_API_SYMBOL,
    "name_error": FAMILY_API_SYMBOL,
    "ImportError": FAMILY_IMPORT_DEPENDENCY,
    "import_error": FAMILY_IMPORT_DEPENDENCY,
    "ModuleNotFoundError": FAMILY_IMPORT_DEPENDENCY,
    "module_not_found_error": FAMILY_IMPORT_DEPENDENCY,
    "OSError": FAMILY_IMPORT_DEPENDENCY,
    "os_error": FAMILY_IMPORT_DEPENDENCY,
    "FileNotFoundError": FAMILY_IMPORT_DEPENDENCY,
    "file_not_found_error": FAMILY_IMPORT_DEPENDENCY,
    # Cross-language
    "RuntimeError": FAMILY_RUNTIME,
    "runtime_error": FAMILY_RUNTIME,
    "panic": FAMILY_RUNTIME,
    "test_failure": FAMILY_GENERIC_TEST,
    "timeout": FAMILY_GENERIC_TEST,
    "unknown": FAMILY_GENERIC_TEST,
}

# Weak priors per canonical family.
# These are deliberately low — trajectory signals provide the discrimination.
# Pattern: each family suggests 2-3 candidate CDTypes as starting hypotheses.
_FAMILY_PRIORS: dict[str, dict[str, float]] = {
    FAMILY_ASSERTION: {
        CDTYPE_RELATED_TEST: 0.25,
        CDTYPE_ROOT_CAUSE: 0.20,
        CDTYPE_REGRESSION_CONSTRAINT: 0.15,
    },
    FAMILY_TYPE_INTERFACE: {
        CDTYPE_INTERFACE_CONSTRAINT: 0.25,
        CDTYPE_CALLER_CALLEE: 0.20,
        CDTYPE_API_DEFINITION: 0.15,
    },
    FAMILY_API_SYMBOL: {
        CDTYPE_API_DEFINITION: 0.30,
        CDTYPE_DEPENDENCY: 0.20,
        CDTYPE_CALLER_CALLEE: 0.15,
    },
    FAMILY_COMPILE_BUILD: {
        CDTYPE_INTERFACE_CONSTRAINT: 0.25,
        CDTYPE_API_DEFINITION: 0.20,
        CDTYPE_DEPENDENCY: 0.15,
    },
    FAMILY_RUNTIME: {
        CDTYPE_ROOT_CAUSE: 0.25,
        CDTYPE_CALLER_CALLEE: 0.20,
        CDTYPE_INTERFACE_CONSTRAINT: 0.15,
    },
    FAMILY_IMPORT_DEPENDENCY: {
        CDTYPE_DEPENDENCY: 0.30,
        CDTYPE_API_DEFINITION: 0.20,
    },
    # GENERIC_TEST_FAILURE has no strong prior (relies on trajectory)
    # INFRASTRUCTURE_FAILURE has no prior (not eligible for diagnosis)
}

# Existing typed priors (stronger) for known Python exception types.
# These override family priors when the raw type maps to a known exception.
_PRIORS: dict[str, dict[str, float]] = {
    "AttributeError": {
        CDTYPE_API_DEFINITION: 0.6,
        CDTYPE_ROOT_CAUSE: 0.2,
    },
    "TypeError": {
        CDTYPE_INTERFACE_CONSTRAINT: 0.5,
        CDTYPE_CALLER_CALLEE: 0.3,
    },
    "AssertionError": {
        CDTYPE_RELATED_TEST: 0.4,
        CDTYPE_ROOT_CAUSE: 0.3,
        CDTYPE_REGRESSION_CONSTRAINT: 0.1,
    },
    "RuntimeError": {
        CDTYPE_CALLER_CALLEE: 0.5,
        CDTYPE_API_DEFINITION: 0.2,
    },
    "ImportError": {
        CDTYPE_DEPENDENCY: 0.7,
        CDTYPE_API_DEFINITION: 0.1,
    },
    "ModuleNotFoundError": {
        CDTYPE_DEPENDENCY: 0.7,
        CDTYPE_API_DEFINITION: 0.1,
    },
    "ValueError": {
        CDTYPE_INTERFACE_CONSTRAINT: 0.4,
        CDTYPE_CALLER_CALLEE: 0.3,
    },
    "KeyError": {
        CDTYPE_CALLER_CALLEE: 0.4,
        CDTYPE_API_DEFINITION: 0.3,
    },
    "IndexError": {
        CDTYPE_CALLER_CALLEE: 0.4,
        CDTYPE_API_DEFINITION: 0.3,
    },
    "OSError": {
        CDTYPE_DEPENDENCY: 0.4,
    },
    "FileNotFoundError": {
        CDTYPE_DEPENDENCY: 0.4,
    },
}

_UNIFORM_PRIOR = 0.1

_SNAKE_TO_PASCAL = {
    "assertion_error": "AssertionError",
    "attribute_error": "AttributeError",
    "type_error": "TypeError",
    "value_error": "ValueError",
    "key_error": "KeyError",
    "index_error": "IndexError",
    "runtime_error": "RuntimeError",
    "import_error": "ImportError",
    "module_not_found_error": "ModuleNotFoundError",
    "os_error": "OSError",
    "file_not_found_error": "FileNotFoundError",
    "name_error": "NameError",
    "syntax_error": "SyntaxError",
    "zero_division_error": "ZeroDivisionError",
    "stop_iteration": "StopIteration",
}


def _normalize_error_type(error_type: str) -> str:
    """Normalize snake_case failure type to PascalCase for typed prior matching.

    FailureWitness failure_type values use snake_case (e.g. "assertion_error"),
    while CDType priors use Python exception class names (e.g. "AssertionError").
    Unknown types pass through unchanged.
    """
    return _SNAKE_TO_PASCAL.get(error_type, error_type)


# Build/compile vs infrastructure disambiguation patterns
_COMPILE_PATTERNS = [
    r"compil(?:e|ation)\s+error",
    r"build\s+failed",
    r"cannot\s+compile",
    r"linker?\s+error",
    r"undefined\s+reference",
    r"expected\s+(?:parameter|identifier|type|expression)",
    r"no\s+matching\s+function",
    r"is\s+not\s+a\s+member",
    r"has\s+no\s+member",
    r"use\s+of\s+undeclared",
    r"redundant\s+newline",
    r"cannot\s+find\s+module",
    r"is\s+not\s+exported",
    r"invalid\s+memory\s+address",
    r"nil\s+pointer",
    r"panic(?:ic)?[^s]",  # Go panic / Rust panicked
    r"not\s+implemented",
    r"unresolved\s+(?:import|symbol|reference)",
    r"module\s+.*\s+not\s+found",
    r"ERR_MODULE_NOT_FOUND",
]

_INFRA_PATTERNS = [
    r"no\s+test\s+files",
    r"test\s+session\s+.*(?:start|begin)",
    r"platform\s+linux",
    r"installing",
    r"collecting",
    r"download",
    r"timeout\s+expired",
    r"connection\s+refused",
    r"network\s+unreachable",
    r"docker",
    r"killed",
    r"Segmentation\s+fault",
]


def _disambiguate_build_or_infra(error_message: str) -> str:
    """Split build_or_infra_failure into compile vs infrastructure.

    If error_message contains compile/build error patterns → COMPILE_OR_BUILD_FAILURE
    If error_message contains infrastructure patterns → INFRASTRUCTURE_FAILURE
    If neither matches → COMPILE_OR_BUILD_FAILURE (conservative: treat as code-level)
    """
    err_lower = error_message.lower()
    for pat in _COMPILE_PATTERNS:
        import re
        if re.search(pat, err_lower):
            return FAMILY_COMPILE_BUILD
    for pat in _INFRA_PATTERNS:
        import re
        if re.search(pat, err_lower):
            return FAMILY_INFRASTRUCTURE
    return FAMILY_COMPILE_BUILD


def _map_to_family(
    error_type: str,
    error_message: str = "",
    failure_origin: Optional[list[str]] = None,
) -> str:
    """Map raw failure_type to canonical family.

    Two-phase lookup:
    1. Direct lookup in _FAMILY_MAP
    2. Normalized (snake→pascal) lookup in _FAMILY_MAP
    3. Special handling for build_or_infra_failure
    4. Fallback to GENERIC_TEST_FAILURE
    """
    # Direct lookup
    if error_type in _FAMILY_MAP:
        family = _FAMILY_MAP[error_type]
        if family == FAMILY_GENERIC_TEST and error_type == "build_or_infra_failure":
            return _disambiguate_build_or_infra(error_message)
        return family

    # Normalized lookup
    normalized = _normalize_error_type(error_type)
    if normalized in _FAMILY_MAP:
        family = _FAMILY_MAP[normalized]
        return family

    # Check build_or_infra_failure variants
    if "build" in error_type.lower() or "infra" in error_type.lower():
        return _disambiguate_build_or_infra(error_message)

    return FAMILY_GENERIC_TEST


# =====================================================================
# ContextDeficiencyDiagnoser
# =====================================================================


class ContextDeficiencyDiagnoser:
    """Score each CDType from error_type + trajectory signals + patch behavior.

    Scoring pipeline:
      1. Map error_type → canonical failure family → weak family priors
      2. Override with typed priors if available (for known exception types)
      3. Compute signal evidence from trajectory
      4. Compute patch behavior scores
      5. Weighted sum: prior×0.4 + signal×0.4 + patch×0.2
      6. Confidence from margin + evidence coverage + direct signal
      7. Classify if confidence ≥ threshold; else abstain (unknown)

    Usage:
        diagnoser = ContextDeficiencyDiagnoser()
        diagnosis = diagnoser.diagnose(error_type, signals, patch_behavior)

    Thread-safe and stateless — holds no mutable state across calls.
    """

    def diagnose(
        self,
        error_type: str,
        signals: RuntimeSignals,
        patch_behavior: PatchBehavior,
        error_message: str = "",
        failure_origin: Optional[list[str]] = None,
    ) -> ContextDeficiencyDiagnosis:
        """Run full scoring pipeline and return diagnosis.

        Pipeline: canonical family → prior → signal evidence → patch
                  → weighted sum → confidence → classification/abstention.

        Args:
            error_type: From failure witness (e.g. "AssertionError" or "panic").
            signals: Pre-computed RuntimeSignals from trajectory.
            patch_behavior: PatchBehavior from Attempt-1 patch.
            error_message: Full error message from witness (for family disambiguation).
            failure_origin: Failure origins list (for build_or_infra disambiguation).

        Returns:
            ContextDeficiencyDiagnosis with primary_cdtype, scores, confidence.
        """
        # Map to canonical failure family
        family = _map_to_family(error_type, error_message, failure_origin)

        # Get family-based weak priors
        family_priors = _FAMILY_PRIORS.get(family, {})

        # Get typed priors (stronger, for known exception types)
        error_type_pascal = _normalize_error_type(error_type)
        typed_priors = _PRIORS.get(error_type_pascal, {})

        # Merge: typed priors override family priors where they exist
        merged_priors = dict(family_priors)
        for cdtype, score in typed_priors.items():
            merged_priors[cdtype] = score

        prior_scores = {
            cdtype: merged_priors.get(cdtype, _UNIFORM_PRIOR)
            for cdtype in _ALL_CDTYPES
        }

        signal_evidence = self._compute_signal_evidence(signals, error_type_pascal)
        patch_scores_val = self._compute_patch_scores(patch_behavior)

        # Weighted sum: prior × 0.4 + signal × 0.4 + patch × 0.2
        scores: dict[str, float] = {}
        for cdtype in _ALL_CDTYPES:
            scores[cdtype] = (
                prior_scores.get(cdtype, _UNIFORM_PRIOR) * 0.4
                + signal_evidence.get(cdtype, 0.0) * 0.4
                + patch_scores_val.get(cdtype, 0.0) * 0.2
            )

        # Clamp to [0.0, 1.0]
        for cdtype in scores:
            scores[cdtype] = max(0.0, min(1.0, scores[cdtype]))

        # Compute confidence (margin + evidence coverage + direct signal)
        confidence, conf_factors = self._compute_confidence(
            scores, prior_scores, signal_evidence, signals,
        )

        # Primary type: highest-score
        primary = max(scores, key=scores.get) if scores else "unknown"
        top_score = scores.get(primary, 0.0)

        # Abstain if confidence is low
        if top_score < 0.15:  # absolute floor — below this is truly random
            primary = "unknown"
        elif confidence < 0.30:
            primary = "unknown"

        rationale = self._build_rationale(
            error_type, primary, scores, prior_scores,
            signal_evidence, patch_scores_val, signals,
        )

        return ContextDeficiencyDiagnosis(
            primary_cdtype=primary,
            cdtype_scores=scores,
            diagnosis_rationale=rationale,
            diagnosis_version="1.1-dev",
            diagnoser_version="1.1-dev",
            failure_family_version="1.0",
            confidence_version="2.0",
            confidence=confidence,
            confidence_factors=conf_factors,
            prior_scores=prior_scores,
            signal_evidence=signal_evidence,
            patch_scores=patch_scores_val,
            failure_family=family,
            error_type=error_type,
        )

    # ------------------------------------------------------------------
    # Confidence
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_confidence(
        scores: dict[str, float],
        prior_scores: dict[str, float],
        signal_evidence: dict[str, float],
        signals: RuntimeSignals,
    ) -> tuple[float, dict[str, float]]:
        """Compute diagnostic confidence from multiple factors.

        Factors:
        - top1_score: absolute score of top CDType (0.0-1.0, weight 0.25)
        - top1_top2_margin: gap between #1 and #2 (capped at 0.3, weight 0.30)
        - evidence_advantage: signal evidence support for top1 vs top2 (weight 0.20)
        - direct_evidence: whether agent visited error file (binary, weight 0.15)
        - evidence_count: number of active signal evidence entries (weight 0.10)

        Returns (confidence, factor_dict).
        """
        sorted_items = sorted(scores.items(), key=lambda x: -x[1])
        top1_cdtype = sorted_items[0][0]
        top1_score = sorted_items[0][1]
        top2_score = sorted_items[1][1] if len(sorted_items) > 1 else 0.0

        margin = top1_score - top2_score
        margin_score = min(margin * 2.5, 0.30)

        # Evidence support: does signal evidence favor top1 over top2?
        signal_for_top1 = signal_evidence.get(top1_cdtype, 0.0)
        top2_cdtype = sorted_items[1][0] if len(sorted_items) > 1 else None
        signal_for_top2 = signal_evidence.get(top2_cdtype, 0.0) if top2_cdtype else 0.0
        evidence_adv = max(0.0, min(signal_for_top1 - signal_for_top2, 0.20))

        # Direct evidence: error file was visited
        direct_ev = 0.15 if signals.error_visit_alignment in (
            "visited_error_file", "visited_stack_frame"
        ) else 0.0

        # Evidence count: how many signal evidence entries point to top1
        ev_count = min(sum(1 for v in signal_evidence.values() if v > 0.001) * 0.05, 0.10)

        confidence = (
            top1_score * 0.25
            + margin_score
            + evidence_adv
            + direct_ev
            + ev_count
        )
        confidence = max(0.0, min(1.0, confidence))

        factors = {
            "top1_score": round(top1_score, 3),
            "margin": round(margin, 3),
            "margin_score": round(margin_score, 3),
            "evidence_advantage": round(evidence_adv, 3),
            "direct_evidence": round(direct_ev, 3),
            "evidence_count_score": round(ev_count, 3),
        }
        return round(confidence, 3), factors

    # ------------------------------------------------------------------
    # Scoring components
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_prior(error_type: str) -> dict[str, float]:
        """Get base prior scores from error_type (kept for backward compat)."""
        prior = _PRIORS.get(error_type, {})
        return {cdtype: prior.get(cdtype, _UNIFORM_PRIOR) for cdtype in _ALL_CDTYPES}

    @staticmethod
    def _compute_signal_evidence(
        signals: RuntimeSignals, error_type: str = "",
    ) -> dict[str, float]:
        """Map trajectory signals to CDType score adjustments.

        Each signal contributes to one or two CDTypes. Adjustments are
        additive within signal_evidence, then weighted ×0.4 in final score.

        Note on error_file_never_viewed:
            "Never viewed the error file" alone is a localization problem
            (ROOT_CAUSE_RELOCALIZATION) — the agent doesn't know where to
            look. It only becomes an API_DEFINITION issue when combined
            with errors that specifically indicate missing API/symbol
            (AttributeError, NameError, ImportError, ModuleNotFoundError).
        """
        adj: dict[str, float] = {}

        # Exploration mode
        mode = signals.exploration_mode
        if mode == "oscillating":
            adj[CDTYPE_RELATED_TEST] = adj.get(CDTYPE_RELATED_TEST, 0.0) + 0.3
            adj[CDTYPE_REGRESSION_CONSTRAINT] = adj.get(CDTYPE_REGRESSION_CONSTRAINT, 0.0) + 0.3
        elif mode == "jumping":
            adj[CDTYPE_CALLER_CALLEE] = adj.get(CDTYPE_CALLER_CALLEE, 0.0) + 0.1
        elif mode == "shallow_scan":
            adj[CDTYPE_DEPENDENCY] = adj.get(CDTYPE_DEPENDENCY, 0.0) + 0.2
        elif mode == "focused":
            adj[CDTYPE_API_DEFINITION] = adj.get(CDTYPE_API_DEFINITION, 0.0) + 0.1

        # Error-edit alignment
        alignment = signals.error_edit_alignment
        if alignment == "edited_elsewhere":
            adj[CDTYPE_CALLER_CALLEE] = adj.get(CDTYPE_CALLER_CALLEE, 0.0) + 0.3
            adj[CDTYPE_ROOT_CAUSE] = adj.get(CDTYPE_ROOT_CAUSE, 0.0) + 0.2
        elif alignment == "viewed_not_edited":
            adj[CDTYPE_INTERFACE_CONSTRAINT] = adj.get(CDTYPE_INTERFACE_CONSTRAINT, 0.0) + 0.2
            adj[CDTYPE_API_DEFINITION] = adj.get(CDTYPE_API_DEFINITION, 0.0) + 0.2
        elif alignment == "error_file_never_viewed":
            adj[CDTYPE_ROOT_CAUSE] = adj.get(CDTYPE_ROOT_CAUSE, 0.0) + 0.3
            if error_type in {
                "AttributeError", "NameError", "ImportError",
                "ModuleNotFoundError",
            }:
                adj[CDTYPE_API_DEFINITION] = adj.get(CDTYPE_API_DEFINITION, 0.0) + 0.3
        elif alignment == "aligned":
            # Aligned edit = agent edited at the error location.
            # This COUNTERS ROOT_CAUSE_RELOCALIZATION (agent already found the right place).
            adj[CDTYPE_ROOT_CAUSE] = adj.get(CDTYPE_ROOT_CAUSE, 0.0) - 0.3
            # Positive signal comes from other trajectory evidence (failure family,
            # exploration mode, patch behavior) — not from alignment alone.

        # Regression signal
        if signals.has_regression_signal:
            adj[CDTYPE_REGRESSION_CONSTRAINT] = adj.get(CDTYPE_REGRESSION_CONSTRAINT, 0.0) + 0.4

        # Visit alignment
        if signals.error_visit_alignment == "visited_none":
            adj[CDTYPE_ROOT_CAUSE] = adj.get(CDTYPE_ROOT_CAUSE, 0.0) + 0.2

        return adj

    @staticmethod
    def _compute_patch_scores(patch: PatchBehavior) -> dict[str, float]:
        """Map patch behavior to CDType adjustments."""
        adj: dict[str, float] = {}

        if not patch.has_edit or patch.files_edited_count == 0:
            adj[CDTYPE_ROOT_CAUSE] = adj.get(CDTYPE_ROOT_CAUSE, 0.0) + 0.2
            adj[CDTYPE_API_DEFINITION] = adj.get(CDTYPE_API_DEFINITION, 0.0) + 0.2

        if patch.multi_file_edit:
            adj[CDTYPE_CALLER_CALLEE] = adj.get(CDTYPE_CALLER_CALLEE, 0.0) + 0.2

        return adj

    # ------------------------------------------------------------------
    # Rationale
    # ------------------------------------------------------------------

    @staticmethod
    def _build_rationale(
        error_type: str,
        primary: str,
        scores: dict[str, float],
        prior_scores: dict[str, float],
        signal_evidence: dict[str, float],
        patch_scores: dict[str, float],
        signals: RuntimeSignals,
    ) -> str:
        """Build concise diagnosis rationale from scoring breakdown."""
        ranked = sorted(scores.items(), key=lambda x: -x[1])
        top_pairs = [f"{cdtype}={score:.2f}" for cdtype, score in ranked[:3]]

        return (
            f"primary={primary} ("
            + ", ".join(top_pairs)
            + f"). error_type={error_type}, "
            + f"exploration_mode={signals.exploration_mode}, "
            + f"error_edit_alignment={signals.error_edit_alignment}"
        )
