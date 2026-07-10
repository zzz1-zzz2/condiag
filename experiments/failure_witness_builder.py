#!/usr/bin/env python3
"""FailureWitness builder — dispatches to multi-framework parser package.

Resolution order:
1. If raw_eval_log_path exists → failure_parsers.build_failure_witness_from_log()
2. else → diagnostic_only_no_failure_witness

Internal parse helpers are replaced by the failure_parsers/ package.
Kept for backward compat: from_runtime_artifacts(), build_inventory().
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from condiag.schemas import FailureWitness

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_failure_witness(
    instance_id: str,
    run_dir: Optional[Path] = None,
    eval_log_path: Optional[Path] = None,
    method_version: str = "v2",
) -> FailureWitness:
    """Build FailureWitness for a single instance.

    Resolution order:
    1. eval_log_path exists -> dispatch to failure_parsers
    2. otherwise -> no-witness diagnostic record

    Args:
        instance_id: SWE-bench instance identifier.
        run_dir: Path to attempt_1 runtime artifacts directory (unused here).
        eval_log_path: Path to raw post-validation harness/eval output log.
        method_version: Version string for tracking.

    Returns:
        FailureWitness with provenance fields set according to actual source.
    """
    if eval_log_path and Path(eval_log_path).exists():
        return from_eval_log(instance_id, Path(eval_log_path), method_version)

    return _no_witness(instance_id, method_version,
                       missing_reason="post_validation_log_missing")


def from_eval_log(
    instance_id: str,
    log_path: Path,
    method_version: str = "v2",
) -> FailureWitness:
    """Parse failure witness using the multi-framework parser dispatch.

    Delegates to failure_parsers.build_failure_witness_from_log() which:
    1. Detects failure_stage (validation / patch_apply / dependency / timeout)
    2. Detects test_framework (pytest / go_test / ansible / generic / ...)
    3. Dispatches to the appropriate registered parser
    4. Falls back to GenericParser

    Returns:
        FailureWitness with v2 schema fields populated.
    """
    from experiments.failure_parsers.base import build_failure_witness_from_log

    raw = log_path.read_text(encoding="utf-8", errors="ignore")
    witness = build_failure_witness_from_log(
        instance_id=instance_id,
        log_text=raw,
        raw_log_path=str(log_path),
    )
    witness.version = method_version
    return witness


# ---------------------------------------------------------------------------
# Auxiliary: runtime artifacts (kept for backward compat)
# ---------------------------------------------------------------------------


def from_runtime_artifacts(
    instance_id: str,
    run_dir: Path,
    method_version: str = "v1",
) -> FailureWitness:
    """Auxiliary: extract runtime signals from attempt_1 artifacts."""
    traj_path = run_dir / "trajectory.json"
    if not traj_path.exists():
        return _no_witness(instance_id, method_version,
                           missing_reason="no_trajectory")

    try:
        traj = json.loads(traj_path.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, IOError):
        return _no_witness(instance_id, method_version,
                           missing_reason="unreadable_trajectory")

    info = traj.get("info", {})
    return FailureWitness(
        instance_id=instance_id,
        has_failure_witness=False,
        failure_type=info.get("exit_status", "unknown"),
        error_message=info.get("error_message", ""),
        mode="diagnostic_only_context",
        source="attempt_1_trajectory",
        source_type="runtime_artifact",
        raw_output_path=str(traj_path),
        version=method_version,
    )


# ---------------------------------------------------------------------------
# Inventory builder (kept for backward compat)
# ---------------------------------------------------------------------------


def build_inventory(
    csv_path: Path,
    artifact_base: Path,
    first_failed_instances: list,
) -> list[dict]:
    """Build failure witness source inventory for first-failed instances."""
    inventory = []
    for instance_id in first_failed_instances:
        record = _inventory_for_instance(instance_id, artifact_base)
        inventory.append(record)
    return inventory


def _inventory_for_instance(instance_id: str, artifact_base: Path) -> dict:
    """Search for raw validation output for a single instance."""
    post_val_log = (
        artifact_base / "post_validation_logs" / instance_id / "validation_combined.log"
    )
    if post_val_log.exists():
        return {
            "instance_id": instance_id,
            "has_raw_validation_output": True,
            "raw_output_path": str(post_val_log),
            "source_type": "post_validation_log",
            "missing_reason": "",
        }

    search_paths = [
        artifact_base / "task0_missing_base_eval" / "reports" / f"{instance_id}.json",
        artifact_base / "task0_missing_base_eval" / "reports" / f"{instance_id}.log",
        artifact_base / "task0_missing_base_eval" / "logs" / f"{instance_id}.json",
        artifact_base / "task0_missing_base_eval" / "logs" / f"{instance_id}.log",
    ]

    for p in search_paths:
        if p.exists():
            source_type = _infer_source_type(p)
            return {
                "instance_id": instance_id,
                "has_raw_validation_output": True,
                "raw_output_path": str(p),
                "source_type": source_type,
                "missing_reason": "",
            }

    return {
        "instance_id": instance_id,
        "has_raw_validation_output": False,
        "raw_output_path": "",
        "source_type": "none",
        "missing_reason": "post_validation_log_missing",
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _no_witness(
    instance_id: str,
    method_version: str = "v1",
    missing_reason: str = "post_validation_log_missing",
) -> FailureWitness:
    """Return a no-witness diagnostic-only FailureWitness."""
    return FailureWitness(
        instance_id=instance_id,
        has_failure_witness=False,
        mode="diagnostic_only_no_failure_witness",
        source="none",
        source_type="none",
        raw_output_path="",
        missing_reason=missing_reason,
        version=method_version,
    )


def resolve_artifact_path(path_str: str) -> Path:
    """Resolve artifact paths across /mnt/d, D:/, and D:\\ forms."""
    p = path_str.replace("\\", "/")
    if p.startswith("/mnt/d/"):
        return Path(p)
    if p.startswith("D:/") or p.startswith("d:/"):
        return Path("/mnt/d/" + p[3:])
    return Path(p)


# ---------------------------------------------------------------------------
# Deprecated helpers — kept for import compatibility
# ---------------------------------------------------------------------------


def _infer_source_type(path: Path) -> str:
    parent = path.parent.name.lower() if path.parent else ""
    grandparent = path.parent.parent.name.lower() if path.parent and path.parent.parent else ""
    suffix = path.suffix.lower() if path.suffix else ""

    if "post_validation_log" in parent or "post_validation_log" in grandparent:
        return "post_validation_log"
    if "report" in parent:
        return "per_instance_report"
    if suffix == ".log":
        return "harness_log"
    if suffix == ".json":
        return "harness_report"
    return "unknown"


def _collect_test_output_excerpts(
    runtime_path: Path,
    local_test_path: Path,
) -> dict:
    """Collect test output excerpts from runtime artifacts (auxiliary only)."""
    result = {
        "failed_tests": [],
        "error_message": "",
        "stack_trace": [],
        "top_repo_frames": [],
        "expected_actual": {},
    }

    if runtime_path.exists():
        try:
            data = json.loads(runtime_path.read_text(encoding="utf-8", errors="ignore"))
            samples = data.get("test_output_samples", [])
            combined_output = ""
            for sample in samples:
                excerpt = sample.get("output_excerpt", "")
                if excerpt:
                    combined_output += excerpt + "\n"
            if combined_output:
                from experiments.failure_parsers.pytest_parser import PytestParser
                pw = PytestParser.parse("dummy", combined_output)
                if pw.error_message:
                    result["error_message"] = pw.error_message
                result["stack_trace"] = pw.stack_trace
                result["top_repo_frames"] = pw.top_repo_frames
                result["failed_tests"] = pw.failed_tests
                if pw.expected and pw.actual:
                    result["expected_actual"] = {"expected": pw.expected, "actual": pw.actual}
        except (json.JSONDecodeError, IOError):
            pass

    return result
