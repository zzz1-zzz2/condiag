"""ConDiag diagnosis normalizer — produce NormalizedDiagnosis from ManualDiagnosis.

Why: ManualDiagnosis mirrors the on-disk schema but is missing derived fields
(action_family) and isn't a flat representation. NormalizedDiagnosis is the
canonical form that retrieval executor / agent retry will consume.

This module also fills in defaults from taxonomy when fields are missing
(e.g. 5r_action / retry_intent / action_family from pathology).
"""
from __future__ import annotations

from typing import Optional

from .schemas import (
    ConDiagTaxonomyError,
    ManualDiagnosis,
    NormalizedDiagnosis,
    PathologyTaxonomy,
)


def normalize(md: ManualDiagnosis, taxonomy: PathologyTaxonomy) -> NormalizedDiagnosis:
    diag = md.diagnosis or {}

    pathology = diag.get("pathology", "")
    if not pathology:
        raise ConDiagTaxonomyError(f"[{md.instance_id}] diagnosis.pathology is empty")

    p_entry = taxonomy.pathology_by_id(pathology)
    if p_entry is None:
        raise ConDiagTaxonomyError(
            f"[{md.instance_id}] pathology '{pathology}' not in taxonomy"
        )

    # Derive action_family from taxonomy entry (canonical)
    action_family = p_entry.get("action_family", "ABSTAIN")

    # 5r_action: prefer manual value; fall back to taxonomy default
    action_5r = diag.get("5r_action") or p_entry.get("5r_action")

    # retry_intent: prefer manual value; fall back to taxonomy default
    retry_intent = md.retry_intent or p_entry.get("default_retry_intent", "")

    nd = NormalizedDiagnosis(
        instance_id=md.instance_id,
        pathology=pathology,
        action_family=action_family,
        primary_5r_action=action_5r,
        secondary_pathologies=diag.get("secondary_pathologies", []),
        scope=diag.get("scope", ""),
        gap_kind=diag.get("gap_kind"),
        primary_missing_context_type=diag.get("primary_missing_context_type"),
        secondary_missing_context_types=diag.get("secondary_missing_context_types", []),
        failure_mode=diag.get("failure_mode"),
        confidence=float(diag.get("confidence", 0.0)),
        abstain=bool(diag.get("abstain", False)),
        retry_intent=retry_intent,
        mode=md.mode,
        context_packet_instruction=md.context_packet_instruction,
        target_hints=md.target_hints or [],
    )
    return nd
