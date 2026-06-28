"""ConDiag leakage guard — reject oracle-only fields entering runtime path.

Per design: gold_check fields (contextbench_metrics, gold_patch_shape,
oracle_pathology_label, oracle_notes, official_eval, FAIL_TO_PASS, PASS_TO_PASS,
resolved) are EVALUATION_ONLY — they can be carried alongside for offline audit
but must NOT be read by runtime ConDiag trigger / classifier / retriever.

This module scans:
    - runtime_signals (the dict from runtime_signals.json)
    - manual_diagnosis top-level fields (everything except gold_check)
    - any 'trigger_assessment' / 'runtime_evidence' / 'diagnosis' sub-dicts

and reports if any forbidden field appears outside gold_check / official_eval.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

from .schemas import ConDiagLeakageError, ManualDiagnosis, PathologyTaxonomy, RuntimeSignals


# Paths in manual_diagnosis that ARE allowed to carry oracle fields
# (these are explicitly evaluation-only containers).
_GOLD_CHECK_WHITELIST = {"gold_check"}


@dataclass
class LeakageReport:
    instance_id: str = ""
    ok: bool = True
    forbidden_fields_seen: List[str] = field(default_factory=list)
    forbidden_locations: List[str] = field(default_factory=list)  # dot-paths
    notes: List[str] = field(default_factory=list)

    def raise_if_leak(self) -> None:
        if not self.ok:
            summary = "; ".join(self.forbidden_locations) or ", ".join(self.forbidden_fields_seen)
            raise ConDiagLeakageError(
                f"[{self.instance_id}] runtime leakage detected: {summary}"
            )


def _walk_for_keys(obj, prefix: str, forbidden: set[str]):
    """Yield (dot_path, key) for each forbidden key found at any depth."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if k in forbidden:
                yield path, k
            yield from _walk_for_keys(v, path, forbidden)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            yield from _walk_for_keys(item, f"{prefix}[{i}]", forbidden)


def check_runtime_signals(rs: RuntimeSignals, taxonomy: PathologyTaxonomy) -> LeakageReport:
    """Runtime signals should NEVER carry oracle fields."""
    forbidden = set(taxonomy.leakage_forbidden_fields_in_runtime_path)
    if not forbidden:
        return LeakageReport(instance_id=rs.instance_id, ok=True, notes=["taxonomy has no forbidden-field list"])

    d = rs.to_dict()
    leaks = list(_walk_for_keys(d, "", forbidden))
    report = LeakageReport(instance_id=rs.instance_id)
    if leaks:
        report.ok = False
        report.forbidden_locations = [p for p, _ in leaks]
        report.forbidden_fields_seen = sorted({k for _, k in leaks})
    return report


def check_manual_diagnosis(md: ManualDiagnosis, taxonomy: PathologyTaxonomy) -> LeakageReport:
    """Manual diagnosis: oracle fields allowed ONLY inside `gold_check`.

    All other top-level fields (trigger_assessment, runtime_evidence, diagnosis,
    target_hints, retrieval_plan, etc.) are runtime-readable and must be
    oracle-free.
    """
    forbidden = set(taxonomy.leakage_forbidden_fields_in_runtime_path)
    if not forbidden:
        return LeakageReport(instance_id=md.instance_id, ok=True, notes=["taxonomy has no forbidden-field list"])

    raw = md.to_dict()
    report = LeakageReport(instance_id=md.instance_id)
    for top_key, top_val in raw.items():
        if top_key in _GOLD_CHECK_WHITELIST:
            continue
        if not isinstance(top_val, (dict, list)):
            continue
        leaks = list(_walk_for_keys(top_val, top_key, forbidden))
        for path, key in leaks:
            report.forbidden_locations.append(path)
            report.forbidden_fields_seen.append(key)

    if report.forbidden_locations:
        report.ok = False
        report.forbidden_fields_seen = sorted(set(report.forbidden_fields_seen))

    # Additionally, gold_check.allowed_for_runtime must be False
    gc = raw.get("gold_check") or {}
    if gc.get("allowed_for_runtime") is not False:
        report.ok = False
        report.notes.append(
            "gold_check.allowed_for_runtime must be explicitly False "
            "(signals runtime must not consume it)"
        )

    return report
