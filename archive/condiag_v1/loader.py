"""ConDiag loader — read runtime_signals / manual_diagnosis / taxonomy.

Pure stdlib, no LLM calls, no retrieval. Layered:
    load_taxonomy            → PathologyTaxonomy
    load_case_bundle         → (RuntimeSignals, CaseBundlePaths)
    load_manual_diagnosis    → ManualDiagnosis

Each function validates schema_version and required-field presence.
Leakage checks are in leakage_guard.py — loader does not duplicate them,
but it does check structural completeness.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from .schemas import (
    CaseBundlePaths,
    ConDiagSchemaError,
    ManualDiagnosis,
    PathologyTaxonomy,
    RuntimeSignals,
)


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"required file missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConDiagSchemaError(f"invalid JSON in {path}: {e}") from e


def load_taxonomy(path: Path) -> PathologyTaxonomy:
    """Load pathology_taxonomy.json.

    Required schema_version: condiag.pathology_taxonomy.v0.2
    """
    d = _load_json(path)
    sv = d.get("schema_version", "")
    if not sv.startswith("condiag.pathology_taxonomy.v0"):
        raise ConDiagSchemaError(
            f"taxonomy schema_version mismatch in {path}: got '{sv}', "
            f"expected 'condiag.pathology_taxonomy.v0.*'"
        )
    if "pathologies" not in d or not isinstance(d["pathologies"], list):
        raise ConDiagSchemaError(f"taxonomy missing 'pathologies' list in {path}")
    if not d["pathologies"]:
        raise ConDiagSchemaError(f"taxonomy 'pathologies' is empty in {path}")
    return PathologyTaxonomy.from_dict(d)


def load_case_bundle(case_dir: Path) -> Tuple[RuntimeSignals, CaseBundlePaths]:
    """Load runtime_signals.json from a case_bundle/<instance>/ directory.

    Returns (RuntimeSignals, CaseBundlePaths) where the paths point to all
    sibling files (raw_trajectory.json, patch.diff, etc.) — caller can use
    them to read more if needed.
    """
    case_dir = Path(case_dir)
    if not case_dir.is_dir():
        raise FileNotFoundError(f"case bundle dir missing: {case_dir}")

    rs_f = case_dir / "runtime_signals.json"
    raw = _load_json(rs_f)

    sv = raw.get("schema_version", "")
    if not sv.startswith("condiag.runtime_signals.v0"):
        raise ConDiagSchemaError(
            f"runtime_signals schema_version mismatch in {rs_f}: got '{sv}'"
        )

    rs = RuntimeSignals.from_dict(raw)

    # Required-field presence check
    missing = [
        f for f in (
            "instance_id", "exit_status",
            "viewed_files_count", "edited_files_count",
            "test_runs_count", "git_checkout_count",
        )
        if not hasattr(rs, f) or getattr(rs, f) is None
    ]
    if missing:
        raise ConDiagSchemaError(
            f"runtime_signals missing required fields in {rs_f}: {missing}"
        )

    paths = CaseBundlePaths(
        instance_id=rs.instance_id or case_dir.name,
        bundle_dir=case_dir,
        runtime_signals_f=rs_f,
        manual_diagnosis_f=None,    # set by caller if diagnosis exists
        raw_trajectory_f=case_dir / "raw_trajectory.json",
        patch_diff_f=case_dir / "patch.diff",
        issue_statement_f=case_dir / "issue_statement.txt",
        task_json_f=case_dir / "task.json",
    )
    return rs, paths


def load_manual_diagnosis(path: Path, taxonomy: PathologyTaxonomy) -> ManualDiagnosis:
    """Load a manual_diagnosis.json file.

    Validates:
      - schema_version starts with 'condiag.manual_diagnosis.v0'
      - all taxonomy.manual_diagnosis_required_fields are present
      - pathology / retry_intent are known to taxonomy (cross-ref)
      - 5r_action is known (if present)
    """
    path = Path(path)
    raw = _load_json(path)
    sv = raw.get("schema_version", "")
    if not sv.startswith("condiag.manual_diagnosis.v0"):
        raise ConDiagSchemaError(
            f"manual_diagnosis schema_version mismatch in {path}: got '{sv}'"
        )

    missing = [f for f in taxonomy.manual_diagnosis_required_fields if f not in raw]
    if missing:
        raise ConDiagSchemaError(
            f"manual_diagnosis missing required fields in {path}: {missing}"
        )

    md = ManualDiagnosis.from_dict(raw)

    # Cross-reference pathology / 5r_action / retry_intent against taxonomy
    pathology = md.diagnosis.get("pathology", "")
    if pathology and pathology not in taxonomy.pathology_ids():
        from .schemas import ConDiagTaxonomyError
        raise ConDiagTaxonomyError(
            f"manual_diagnosis.pathology '{pathology}' not in taxonomy "
            f"(known: {sorted(taxonomy.pathology_ids())})"
        )

    action_5r = md.diagnosis.get("5r_action")
    if action_5r:
        known_5r = {a["name"] for a in taxonomy.framework_definition.get("actions", [])}
        if action_5r not in known_5r:
            from .schemas import ConDiagTaxonomyError
            raise ConDiagTaxonomyError(
                f"manual_diagnosis.5r_action '{action_5r}' not in 5R enum "
                f"(known: {sorted(known_5r)})"
            )

    if md.retry_intent and md.retry_intent not in set(taxonomy.retry_intent_enum):
        from .schemas import ConDiagTaxonomyError
        raise ConDiagTaxonomyError(
            f"manual_diagnosis.retry_intent '{md.retry_intent}' not in enum "
            f"(known: {taxonomy.retry_intent_enum})"
        )

    return md
