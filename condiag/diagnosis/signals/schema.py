"""Pydantic schema for structured signal extraction.

Designed from real test_log analysis (see data-first-methodology.md):
  Every field maps to a concrete extraction source.
  No field is added "because it might be useful later."

Data sources:
  - test_log: raw SWE-bench evaluation stdout (the test_output.txt file)
  - instance_spec: SWE-bench dataset row (FAIL_TO_PASS, PASS_TO_PASS, etc.)
  - trajectory: R1 agent interaction history
  - patch: R1 git diff output

Three-layer design:
  1. Raw extraction (per-source, type-validated)
  2. Aggregated bundle (Diagnoser input)
  3. Framework-specific parsers produce the same Raw* types
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from condiag.diagnosis.signals.enums import ErrorType, IterationSignal


# ════════════════════════════════════════════════════════════════════
# Layer 1: Stack frame (shared across all sources)
# ════════════════════════════════════════════════════════════════════


class StackFrame(BaseModel):
    """A single stack frame from a test failure.

    Extracted from the pytest short format:
      astropy/coordinates/baseframe.py:1202: in transform_to
    Or from the Django/pytest File format:
      File "/path/to/file.py", line 42, in func_name
    """

    file: str = Field(description="File path (repo-relative, e.g. astropy/utils/iers/iers.py)")
    line: int = Field(0, description="Line number (0 if unknown)")
    function: str = Field("", description="Function/method name")
    is_test_file: bool = Field(False, description="True if path is under a test directory")
    is_repo_frame: bool = Field(True, description="False if from site-packages or system libs")


# ════════════════════════════════════════════════════════════════════
# Layer 1: Raw extraction outputs (one per data source)
# ════════════════════════════════════════════════════════════════════


class TestLogSignals(BaseModel):
    """Signals extracted from the raw SWE-bench test_output.txt.

    Source: test_log_path → file content → regex extraction

    NOTE: These are NOT the final fused signals.
    This is the raw extraction output, organized by what part of the log it came from.
    """

    # -- Framework detection --
    framework: str = Field(
        default="unknown",
        description="Detected test framework: 'pytest' | 'django' | 'unknown'",
    )

    # -- Test results --
    failed_tests: list[str] = Field(
        default_factory=list,
        description="Tests marked FAILED in test_log; maps to FAIL_TO_PASS in dataset",
    )
    passed_tests: list[str] = Field(
        default_factory=list,
        description="Tests marked PASSED in test_log; subset of PASS_TO_PASS in dataset",
    )
    num_tests_run: int = Field(0, description="Total tests executed in this session")

    # -- Error details --
    error_types: dict[str, int] = Field(
        default_factory=dict,
        description="Count of each error type (e.g. {'TypeError': 5, 'AssertionError': 3})",
    )
    error_messages: list[str] = Field(
        default_factory=list,
        description="Full error line texts, e.g. 'TypeError: unsupported operand type(s) for -: Time and float'",
    )
    first_error_message: str = Field(
        default="",
        description="First error message encountered (used for quick preview)",
    )

    # -- Failure details --
    failure_assertions: list[str] = Field(
        default_factory=list,
        description="The '>       ' lines from pytest output — shows which source code line triggered the failure",
    )

    # -- Stack frames --
    stack_frames: list[StackFrame] = Field(
        default_factory=list,
        description="ALL stack frames from ALL failures, in order of appearance",
    )
    build_frames: list[StackFrame] = Field(
        default_factory=list,
        description="Stack frames from build/setup phase (pip install errors), NOT test failures",
    )

    # -- Per-failure call chains --
    call_chains: list[list[StackFrame]] = Field(
        default_factory=list,
        description="Each failure's call chain as an ordered list of frames; one entry per FAILED test",
    )


class InstanceSignals(BaseModel):
    """Signals from the SWE-bench dataset row (NOT from test_log).

    Source: SWE-bench dataset → instance row → structured fields
    These are available BEFORE running any agent.
    """

    instance_id: str = ""
    repo: str = ""
    base_commit: str = ""
    version: str = ""
    fail_to_pass: list[str] = Field(default_factory=list, description="Known failing tests (gold labels)")
    pass_to_pass: list[str] = Field(default_factory=list, description="Tests that must not regress")
    difficulty: str = ""  # e.g. "1-4 hours"
    has_gold_context: bool = Field(default=False, description="True if ContextBench gold_context is available")


class PatchSignals(BaseModel):
    """Signals extracted from the R1 git diff patch.

    Source: _canonical_patch() → git diff output → regex analysis
    """

    edited_files: list[str] = Field(default_factory=list, description="Files modified in the patch")
    patch_size_chars: int = Field(0, description="Total patch size in characters")
    patch_size_lines: int = Field(0, description="Number of lines changed (+/-)")
    introduced_config_change: bool = Field(
        default=False,
        description="True if patch modifies pyproject.toml, setup.cfg, or similar config files",
    )


class TrajectorySignals(BaseModel):
    """Signals extracted from the R1 agent trajectory.

    Source: R1 trajectory → message analysis

    TODO(phase-2): Enrich with exploration pattern detection, file viewing stats.
    Currently limited to what the existing trajectory_signals.py provides.
    """

    total_tool_calls: int = 0
    format_error_count: int = Field(0, description="Number of consecutive format errors")
    iteration_signal: str = Field(
        default="normal_exploring",
        description="Detected iteration behavior pattern (see IterationSignal enum values)",
    )


# ════════════════════════════════════════════════════════════════════
# Layer 2: Runtime-safe vs Oracle separation
# ════════════════════════════════════════════════════════════════════


class RuntimeInstanceSignals(BaseModel):
    """Runtime-safe instance fields — NO gold data.

    This is what the Diagnoser receives at inference time.
    """

    instance_id: str = ""
    repo: str = ""
    base_commit: str = ""
    version: str = ""


class RuntimeFailureFeatureBundle(BaseModel):
    """Diagnoser input — runtime-safe version.

    Contains only fields available at inference time.
    NO fail_to_pass, pass_to_pass, gold_context, or has_gold_context.
    """

    test_log: TestLogSignals = Field(default_factory=TestLogSignals)
    instance: RuntimeInstanceSignals = Field(default_factory=RuntimeInstanceSignals)
    patch: PatchSignals = Field(default_factory=PatchSignals)
    trajectory: TrajectorySignals = Field(default_factory=TrajectorySignals)


# ════════════════════════════════════════════════════════════════════
# Layer 3: Full bundle with Oracle fields (for offline evaluation)
# ════════════════════════════════════════════════════════════════════


class FailureFeatureBundle(BaseModel):
    """Full bundle including Oracle fields — for offline evaluation ONLY.
    NOT for Diagnoser at inference time.
    """

    test_log: TestLogSignals = Field(default_factory=TestLogSignals)
    instance: InstanceSignals = Field(default_factory=InstanceSignals)
    patch: PatchSignals = Field(default_factory=PatchSignals)
    trajectory: TrajectorySignals = Field(default_factory=TrajectorySignals)
    trajectory: TrajectorySignals = Field(default_factory=TrajectorySignals)

    def to_flat_dict(self) -> dict[str, Any]:
        """Flatten to a dict for logging/storage (no Pydantic validation)."""
        return self.model_dump()
