"""ConDiag v0 schemas — typed dictionaries for runtime signals, manual diagnosis,
taxonomy, and ConDiag dry-run output.

Uses dataclasses (stdlib only — no pydantic) so the package works
under the WSL Python 3.10 env without extra deps.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# =====================================================================
# Inputs
# =====================================================================

@dataclass
class RuntimeSignals:
    """Mirror of runtime_signals.json (schema v0.1).

    Only runtime-visible fields. No oracle metrics (file_cov/EditLoc/gold_*)
    allowed here — leakage_guard rejects them.
    """
    schema_version: str = ""
    instance_id: str = ""
    exit_status: str = ""

    api_calls: int = 0
    n_messages: int = 0
    n_assistant_messages: int = 0
    n_user_messages: int = 0

    viewed_files_count: int = 0
    viewed_files_in_order: list = field(default_factory=list)
    viewed_spans: dict = field(default_factory=dict)
    viewed_total_line_bytes: int = 0

    edited_files: list = field(default_factory=list)
    edited_files_count: int = 0
    edited_spans_per_file: dict = field(default_factory=dict)
    edited_hunks_total: int = 0

    search_commands: list = field(default_factory=list)
    search_commands_count: int = 0

    test_runs: list = field(default_factory=list)
    test_runs_count: int = 0
    test_output_samples: list = field(default_factory=list)

    patch_context_files: list = field(default_factory=list)
    patch_context_files_count: int = 0

    # v0.1 additions
    changed_files_count: int = 0
    changed_lines_total: int = 0
    changed_lines_added: int = 0
    changed_lines_removed: int = 0
    repeated_edit_patterns: list = field(default_factory=list)
    repeated_edit_pattern_detected: bool = False
    final_patch_context_files: list = field(default_factory=list)
    final_patch_context_files_count: int = 0
    viewed_but_not_final_files: list = field(default_factory=list)
    viewed_but_not_final_files_count: int = 0
    edited_but_not_viewed_files: list = field(default_factory=list)
    edited_but_not_viewed_files_count: int = 0
    test_commands: list = field(default_factory=list)
    test_failures: list = field(default_factory=list)
    test_failures_count: int = 0
    possible_regression_failures: list = field(default_factory=list)
    submitted_without_tests: bool = False
    git_checkout_count: int = 0

    last_user_messages_tail: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "RuntimeSignals":
        """Tolerant loader — keeps known fields, ignores unknowns."""
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ManualDiagnosis:
    """Mirror of manual_diagnosis.json (schema v0)."""
    schema_version: str = ""
    instance_id: str = ""
    agent: str = ""
    model: str = ""
    source: str = ""
    mode: str = ""
    mode_note: Optional[str] = None

    trigger_assessment: dict = field(default_factory=dict)
    runtime_evidence: dict = field(default_factory=dict)
    diagnosis: dict = field(default_factory=dict)
    target_hints: list = field(default_factory=list)
    retrieval_plan: list = field(default_factory=list)
    retry_intent: str = ""
    context_packet_instruction: str = ""
    gold_check: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ManualDiagnosis":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PathologyTaxonomy:
    """Mirror of pathology_taxonomy.json (schema v0.2)."""
    schema_version: str = ""
    version_date: str = ""
    framework: str = ""
    framework_definition: dict = field(default_factory=dict)
    pathologies: list = field(default_factory=list)
    action_family_enum: list = field(default_factory=list)
    gap_kind_enum: list = field(default_factory=list)
    scope_enum: list = field(default_factory=list)
    missing_context_type_enum: list = field(default_factory=list)
    failure_mode_enum: list = field(default_factory=list)
    secondary_pathology_enum: list = field(default_factory=list)
    retrieval_action_enum: list = field(default_factory=list)
    control_action_enum: list = field(default_factory=list)
    retry_intent_enum: list = field(default_factory=list)
    trigger_type_enum: list = field(default_factory=list)
    architectural_modules: list = field(default_factory=list)
    manual_diagnosis_required_fields: list = field(default_factory=list)
    runtime_signals_required_fields: list = field(default_factory=list)
    leakage_forbidden_fields_in_runtime_path: list = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "PathologyTaxonomy":
        known = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in d.items() if k in known}
        return cls(**kwargs)

    def pathology_ids(self) -> set:
        return {p["id"] for p in self.pathologies}

    def pathology_by_id(self, pid: str) -> Optional[dict]:
        for p in self.pathologies:
            if p["id"] == pid:
                return p
        return None


# =====================================================================
# Outputs (dry-run produces these)
# =====================================================================

@dataclass
class TriggerResult:
    """Output of trigger.py — automatic trigger classification from runtime_signals."""
    instance_id: str = ""
    triggered: bool = False
    trigger_type: str = "NO_TRIGGER"
    trigger_layer: Optional[str] = None
    trigger_reasons: list = field(default_factory=list)
    scope_anomaly_score: int = 0
    scope_anomaly_threshold_warning: int = 2
    scope_anomaly_threshold_strong: int = 3
    scope_signals: dict = field(default_factory=dict)
    runtime_validation_signals: dict = field(default_factory=dict)
    inferred_pathology_candidates: list = field(default_factory=list)
    inferred_action_family: str = "ABSTAIN"
    confidence_runtime: float = 0.0
    notes: list = field(default_factory=list)


@dataclass
class ActionPlan:
    """Output of action_planner.py — split retrieval vs control actions."""
    instance_id: str = ""
    pathology: str = ""
    action_family: str = ""
    primary_5r_action: Optional[str] = None
    retrieval_actions: list = field(default_factory=list)
    control_actions: list = field(default_factory=list)
    unknown_operations: list = field(default_factory=list)


@dataclass
class NormalizedDiagnosis:
    """Output of diagnosis_normalizer.py — manual_diagnosis with derived fields."""
    instance_id: str = ""
    pathology: str = ""
    action_family: str = ""
    primary_5r_action: Optional[str] = None
    secondary_pathologies: list = field(default_factory=list)
    scope: str = ""
    gap_kind: Optional[str] = None
    primary_missing_context_type: Optional[str] = None
    secondary_missing_context_types: list = field(default_factory=list)
    failure_mode: Optional[str] = None
    confidence: float = 0.0
    abstain: bool = False
    retry_intent: str = ""
    mode: str = ""
    context_packet_instruction: str = ""
    target_hints: list = field(default_factory=list)


@dataclass
class CaseBundlePaths:
    """Locations of input files for one instance."""
    instance_id: str
    bundle_dir: Path
    runtime_signals_f: Path
    manual_diagnosis_f: Path
    raw_trajectory_f: Path
    patch_diff_f: Path
    issue_statement_f: Path
    task_json_f: Path


# =====================================================================
# Errors
# =====================================================================

class ConDiagSchemaError(ValueError):
    """Raised when input files do not match schema."""


class ConDiagLeakageError(ValueError):
    """Raised when runtime path reads oracle-only fields."""


class ConDiagTaxonomyError(ValueError):
    """Raised when diagnosis refers to unknown pathology/action/intent."""
