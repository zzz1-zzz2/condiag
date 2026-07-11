"""ConDiag action planner — split manual_diagnosis.retrieval_plan into
retrieval_actions vs control_actions.

Per user directive 2026-06-27:
    retrieval_actions  → executed by Retrieval Executor (LLM-driven code search)
    control_actions    → executed by Scope Guard / Runtime Validation Resolver
                         (no LLM, pure harness control: prune patch, run tests)

Operations not recognized by the taxonomy are reported as `unknown_operations`
so the loader's leakage/taxonomy check can surface them.
"""
from __future__ import annotations

from .schemas import ActionPlan, ManualDiagnosis, NormalizedDiagnosis, PathologyTaxonomy


def build_plan(
    md: ManualDiagnosis,
    nd: NormalizedDiagnosis,
    taxonomy: PathologyTaxonomy,
) -> ActionPlan:
    retrieval_set = set(taxonomy.retrieval_action_enum)
    control_set = set(taxonomy.control_action_enum)

    retrieval_actions = []
    control_actions = []
    unknown = []

    for step in md.retrieval_plan or []:
        if not isinstance(step, dict):
            unknown.append({"raw": step})
            continue
        op = step.get("operation") or step.get("op") or ""
        if op in retrieval_set:
            retrieval_actions.append(step)
        elif op in control_set:
            control_actions.append(step)
        else:
            unknown.append(step)

    return ActionPlan(
        instance_id=md.instance_id,
        pathology=nd.pathology,
        action_family=nd.action_family,
        primary_5r_action=nd.primary_5r_action,
        retrieval_actions=retrieval_actions,
        control_actions=control_actions,
        unknown_operations=unknown,
    )
