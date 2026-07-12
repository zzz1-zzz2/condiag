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
# Adapter schemas (retry injection)
# =====================================================================

@dataclass
class RetryRequest:
    """Request payload for retry injection adapter."""
    instance_id: str = ""
    baseline_name: str = ""
    repo_path: Optional[Path] = None
    context_packet_path: Optional[Path] = None
    issue_text: str = ""
    max_steps: int = 60
    timeout_sec: int = 1800
    retry_reason: str = ""
    should_retry: bool = True
    attempt1_patch_path: Optional[Path] = None
    attempt1_runtime_signals_path: Optional[Path] = None
    intervention_report_path: Optional[Path] = None
    base_commit: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict (for JSON logging). Converts Path to str."""
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Path):
                d[k] = str(v)
        return d


@dataclass
class RetryInput:
    """Output of retry injection adapter — ready to feed to mini-SWE runner."""
    instance_id: str = ""
    baseline_name: str = ""
    repo_path: Optional[Path] = None
    task_message: str = ""
    context_packet_path: Optional[Path] = None
    metadata: dict = field(default_factory=dict)
    run_dir: Optional[Path] = None
    command: list = field(default_factory=list)

    def to_dict(self) -> dict:
        """Serialize to dict (for JSON logging). Converts Path to str."""
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, Path):
                d[k] = str(v)
        return d


# =====================================================================
# Post-validation / CI-Feedback schemas (Task 3-5)
# =====================================================================

# Hint-source boundaries — canonical copy lives in
# experiments/experiment_settings.py (ALLOWED_HINT_SOURCES /
# FORBIDDEN_HINT_SOURCES).  The tuples here provide fast membership
# checks without a cross-package import.
_ALLOWED_HINT_SOURCES = frozenset({
    "public_api_signature",
    "repo_source_signature",
    "issue_keyword_api_match",
    "runtime_introspection",
})

_FORBIDDEN_HINT_SOURCES = frozenset({
    "gold_patch",
    "feedback_success_patch",
    "manual_hindsight_only",
    "contextbench_oracle",
    "gold_context",
    "resolved_label",
})


@dataclass
class FailureWitness:
    """Structured record of post-validation failure output (v2).

    Stage-aware: separates failure_observed (ground truth) from
    eligible_for_condiag (whether the failure is a code-context deficiency
    vs. infrastructure / patch-apply / timeout).

    Fields:
        instance_id: SWE-bench instance identifier.
        has_failure_witness: True if any failure signal was captured
            (legacy field, kept for backwards compatibility).
        failure_observed: True if official eval confirmed failure
            (ground truth, independent of parsing success).
        failure_stage: High-level stage of failure.
            "validation_failure" = tests ran and failed;
            "patch_apply_failure" = patch didn't apply;
            "test_collection_failure" = tests couldn't be collected;
            "dependency_or_environment_failure" = infra/dep missing;
            "timeout" = eval timed out;
            "unknown_failure" = cannot determine.
        failure_type: Categorisation of the failure (e.g. "AssertionError").
        test_framework: Detected test framework.
            One of: "pytest", "unittest", "go_test", "cargo_test",
            "catch2", "gtest", "junit", "jest", "mocha",
            "ansible_custom", "autoconf_make", "cmake", "generic",
            "unknown".
        failed_tests: List of failing test identifiers.
        error_message: Raw error message from validation run.
        stack_trace: Full stack trace frames.
        top_repo_frames: Frames from the target repository (not test infra).
        expected: Expected value (nullable).
        actual: Actual value (nullable).
        validation_command: Command used to run validation.
        eligible_for_condiag: True if this failure is a code-context
            deficiency suitable for ConDiag diagnosis.
        quality: Evidence quality rating.
            "strong" = structured failure data extracted;
            "moderate" = partial structure + error text;
            "weak" = error text only, no structured evidence;
            "none" = no evidence.
        parser_name: Name of the parser that produced this witness.
        parser_version: Version of the parser.
        matched_patterns: List of regex / pattern names that matched.
        mode: How the witness was obtained (e.g. "post_validation_failure",
            "post_validation_output_unparseable",
            "diagnostic_only_no_failure_witness").
        source: Provenance of the failure data.
            "post_validation_output" = from raw harness/eval output;
            "none" = no raw post-validation output;
            "attempt1_runtime_artifacts" = auxiliary metadata only.
            Default "none" to prevent accidental misattribution.
        source_type: Specific sub-type of the raw source.
            One of: "harness_report", "harness_log", "per_instance_report",
            "per_instance_log", "attempt1_runtime_artifacts", "none".
        raw_output_path: Path to the raw post-validation output file
            that was parsed, if available.
        missing_reason: If has_failure_witness is False and the reason
            is not captured by mode alone, additional explanation
            (e.g. "post_validation_log_missing", "no_parseable_failure").
        oracle_labels_hidden: MUST be True.  Signals that F2P/P2P / resolved
            labels are NOT exposed in agent-facing contexts.
        version: Schema version.
    """
    instance_id: str
    has_failure_witness: bool
    failure_observed: bool = False
    failure_stage: str = "unknown_failure"
    failure_type: str = ""
    test_framework: str = "unknown"
    failed_tests: list = field(default_factory=list)
    error_message: str = ""
    stack_trace: list = field(default_factory=list)
    top_repo_frames: list = field(default_factory=list)
    expected: str | None = None
    actual: str | None = None
    validation_command: str = ""
    eligible_for_condiag: bool = False
    quality: str = "none"
    parser_name: str = ""
    parser_version: str = ""
    matched_patterns: list = field(default_factory=list)
    mode: str = "diagnostic_only_no_failure_witness"
    source: str = "none"
    source_type: str = "none"
    raw_output_path: str = ""
    missing_reason: str = ""
    oracle_labels_hidden: bool = True
    version: str = "v2.0"


@dataclass
class ApiNavigationHint:
    """A hint pointing the Host Agent toward a relevant API surface.

    Fields:
        hint_text: Natural-language description of what to look up / use.
        hint_source: MUST be one of ALLOWED_HINT_SOURCES.
        supporting_artifact: Path or reference to the evidence file.
        target_symbol: Specific function / class / method name.
        confidence: Score in [0.0, 1.0].
        generation_method: How this hint was produced (e.g. "symbol_extraction").
        version: Schema version.
    """
    hint_text: str
    hint_source: str
    supporting_artifact: str
    target_symbol: str
    confidence: float
    generation_method: str
    version: str = "v1"


def validate_api_hint_source(hint_source: str) -> bool:
    """Check that *hint_source* is allowed.

    Returns True on success.
    Raises ValueError if the source is forbidden or unknown.
    """
    if hint_source in _FORBIDDEN_HINT_SOURCES:
        raise ValueError(
            f"forbidden hint_source '{hint_source}'. "
            f"Allowed: {sorted(_ALLOWED_HINT_SOURCES)}. "
            f"Forbidden: {sorted(_FORBIDDEN_HINT_SOURCES)}."
        )
    if hint_source not in _ALLOWED_HINT_SOURCES:
        raise ValueError(
            f"unknown hint_source '{hint_source}'. "
            f"Must be one of {sorted(_ALLOWED_HINT_SOURCES)}."
        )
    return True


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
    """Output of diagnosis_normalizer.py — manual_diagnosis with derived fields.

    context_deficiency_type is the primary ConDiag diagnosis output: what type
    of context the agent was missing. This replaces pathology as the main
    diagnosis axis. pathology/5r are kept for backward compatibility.
    """
    instance_id: str = ""
    # Primary diagnosis: what context was the agent missing?
    context_deficiency_type: str = ""
    context_deficiency_secondary: list = field(default_factory=list)
    # Legacy fields (kept for backward compat)
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
