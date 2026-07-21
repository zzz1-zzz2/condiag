"""Build a RuntimeFailureFeatureBundle from available episode data.

Standard entry point for the Diagnoser's input.
Multi-source fusion: parsed test log + FailureWitness → merged TestLogSignals.
"""
from __future__ import annotations

import logging
from typing import Any

from condiag.diagnosis.signals.frame_normalizer import normalize_frame
from condiag.diagnosis.signals.schema import (
    PatchSignals,
    RuntimeFailureFeatureBundle,
    RuntimeInstanceSignals,
    TestLogSignals,
    TrajectorySignals,
)

logger = logging.getLogger("condiag.diagnosis.bundle_builder")


def build_failure_feature_bundle(
    failure_witness: dict | None = None,
    evaluation_patch: str = "",
    workspace_patch: str = "",
    trajectory: dict | None = None,
    instance_spec: Any = None,
    test_log: TestLogSignals | None = None,
) -> RuntimeFailureFeatureBundle:
    """Construct a RuntimeFailureFeatureBundle from all available episode data.

    Multi-source fusion: parsed test_log (if available) is the primary source,
    FailureWitness fills in gaps and deduplicates.

    Returns:
        RuntimeFailureFeatureBundle ready for DiagnoserCore.diagnose().
    """
    bundle = RuntimeFailureFeatureBundle()

    # ── Test log signals: fusion of parsed + FW ──────────────
    _merge_test_log_signals(bundle.test_log, test_log, failure_witness)

    # ── Patch signals ────────────────────────────────────────
    patch_text = evaluation_patch or workspace_patch or ""
    if patch_text.strip():
        from condiag.diagnosis.signals.patch_extractor import extract_patch_signals
        bundle.patch = extract_patch_signals(patch_text)

    # ── Trajectory signals ───────────────────────────────────
    if trajectory:
        from condiag.diagnosis.signals.trajectory_extractor import extract_trajectory_signals
        bundle.trajectory = extract_trajectory_signals(trajectory)

    # ── Instance signals (runtime-safe) ──────────────────────
    if instance_spec:
        _populate_instance_signals(bundle.instance, instance_spec)

    return bundle


# ─── Fusion: parsed test log + FailureWitness ──────────────────────


def _merge_test_log_signals(
    target: TestLogSignals,
    parsed: TestLogSignals | None,
    fw: dict | None,
) -> None:
    """Merge parsed test log signals with FailureWitness data.

    Rules:
      - parsed is the primary source (more detailed)
      - FW fills in gaps where parsed is empty
      - Stack frames are deduplicated by (file, line, function)
    """
    if parsed is not None:
        target.framework = parsed.framework
        target.failed_tests = list(parsed.failed_tests)
        target.passed_tests = list(parsed.passed_tests)
        target.num_tests_run = parsed.num_tests_run
        target.error_types = dict(parsed.error_types)
        target.error_messages = list(parsed.error_messages)
        target.first_error_message = parsed.first_error_message
        target.failure_assertions = list(parsed.failure_assertions)
        target.call_chains = list(parsed.call_chains)

    # FW as fallback/supplement
    if fw is None:
        return

    fw_failed = fw.get("failed_tests") or []
    fw_error = fw.get("error_message") or ""
    fw_frames = fw.get("stack_frames") or []

    # Fill failed tests
    if not target.failed_tests and fw_failed:
        target.failed_tests = list(fw_failed)

    # Fill error messages
    if fw_error:
        if not target.first_error_message:
            target.first_error_message = fw_error
        if fw_error not in target.error_messages:
            target.error_messages.append(fw_error)

        for etype_name in ("TypeError", "AssertionError", "AttributeError", "ValueError", "ImportError"):
            if etype_name in fw_error:
                target.error_types[etype_name] = target.error_types.get(etype_name, 0) + 1

    # Fill stack frames (deduplicated)
    existing_keys = {(f.file, f.line, f.function) for f in target.stack_frames}
    for raw_frame in fw_frames:
        if not isinstance(raw_frame, dict):
            continue
        path = raw_frame.get("file", "") or ""
        func = raw_frame.get("function", raw_frame.get("func", "")) or ""
        line = raw_frame.get("line", 0)
        key = (path, line, func)
        if key not in existing_keys:
            existing_keys.add(key)
            target.stack_frames.append(normalize_frame(path, line, func))


# ─── Instance signals ──────────────────────────────────────────────


def _populate_instance_signals(
    signals: RuntimeInstanceSignals,
    instance_spec,
) -> None:
    """Extract runtime-safe instance fields (supports both dict and object)."""
    def _get(key, default=""):
        if isinstance(instance_spec, dict):
            return instance_spec.get(key, default)
        return getattr(instance_spec, key, default)

    signals.instance_id = _get("instance_id")
    signals.repo = _get("repo")
    signals.base_commit = _get("base_commit")
    signals.version = _get("version")
