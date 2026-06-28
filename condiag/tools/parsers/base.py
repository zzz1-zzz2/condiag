"""Parser interface for trajectory formats.

Each concrete parser (miniswe, agentless, ...) implements `parse(traj_path)`
returning a `ParsedTrajectory` containing all runtime-visible facts.

Strict policy:
- Parsers extract ONLY runtime-visible facts (what the agent viewed, searched,
  edited, ran as tests, and what the test outputs said).
- Parsers MUST NOT read or copy gold/oracle/official-eval fields
  (file_cov, symbol_cov, line_cov, EditLoc, FAIL_TO_PASS, resolved label, ...).
  Those belong in `contextbench_metrics.json` / `official_eval.json` /
  `gold_check`, which are produced by separate evaluation tooling.
- Parsers MUST NOT classify pathology or 5R action. That is the job of
  trigger.py / scope_guard.py / find_relocalize_candidates.py / etc.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ParsedTrajectory:
    """Runtime-visible facts extracted from an agent trajectory.

    Field naming and shape match `condiag.runtime_signals.v0.1`.
    """

    # Identity
    schema_version: str = "condiag.runtime_signals.v0.1"
    instance_id: str = ""
    agent: str = ""

    # Top-level status
    exit_status: str = ""
    n_messages: int = 0
    n_assistant_messages: int = 0
    n_user_messages: int = 0
    api_calls: int = 0

    # What the agent viewed (from <EXPLORE_CONTEXT> blocks, fallback to bash views)
    viewed_files_in_order: list[str] = field(default_factory=list)
    viewed_files_count: int = 0
    viewed_spans: dict[str, list[list[int]]] = field(default_factory=dict)
    viewed_total_line_bytes: int = 0

    # Bash commands
    bash_commands_count: int = 0
    search_commands: list[str] = field(default_factory=list)
    search_commands_count: int = 0

    # Tests the agent ran
    test_commands: list[dict] = field(default_factory=list)
    test_runs: list[dict] = field(default_factory=list)
    test_runs_count: int = 0
    test_output_samples: list[dict] = field(default_factory=list)
    test_failures: list[str] = field(default_factory=list)
    test_failures_count: int = 0
    possible_regression_failures: list[str] = field(default_factory=list)

    # Submit-time patch context declared by the agent
    patch_context_files: list[dict] = field(default_factory=list)
    patch_context_files_count: int = 0

    # Final patch context (last <PATCH_CONTEXT> declaration)
    final_patch_context_files: list[dict] = field(default_factory=list)
    final_patch_context_files_count: int = 0

    # Patch itself (from info.submission)
    edited_files: list[str] = field(default_factory=list)
    edited_files_count: int = 0
    edited_hunks_total: int = 0
    edited_spans_per_file: dict[str, list[int]] = field(default_factory=dict)
    changed_files: list[str] = field(default_factory=list)
    changed_files_count: int = 0
    changed_lines_total: int = 0
    changed_lines_added: int = 0
    changed_lines_removed: int = 0

    # Shape signals
    repeated_edit_patterns: list[dict] = field(default_factory=list)
    repeated_edit_pattern_detected: bool = False
    submitted_without_tests: bool = False
    git_checkout_count: int = 0

    # Derived: viewed-but-dropped / edited-but-not-viewed
    viewed_but_not_final_files: list[str] = field(default_factory=list)
    viewed_but_not_final_files_count: int = 0
    edited_but_not_viewed_files: list[str] = field(default_factory=list)
    edited_but_not_viewed_files_count: int = 0

    # Misc
    last_user_messages_tail: list[str] = field(default_factory=list)

    # Reserved for future use — fixed position so the schema stays stable
    # across cases even when a particular parser version doesn't populate them.
    stack_trace: list[dict] = field(default_factory=list)
    error_tokens: list[str] = field(default_factory=list)
    error_origin_candidates: list[str] = field(default_factory=list)

    # Quality / provenance
    quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-ready dict with stable key ordering."""
        return {
            "schema_version": self.schema_version,
            "instance_id": self.instance_id,
            "agent": self.agent,
            "exit_status": self.exit_status,
            "n_messages": self.n_messages,
            "n_assistant_messages": self.n_assistant_messages,
            "n_user_messages": self.n_user_messages,
            "api_calls": self.api_calls,
            "viewed_files_in_order": self.viewed_files_in_order,
            "viewed_files_count": self.viewed_files_count,
            "viewed_spans": self.viewed_spans,
            "viewed_total_line_bytes": self.viewed_total_line_bytes,
            "bash_commands_count": self.bash_commands_count,
            "search_commands": self.search_commands,
            "search_commands_count": self.search_commands_count,
            "test_commands": self.test_commands,
            "test_runs": self.test_runs,
            "test_runs_count": self.test_runs_count,
            "test_output_samples": self.test_output_samples,
            "test_failures": self.test_failures,
            "test_failures_count": self.test_failures_count,
            "possible_regression_failures": self.possible_regression_failures,
            "patch_context_files": self.patch_context_files,
            "patch_context_files_count": self.patch_context_files_count,
            "final_patch_context_files": self.final_patch_context_files,
            "final_patch_context_files_count": self.final_patch_context_files_count,
            "edited_files": self.edited_files,
            "edited_files_count": self.edited_files_count,
            "edited_hunks_total": self.edited_hunks_total,
            "edited_spans_per_file": self.edited_spans_per_file,
            "changed_files": self.changed_files,
            "changed_files_count": self.changed_files_count,
            "changed_lines_total": self.changed_lines_total,
            "changed_lines_added": self.changed_lines_added,
            "changed_lines_removed": self.changed_lines_removed,
            "repeated_edit_patterns": self.repeated_edit_patterns,
            "repeated_edit_pattern_detected": self.repeated_edit_pattern_detected,
            "submitted_without_tests": self.submitted_without_tests,
            "git_checkout_count": self.git_checkout_count,
            "viewed_but_not_final_files": self.viewed_but_not_final_files,
            "viewed_but_not_final_files_count": self.viewed_but_not_final_files_count,
            "edited_but_not_viewed_files": self.edited_but_not_viewed_files,
            "edited_but_not_viewed_files_count": self.edited_but_not_viewed_files_count,
            "last_user_messages_tail": self.last_user_messages_tail,
            "stack_trace": self.stack_trace,
            "error_tokens": self.error_tokens,
            "error_origin_candidates": self.error_origin_candidates,
            "quality": self.quality,
        }


class TrajectoryParser:
    """Interface contract. Subclasses override `parse`."""

    name: str = "base"

    def parse(self, traj_path: Path) -> ParsedTrajectory:
        raise NotImplementedError
