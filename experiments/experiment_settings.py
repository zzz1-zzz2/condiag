"""
ConDiag experiment settings — single source of truth for all paths and config.

All code must import from here instead of hardcoding paths.
Env vars CONDIAG_PROJECT_ROOT and CONDIAG_ARTIFACT_ROOT can override defaults.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import ClassVar

# =====================================================================
# Roots
# =====================================================================

_PROJECT_ROOT_ENV = "CONDIAG_PROJECT_ROOT"
_ARTIFACT_ROOT_ENV = "CONDIAG_ARTIFACT_ROOT"

PROJECT_ROOT = Path(
    os.environ.get(_PROJECT_ROOT_ENV, "/home/swelite/condiag")
).resolve()

ARTIFACT_ROOT = Path(
    os.environ.get(_ARTIFACT_ROOT_ENV, "/mnt/d/condiag-artifacts/condiag")
).resolve()

# =====================================================================
# Derived paths — code
# =====================================================================

CONDIAG_PKG = PROJECT_ROOT / "condiag"
EXPERIMENTS_PKG = PROJECT_ROOT / "experiments"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DOCS_DIR = PROJECT_ROOT / "docs"
ARCHIVE_DIR = PROJECT_ROOT / "archive"

# =====================================================================
# Derived paths — artifacts
# =====================================================================

MANIFESTS_DIR = ARTIFACT_ROOT / "manifests"
INSTANCES_DIR = ARTIFACT_ROOT / "instances"
AGGREGATE_DIR = ARTIFACT_ROOT / "aggregate"
ARCHIVE_ARTIFACTS_DIR = ARTIFACT_ROOT / "archive"
LEGACY_DIR = ARTIFACT_ROOT / "v0"  # old v0 artifact tree, frozen

# =====================================================================
# Instance subdirectory layout
# =====================================================================

# <INSTANCES_DIR>/<instance_id>/attempt_1/{trajectory,patch,...}
# <INSTANCES_DIR>/<instance_id>/retries/<baseline>/{...}

ATTEMPT_1_DIRNAME = "attempt_1"
RETRIES_DIRNAME = "retries"

# Standard filenames per attempt
FN_TRAJECTORY = "trajectory.json"
FN_PATCH = "patch.diff"
FN_AGENT_INFO = "agent_info.json"
FN_RUN_LOG = "run.log"
FN_OFFICIAL_EVAL = "official_eval.json"
FN_TEST_OUTPUT = "test_output.txt"
FN_CONTEXTBENCH_METRICS = "contextbench_metrics.json"
FN_FAILURE_WITNESS = "failure_witness.json"
FN_TRAJECTORY_SIGNALS = "trajectory_signals.json"

# ConDiag-specific filenames per retry baseline
FN_SEARCH_CONTRACT = "search_contract.json"
FN_RENDERED_CONTRACT = "rendered_contract.md"
FN_COMPLIANCE = "compliance.json"

# =====================================================================
# Baselines
# =====================================================================

BASELINES_CORE: ClassVar[list[str]] = [
    "plain_rerun",
    "feedback_retry",
    "broad_expansion",
    "condiag_contract",
]

BASELINES_ABLATION: ClassVar[list[str]] = [
    "random_expansion",
    "rehydrate_only",
]

ALL_BASELINES = BASELINES_CORE + BASELINES_ABLATION

# =====================================================================
# Instance pool labels
# =====================================================================

POOL_SOLVED = "solved"
POOL_FIRST_FAILED = "first_failed"
POOL_TIMEOUT = "timeout"
POOL_PENDING = "pending"
POOL_INELIGIBLE = "ineligible"

# =====================================================================
# Benchmarks
# =====================================================================

BENCHMARK_VERIFIED = "verified"
BENCHMARK_PRO = "pro"
BENCHMARK_MULTI = "multi"
BENCHMARK_POLY = "poly"

ALL_BENCHMARKS = [BENCHMARK_VERIFIED, BENCHMARK_PRO, BENCHMARK_MULTI, BENCHMARK_POLY]

# =====================================================================
# Legacy paths (read-only reference to v0 artifact tree)
# =====================================================================

LEGACY_EVAL_PREDICTIONS = LEGACY_DIR / "eval_predictions"
LEGACY_FAILURE_WITNESS = LEGACY_DIR / "failure_witness"
LEGACY_CASE_BUNDLES = LEGACY_DIR / "case_bundles"
LEGACY_PILOT50 = LEGACY_DIR / "pilot50"

# =====================================================================
# Validation
# =====================================================================

def validate():
    """Check that critical paths exist. Raise if not."""
    checks = [
        ("CONDIAG_PKG", CONDIAG_PKG),
        ("EXPERIMENTS_PKG", EXPERIMENTS_PKG),
        ("MANIFESTS_DIR", MANIFESTS_DIR),
        ("LEGACY_DIR", LEGACY_DIR),
    ]
    for name, path in checks:
        if not path.exists():
            raise FileNotFoundError(
                f"{name}={path} does not exist. "
                f"Set {_PROJECT_ROOT_ENV} or {_ARTIFACT_ROOT_ENV} if running outside standard layout."
            )


def instance_dir(instance_id: str, *, create: bool = False) -> Path:
    """Return the canonical artifacts directory for an instance."""
    d = INSTANCES_DIR / instance_id
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def attempt_dir(instance_id: str, baseline: str | None = None, *, create: bool = False) -> Path:
    """Return the directory for attempt_1 or a retry baseline."""
    base = instance_dir(instance_id, create=create)
    if baseline is None:
        d = base / ATTEMPT_1_DIRNAME
    else:
        d = base / RETRIES_DIRNAME / baseline
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d
