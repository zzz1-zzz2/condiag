"""Context Deficiency Diagnoser — scoring-based CDType classification.

Maps error_type + trajectory signals + patch behavior to a 7-type
Context Deficiency Diagnosis.

Pipeline position:
  FailureWitness → TrajectorySignals → ContextDeficiencyDiagnosis
    → DiagnosticSearchContract → Attempt-2 Agent

Design (plan_version=plan_v2.0_search_contract):
  score = prior × 0.4 + signal_evidence × 0.4 + patch_behavior × 0.2

  - Prior: error_type → CDType initial weights (probabilistic, not hard rules)
  - Signal evidence: trajectory signals adjust scores
  - Patch behavior: empty/multi-file edit patterns

  No hard keyword→CDType mapping. No gold leakage.
  Gold leakage guard: uses only error_type + trajectory signals + patch behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

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
        The highest-scoring CDType, or "unknown" if all scores < 0.3.
    cdtype_scores:
        All 7 types with 0.0-1.0 scores (keys are CDTYPE_* constants).
    diagnosis_rationale:
        Concise explanation of why this type was chosen.
    diagnosis_version:
        Schema version, incremented on breaking changes.
    """
    primary_cdtype: str = "unknown"
    cdtype_scores: dict[str, float] = field(default_factory=dict)
    diagnosis_rationale: str = ""
    diagnosis_version: str = "1.0"


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
# Prior mapping: error_type → CDType base scores
#
# These are NOT hard rules — they act as Bayesian priors that get adjusted
# by signal evidence. Unknown error types get uniform 0.1 prior.
# =====================================================================

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


# =====================================================================
# ContextDeficiencyDiagnoser
# =====================================================================


class ContextDeficiencyDiagnoser:
    """Score each CDType from error_type + trajectory signals + patch behavior.

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
    ) -> ContextDeficiencyDiagnosis:
        """Run full scoring pipeline and return diagnosis.

        Pipeline: prior → signal evidence → patch behavior → weighted sum.

        Args:
            error_type: From failure witness (e.g. "AttributeError").
            signals: Pre-computed RuntimeSignals from trajectory.
            patch_behavior: PatchBehavior from Attempt-1 patch.

        Returns:
            ContextDeficiencyDiagnosis with primary_cdtype, scores, rationale.
        """
        prior_scores = self._compute_prior(error_type)
        signal_evidence = self._compute_signal_evidence(signals, error_type)
        patch_scores = self._compute_patch_scores(patch_behavior)

        # Weighted sum: prior × 0.4 + signal × 0.4 + patch × 0.2
        scores: dict[str, float] = {}
        for cdtype in _ALL_CDTYPES:
            scores[cdtype] = (
                prior_scores.get(cdtype, _UNIFORM_PRIOR) * 0.4
                + signal_evidence.get(cdtype, 0.0) * 0.4
                + patch_scores.get(cdtype, 0.0) * 0.2
            )

        # Clamp to [0.0, 1.0]
        for cdtype in scores:
            scores[cdtype] = max(0.0, min(1.0, scores[cdtype]))

        # Primary type: highest-score, with "unknown" floor for low-confidence
        primary = max(scores, key=scores.get) if scores else "unknown"
        top_score = scores.get(primary, 0.0)
        if top_score < 0.25:
            primary = "unknown"

        rationale = self._build_rationale(
            error_type, primary, scores, prior_scores,
            signal_evidence, patch_scores, signals,
        )

        return ContextDeficiencyDiagnosis(
            primary_cdtype=primary,
            cdtype_scores=scores,
            diagnosis_rationale=rationale,
            diagnosis_version="1.0",
        )

    # ------------------------------------------------------------------
    # Scoring components
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_prior(error_type: str) -> dict[str, float]:
        """Get base prior scores from error_type."""
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
            adj[CDTYPE_CALLER_CALLEE] = adj.get(CDTYPE_CALLER_CALLEE, 0.0) + 0.3
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
            # Default: this is a localization problem (root cause missing).
            adj[CDTYPE_ROOT_CAUSE] = adj.get(CDTYPE_ROOT_CAUSE, 0.0) + 0.3
            # Only boost API_DEFINITION when error_type indicates missing
            # API/symbol — otherwise the file is unvisited for a different
            # reason (e.g. wrong stack frame, wrong file path).
            if error_type in {
                "AttributeError", "NameError", "ImportError",
                "ModuleNotFoundError",
            }:
                adj[CDTYPE_API_DEFINITION] = adj.get(CDTYPE_API_DEFINITION, 0.0) + 0.3
        elif alignment == "aligned":
            adj[CDTYPE_ROOT_CAUSE] = adj.get(CDTYPE_ROOT_CAUSE, 0.0) + 0.3

        # Regression signal
        if signals.has_regression_signal:
            adj[CDTYPE_REGRESSION_CONSTRAINT] = adj.get(CDTYPE_REGRESSION_CONSTRAINT, 0.0) + 0.4

        # Visit alignment
        if signals.error_visit_alignment == "visited_none":
            # Agent never visited any stack frame — strong localization gap.
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
