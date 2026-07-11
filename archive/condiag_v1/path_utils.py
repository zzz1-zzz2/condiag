"""Path utilities for ConDiag — centralize SWE-bench and host path conventions.

No hardcoded host paths in core logic — use these helpers instead.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# SWE-bench container conventions
# ---------------------------------------------------------------------------
# Inside a SWE-bench eval container, the target repository is checked out
# at /testbed/.  All repo-relative file paths in trajectories, stack traces,
# and runtime signals originate from this prefix.
_TESTBED_PREFIX = "/testbed/"


def is_test_file(file_path: str) -> bool:
    """Check whether *file_path* (from a SWE-bench trajectory or traceback)
    refers to a test file (inside the tests/ directory).

    This is the canonical check — avoids scattering '/testbed/tests/' patterns
    across the codebase.
    """
    return ("/testbed/tests/" in file_path
            or file_path.startswith("tests/")
            or "/tests/" in file_path)


def strip_testbed(file_path: str) -> str:
    """Strip the /testbed/ prefix from a SWE-bench container path.

    Returns the repo-relative path.  If the path doesn't start with /testbed/,
    returns it unchanged.
    """
    if file_path.startswith(_TESTBED_PREFIX):
        return file_path[len(_TESTBED_PREFIX):]
    return file_path.lstrip("/")


# ---------------------------------------------------------------------------
# Host path layout (ConDiag artifacts + repo cache)
# ---------------------------------------------------------------------------
# These are set once from env / layout conventions and used everywhere else.
# Only this module knows about /mnt/d/condiag-artifacts/.
# ---------------------------------------------------------------------------

# Override via CONDIAG_ARTIFACTS env var (set by env.sh)
_CONDIAG_ARTIFACTS = Path("/mnt/d/condiag-artifacts")

# Repo cache — bare / full clones used by retrieval_executor
_REPO_CACHE: Optional[Path] = None


def get_artifacts_root() -> Path:
    """Return the ConDiag artifacts root directory."""
    import os
    env = os.environ.get("CONDIAG_ARTIFACTS")
    if env:
        return Path(env)
    return _CONDIAG_ARTIFACTS


def get_repo_cache_root() -> Path:
    """Return the repo cache root directory (used by retrieval_executor)."""
    import os
    global _REPO_CACHE
    if _REPO_CACHE is not None:
        return _REPO_CACHE
    env = os.environ.get("CONDIAG_REPO_CACHE")
    if env:
        _REPO_CACHE = Path(env)
    else:
        _REPO_CACHE = get_artifacts_root() / "cache" / "repos"
    return _REPO_CACHE


def resolve_repo_path(repo_name: str) -> Optional[Path]:
    """Resolve a repo name (e.g. 'django__django') to its local cache path.

    Checks the repo cache for a matching directory.  Returns None if not found.
    """
    root = get_repo_cache_root()
    # Try exact name first, then "github.com__<name>" pattern (used by
    # repository_index).
    for candidate in [root / repo_name, root / f"github.com__{repo_name}"]:
        if candidate.is_dir():
            return candidate
    # Broader scan: match by suffix (e.g. "django" in "github.com__django__django")
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir() and repo_name.replace("_", "").replace("-", "") in d.name.replace("_", "").replace("-", ""):
                return d
    return None
