"""ConDiag evidence selector — pick top-k candidates under budget.

Inputs:
  - List[ActionResult] from retrieval_executor
  - budget: {max_files, max_lines, max_evidence}

Selection policy (v0):
  1. Drop duplicates by (path, start_line, end_line).
  2. Sort by score descending (stable: ties keep retrieval order).
  3. Apply diversity: at most 2 candidates per (path, operation) pair unless
     we have spare budget — prevents one operation/file from dominating.
  4. Greedily fill until max_evidence or max_lines exceeded.

Output: SelectedEvidence dict matching the user-specified schema.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .retrieval_executor import ActionResult, EvidenceCandidate


DEFAULT_BUDGET = {
    "max_files": 6,
    "max_lines": 300,
    "max_evidence": 8,
}


def _span_len(c: EvidenceCandidate) -> int:
    return max(0, c.end_line - c.start_line + 1)


# Diagnosis-aware reranking: adjust evidence scores based on context_deficiency_type
# so REHYDRATE doesn't dominate when the diagnosis says to look elsewhere.
_DIAGNOSIS_RERANK = {
    "ROOT_CAUSE_RELOCALIZATION": {
        "previously_seen_but_dropped": 0.5,
        "target_symbol_definition": 1.3,
        "enclosing_class_definition": 1.2,
        "sibling_method_implementation": 1.2,
        "neighbor_test_by_concept": 1.1,
    },
    "INTERFACE_CONSTRAINT_CONTEXT": {
        "previously_seen_but_dropped": 0.7,
        "target_symbol_definition": 1.3,
        "sibling_method_implementation": 1.1,
        "enclosing_class_definition": 1.2,
        "visible_regression_test": 1.1,
    },
    "REGRESSION_CONSTRAINT_CONTEXT": {
        "previously_seen_but_dropped": 0.8,
        "target_symbol_definition": 1.1,
        "visible_regression_test": 1.4,
        "target_fix_test": 1.2,
        "neighbor_test_by_concept": 1.1,
    },
    "RELATED_TEST_CONTEXT": {
        "previously_seen_but_dropped": 0.7,
        "target_fix_test": 1.3,
        "neighbor_test_by_concept": 1.3,
        "visible_regression_test": 1.2,
    },
    "EDIT_SCOPE_CONTEXT": {
        "previously_seen_but_dropped": 1.1,
        "target_symbol_definition": 0.8,
        "neighbor_test_by_concept": 0.8,
    },
    "API_DEFINITION_CONTEXT": {
        "previously_seen_but_dropped": 0.7,
        "target_symbol_definition": 1.3,
        "enclosing_class_definition": 1.2,
    },
    "CALLER_CALLEE_CONTEXT": {
        "previously_seen_but_dropped": 0.7,
        "target_symbol_definition": 1.2,
        "enclosing_class_definition": 1.2,
    },
}


def _rerank_by_diagnosis(
    candidates: list[EvidenceCandidate],
    context_deficiency_type: str,
) -> list[EvidenceCandidate]:
    """Adjust candidate scores based on context_deficiency_type.

    REHYDRATE evidence gets penalized in ROOT_CAUSE_RELOCALIZATION (it anchors
    the agent to the old, wrong symptom layer). Symbol definitions get boosted
    across most deficiency types since they provide new, targeted context.
    """
    mults = _DIAGNOSIS_RERANK.get(context_deficiency_type, {})
    if not mults:
        return candidates

    reranked: list[EvidenceCandidate] = []
    for c in candidates:
        rel = c.relation
        multiplier = mults.get(rel, 1.0)
        if multiplier != 1.0:
            from dataclasses import replace
            reranked.append(replace(c, score=c.score * multiplier))
        else:
            reranked.append(c)

    return reranked


def select(
    action_results: List[ActionResult],
    retry_intent: str,
    instance_id: str,
    budget: dict | None = None,
    context_deficiency_type: str = "",
) -> dict:
    """Build selected_evidence.json payload."""
    budget = {**DEFAULT_BUDGET, **(budget or {})}

    # Flatten candidates preserving retrieval order
    flat: list[EvidenceCandidate] = []
    for r in action_results:
        for c in r.candidates:
            flat.append(c)

    # Dedup by (path, start, end)
    seen: set = set()
    deduped: list[EvidenceCandidate] = []
    for c in flat:
        key = (c.path, c.start_line, c.end_line)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(c)

    # Diagnosis-aware reranking: adjust sort order, not absolute scores,
    # so the MIN_EVIDENCE_SCORE filter in context_packet_builder still works.
    if context_deficiency_type:
        mults = _DIAGNOSIS_RERANK.get(context_deficiency_type, {})
        if mults:
            deduped.sort(key=lambda c: -c.score * mults.get(c.relation, 1.0))
        else:
            deduped.sort(key=lambda c: -c.score)
    else:
        deduped.sort(key=lambda c: -c.score)
    deduped.sort(key=lambda c: -c.score)

    # Greedy with diversity: at most 2 per (path, operation)
    selected: list[EvidenceCandidate] = []
    per_key: dict = {}
    total_lines = 0
    total_files: set = set()
    for c in deduped:
        if len(selected) >= budget["max_evidence"]:
            break
        if total_lines >= budget["max_lines"]:
            break
        if len(total_files) >= budget["max_files"] and c.path not in total_files:
            continue
        key = (c.path, c.operation)
        if per_key.get(key, 0) >= 2:
            continue
        span = _span_len(c)
        if total_lines + span > budget["max_lines"]:
            # try to fit smaller ones later; skip this one
            continue
        selected.append(c)
        per_key[key] = per_key.get(key, 0) + 1
        total_lines += span
        total_files.add(c.path)

    # Make sure we have at least 1 of each required relation type when available
    required_relations = ["visible_regression_test", "target_symbol_definition",
                          "enclosing_class_definition", "sibling_method_implementation",
                          "previously_seen_but_dropped"]
    have_relations = {c.relation for c in selected}
    for req in required_relations:
        if any(c.relation == req for c in deduped) and req not in have_relations:
            # Try to add one
            for c in deduped:
                if c.relation == req and c not in selected:
                    span = _span_len(c)
                    if total_lines + span <= budget["max_lines"] and len(selected) < budget["max_evidence"]:
                        selected.append(c)
                        per_key[(c.path, c.operation)] = per_key.get((c.path, c.operation), 0) + 1
                        total_lines += span
                        total_files.add(c.path)
                        have_relations.add(req)
                        break

    return {
        "instance_id": instance_id,
        "retry_intent": retry_intent,
        "evidence": [c.to_dict() for c in selected],
        "budget": budget,
        "selection_summary": {
            "candidate_count": len(flat),
            "deduped_count": len(deduped),
            "selected_count": len(selected),
            "selected_lines_total": total_lines,
            "selected_files_count": len(total_files),
            "relations_present": sorted(have_relations),
        },
    }
