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


def select(
    action_results: List[ActionResult],
    retry_intent: str,
    instance_id: str,
    budget: dict | None = None,
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

    # Sort by score desc; stable
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
