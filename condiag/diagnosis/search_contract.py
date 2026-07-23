"""P1-3C: Search Contract — bounded, template-driven retrieval plans.

Each DiagnosisHypothesis is converted into a small set of SearchAction
objects. The templates are deterministic: same hypothesis_id + same
action template = same action list. No LLM. No free-text.

Action types are a fixed enum — Router can dispatch by type without
parsing strings.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from condiag.diagnosis.hypothesis import DiagnosisHypothesis


class SearchActionType(str, Enum):
    """Closed enum of retrieval action types.

    Each action maps to one Router implementation. Adding a new type
    is a deliberate, schema-versioned decision.
    """

    FIND_DEFINITION = "FIND_DEFINITION"
    FIND_PARALLEL_IMPLEMENTATION = "FIND_PARALLEL_IMPLEMENTATION"
    FIND_RELATED_TESTS = "FIND_RELATED_TESTS"
    FIND_CALLEES = "FIND_CALLEES"
    FIND_CALLERS = "FIND_CALLERS"
    FIND_REGISTRATION_SITE = "FIND_REGISTRATION_SITE"
    REHYDRATE_VIEWED_EVIDENCE = "REHYDRATE_VIEWED_EVIDENCE"
    # Sentinel actions
    ABSTAIN = "ABSTAIN"
    NOOP = "NOOP"


@dataclass
class SearchAction:
    """One bounded retrieval action.

    `target` is the symbol/file name the Router will look up.
    `budget` is the maximum number of files/result the action may produce.
    `stop_condition` describes when the Router should stop and report back.
    """

    action_id: str = ""
    action_type: SearchActionType = SearchActionType.NOOP
    target: str = ""
    reason: str = ""
    cluster_ids: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    budget: int = 2
    stop_condition: str = "one relevant result found"
    abstained: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action_type": self.action_type.value,
            "target": self.target,
            "reason": self.reason,
            "cluster_ids": list(self.cluster_ids),
            "evidence_ids": list(self.evidence_ids),
            "budget": self.budget,
            "stop_condition": self.stop_condition,
            "abstained": self.abstained,
        }


@dataclass
class SearchContract:
    """A complete retrieval plan for one hypothesis (or one cluster)."""

    hypothesis_id: str = ""
    actions: list[SearchAction] = field(default_factory=list)
    abstained: bool = False  # True when no actions were generated
    abstention_reason: str = ""
    total_budget: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "actions": [a.to_dict() for a in self.actions],
            "abstained": self.abstained,
            "abstention_reason": self.abstention_reason,
            "total_budget": self.total_budget,
        }


# ── Templates ──────────────────────────────────────────────────────


# Each template: (subtype, action_type, target_selector, reason)
# - target_selector: callable(hypothesis) → list of strings (target candidates)
# - Each subtype can emit 1..N actions; budget applies globally.
def _t_failure_site(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    """For frame/site failures: look up the symbol at the failure site."""
    if not hyp.failure_sites:
        return []
    actions = []
    for site in hyp.failure_sites[:2]:
        # Take the file name without the line number
        file_part = site.split(":")[0] if ":" in site else site
        if not file_part:
            continue
        actions.append((
            SearchActionType.FIND_DEFINITION,
            file_part,
            f"failure site: {site}",
        ))
    return actions


def _t_find_callers(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    """For target symbols: who calls this?"""
    actions = []
    for sym in hyp.retrieval_targets[:2]:
        if sym:
            actions.append((
                SearchActionType.FIND_CALLERS,
                sym,
                f"who calls {sym}",
            ))
    return actions


def _t_find_callees(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    """For target symbols: what does this call?"""
    actions = []
    for sym in hyp.retrieval_targets[:2]:
        if sym:
            actions.append((
                SearchActionType.FIND_CALLEES,
                sym,
                f"what does {sym} call",
            ))
    return actions


def _t_find_related_tests(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    """Find tests that exercise the failing function/symbol."""
    actions = []
    for sym in hyp.retrieval_targets[:2]:
        if sym:
            actions.append((
                SearchActionType.FIND_RELATED_TESTS,
                sym,
                f"tests for {sym}",
            ))
    return actions


def _t_find_parallel_implementation(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    """Find existing parallel implementations of the same kind."""
    actions = []
    # Use the failure-site file as anchor
    for site in hyp.failure_sites[:1]:
        file_part = site.split(":")[0] if ":" in site else site
        if file_part:
            # Anchor on the module path, not the line
            actions.append((
                SearchActionType.FIND_PARALLEL_IMPLEMENTATION,
                file_part,
                "find existing similar pattern",
            ))
    return actions


def _t_find_registration_site(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    """Find the registration site (e.g. __init__.py exports)."""
    actions = []
    for site in hyp.failure_sites[:1]:
        file_part = site.split(":")[0] if ":" in site else site
        if file_part:
            actions.append((
                SearchActionType.FIND_REGISTRATION_SITE,
                file_part,
                "registration site",
            ))
    return actions


def _t_rehydrate_viewed(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    """Mark that the agent already saw a relevant file; Reshaping rehydrates it."""
    actions = []
    for site in hyp.failure_sites[:1]:
        if site:
            actions.append((
                SearchActionType.REHYDRATE_VIEWED_EVIDENCE,
                site,
                "this was viewed but lost during compression",
            ))
    return actions


def _t_noop(hyp: DiagnosisHypothesis) -> list[tuple[SearchActionType, str, str]]:
    return [(SearchActionType.NOOP, "", "no actionable retrieval target")]


# ── Subtype → template dispatch ─────────────────────────────────────


# Ordered list of (subtype, template_fn).
# First match wins; NOOP is the default.
_TEMPLATE_DISPATCH: list[tuple[str, Any]] = [
    # ── API_DEFINITION subtypes ──
    ("FRAME_ATTRIBUTE_PROPAGATION", _t_failure_site),
    ("FUNCTION_SIGNATURE_MISMATCH", _t_failure_site),
    ("CLASS_ATTRIBUTE_MISSING", _t_failure_site),
    ("METHOD_NOT_FOUND", _t_failure_site),
    ("MODULE_MEMBER_MISSING", _t_failure_site),
    # ── INTERFACE_CONSTRAINT subtypes ──
    ("ARGUMENT_TYPE_MISMATCH", _t_find_callers),
    ("FRAME_ATTRIBUTE_CONSTRAINT", _t_find_callers),
    # ── RELATED_TESTS subtypes ──
    ("NUMERICAL_MISMATCH", _t_find_related_tests),
    ("ROUTE_COMPARISON_FAILURE", _t_find_parallel_implementation),
    ("EDGE_CASE_MISSING", _t_find_related_tests),
    ("REGRESSION_DETECTED", _t_find_related_tests),
    # ── CALLER_CALLEE ──
    ("FRAME_MUTATION_IN_TRANSFORM", _t_find_callees),
    ("ARGUMENT_FORWARDING_MISMATCH", _t_find_callees),
    # ── LOCALIZATION_DIRECTION ──
    ("WRONG_FILE_MODIFIED", _t_failure_site),
    ("WRONG_FUNCTION_MODIFIED", _t_failure_site),
    ("SYMPTOM_CAUSE_MISMATCH", _t_find_parallel_implementation),
    # ── REGISTRATION_SITE ──
    ("TRANSFORM_NOT_REGISTERED", _t_find_registration_site),
    ("CONFIG_NOT_UPDATED", _t_find_registration_site),
    ("EXPORT_MISSING", _t_find_registration_site),
    # ── DEPENDENCY ──
    ("MISSING_IMPORT", _t_failure_site),
    ("MISSING_DATA_FILE", _t_noop),
    ("MISSING_SYSTEM_DEP", _t_noop),
    # ── Fallback ──
    ("UNCLASSIFIED", _t_noop),
]


def _select_template(subtype: str):
    for s, fn in _TEMPLATE_DISPATCH:
        if s == subtype:
            return fn
    return _t_noop


# ── Builder ─────────────────────────────────────────────────────────


def _make_action_id(hyp: DiagnosisHypothesis, action_type: SearchActionType, target: str) -> str:
    raw = f"{hyp.hypothesis_id}|{action_type.value}|{target}"
    return "A" + hashlib.sha256(raw.encode()).hexdigest()[:8]


def build_search_contract(
    hypothesis: DiagnosisHypothesis,
    *,
    global_budget: int = 4,
) -> SearchContract:
    """Build a SearchContract from a single DiagnosisHypothesis.

    Args:
        hypothesis: One DiagnosisHypothesis from the Reasoner.
        global_budget: Maximum total actions to emit for this hypothesis.

    Rules:
      - If the hypothesis is REJECTED or ABSTAINED, return an abstained
        contract with NOOP explanation.
      - Apply the subtype template; cap actions by global_budget.
      - Each action's `budget` field defaults to 2; never > 5.
    """
    from condiag.diagnosis.hypothesis import HypothesisStatus

    if hypothesis.status in (HypothesisStatus.REJECTED, HypothesisStatus.ABSTAINED):
        return SearchContract(
            hypothesis_id=hypothesis.hypothesis_id,
            actions=[],
            abstained=True,
            abstention_reason=f"hypothesis is {hypothesis.status.value}",
            total_budget=0,
        )

    if hypothesis.confidence == "low" and not hypothesis.retrieval_targets \
            and not hypothesis.failure_sites:
        return SearchContract(
            hypothesis_id=hypothesis.hypothesis_id,
            actions=[],
            abstained=True,
            abstention_reason="low confidence + no actionable targets",
            total_budget=0,
        )

    template = _select_template(hypothesis.subtype)
    raw_actions = template(hypothesis)

    actions: list[SearchAction] = []
    for action_type, target, reason in raw_actions:
        if len(actions) >= global_budget:
            break
        action = SearchAction(
            action_id=_make_action_id(hypothesis, action_type, target),
            action_type=action_type,
            target=target,
            reason=reason,
            cluster_ids=list(hypothesis.cluster_ids),
            evidence_ids=list(hypothesis.supporting_evidence_ids),
            budget=2,
        )
        actions.append(action)

    return SearchContract(
        hypothesis_id=hypothesis.hypothesis_id,
        actions=actions,
        abstained=not actions,
        abstention_reason="" if actions else "no actionable targets",
        total_budget=sum(a.budget for a in actions),
    )


def build_search_contracts(
    hypotheses: list[DiagnosisHypothesis],
    *,
    global_budget: int = 4,
) -> list[SearchContract]:
    """Build a SearchContract for each hypothesis."""
    return [build_search_contract(h, global_budget=global_budget) for h in hypotheses]


# ── Plan serializer ─────────────────────────────────────────────────


def serialize_plan(
    contracts: list[SearchContract],
    hypotheses: list[DiagnosisHypothesis],
) -> dict[str, Any]:
    """Serialize a list of contracts + hypotheses as a Shadow artifact."""
    return {
        "version": "1",
        "hypotheses": [h.to_dict() for h in hypotheses],
        "contracts": [c.to_dict() for c in contracts],
        "n_actions": sum(len(c.actions) for c in contracts),
        "n_abstained": sum(1 for c in contracts if c.abstained),
    }
