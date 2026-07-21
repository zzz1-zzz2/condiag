"""Build a RuntimeFailureFeatureBundle from available episode data.

This is the standard entry point for constructing the Diagnoser's input.
It collects data from all available sources and produces a validated bundle.

Phase 1 scope: extract ALL fields already present in the data pipeline.
No complex inference, no gold data.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from condiag.diagnosis.signals.schema import (
    FailureFeatureBundle,
    InstanceSignals,
    PatchSignals,
    RuntimeInstanceSignals,
    RuntimeFailureFeatureBundle,
    TestLogSignals,
    TrajectorySignals,
)

logger = logging.getLogger("condiag.diagnosis.bundle_builder")


def build_failure_feature_bundle(
    failure_witness: dict | None = None,
    evaluation_patch: str = "",
    workspace_patch: str = "",
    trajectory: dict | None = None,
    instance_spec: dict | None = None,
    test_log: TestLogSignals | None = None,
) -> RuntimeFailureFeatureBundle:
    """Construct a RuntimeFailureFeatureBundle from all available episode data.

    All parameters are optional -- the bundle will be populated with whatever
    is available. Missing fields remain at their defaults.

    Returns:
        RuntimeFailureFeatureBundle ready for DiagnoserCore.diagnose().
    """
    from condiag.diagnosis.signals.schema import StackFrame

    bundle = RuntimeFailureFeatureBundle()

    # ── Test log signals ─────────────────────────────────
    if test_log is not None:
        bundle.test_log = test_log
    elif failure_witness:
        _populate_test_log_from_fw(bundle.test_log, failure_witness)

    # ── Patch signals ────────────────────────────────────
    _populate_patch_signals(bundle.patch, evaluation_patch, workspace_patch)

    # ── Trajectory signals ───────────────────────────────
    if trajectory:
        _populate_trajectory_signals(bundle.trajectory, trajectory)

    # ── Instance signals (runtime-safe) ───────────────────
    if instance_spec:
        _populate_instance_signals(bundle.instance, instance_spec)

    return bundle


# ─── Internal: populate sub-signals from raw data ────────────


def _populate_test_log_from_fw(
    signals: TestLogSignals, fw: dict
) -> None:
    """Fill TestLogSignals from FailureWitness dict (no detailed parsing)."""
    failed = fw.get("failed_tests") or []
    signals.failed_tests = list(failed)

    error_msg = fw.get("error_message") or ""
    signals.first_error_message = error_msg
    if error_msg:
        signals.error_messages = [error_msg]

    # Count error types by prefix
    for test in failed:
        if "AssertionError" in str(test):
            signals.error_types["AssertionError"] = signals.error_types.get("AssertionError", 0) + 1
    if "TypeError" in error_msg:
        signals.error_types["TypeError"] = signals.error_types.get("TypeError", 0) + 1
    if "AttributeError" in error_msg:
        signals.error_types["AttributeError"] = signals.error_types.get("AttributeError", 0) + 1

    frames = fw.get("stack_frames") or []
    from condiag.diagnosis.signals.schema import StackFrame
    for frame in frames[:15]:
        if isinstance(frame, dict):
            signals.stack_frames.append(
                StackFrame(
                    file=frame.get("file", ""),
                    line=frame.get("line", 0),
                    function=frame.get("function", frame.get("func", "")),
                    is_repo_frame="/testbed" not in frame.get("file", ""),
                )
            )


def _populate_patch_signals(
    signals: PatchSignals,
    evaluation_patch: str,
    workspace_patch: str,
) -> None:
    """Extract patch-level features from diff text."""
    import re

    signals.patch_size_chars = len(evaluation_patch) if evaluation_patch else len(workspace_patch) if workspace_patch else 0
    signals.patch_size_lines = evaluation_patch.count("\n") if evaluation_patch else workspace_patch.count("\n") if workspace_patch else 0

    # Extract edited files
    patch_text = evaluation_patch or workspace_patch
    for m in re.finditer(r"^diff --git a/(\S+) b/(\S+)", patch_text, re.MULTILINE):
        signals.edited_files.append(m.group(2))

    # Config file changes
    for f in signals.edited_files:
        if any(f.endswith(ext) for ext in ["pyproject.toml", "setup.cfg", "setup.py", "tox.ini", ".circleci"]):
            signals.introduced_config_change = True
            break


def _populate_trajectory_signals(
    signals: TrajectorySignals,
    trajectory: dict,
) -> None:
    """Parse basic trajectory features."""
    total_tool_calls = 0
    for msg in trajectory.get("messages", []) if isinstance(trajectory, dict) else trajectory:
        role = msg.get("role", "")
        if role == "assistant":
            total_tool_calls += 1

    signals.total_tool_calls = total_tool_calls
    signals.format_error_count = 0  # detailed extraction deferred to Phase 2


def _populate_instance_signals(
    signals: RuntimeInstanceSignals,
    instance_spec,
) -> None:
    """Extract runtime-safe instance fields."""
    if hasattr(instance_spec, "instance_id"):
        signals.instance_id = instance_spec.instance_id
    if hasattr(instance_spec, "repo"):
        signals.repo = instance_spec.repo
    if hasattr(instance_spec, "base_commit"):
        signals.base_commit = instance_spec.base_commit
    if hasattr(instance_spec, "version"):
        signals.version = instance_spec.version
