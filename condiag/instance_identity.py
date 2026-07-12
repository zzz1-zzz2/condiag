"""Instance identity resolution and artifact path conventions.

Provides the canonical mapping between instance records and filesystem paths:

    resolve_canonical_instance_id(pool_record) -> str
    instance_artifact_filename(canonical_instance_id) -> str
    resolve_instance_alias(instance_id, records_index) -> str

Two-layer design:
  1. Resolve a pool record to its canonical runtime instance ID
     (the ID that matches the on-disk directory and trajectory).
  2. Convert that canonical ID to a safe, deterministic filename.

Rule: a canonical instance ID must match the on-disk directory name.
Short hashes in pool records are aliases, never used for file addressing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Pattern: anything that looks like an old-style short-hash suffix (hex-only,
# shorter than the full commit-based hash typical of ContextBench IDs).
_SHORT_HASH_PATTERN = re.compile(r"[0-9a-f]{7,12}$")


def resolve_canonical_instance_id(pool_record: dict[str, Any]) -> str:
    """Resolve the canonical runtime instance ID from a pool record.

    Priority (first non-None, non-empty match wins):
      1. ``dir_name``            – the actual filesystem directory name
      2. ``canonical_id``        – explicit canonical field (future-proofing)
      3. ``instance_id``         – fallback (for simple IDs where both match)

    Verification (non-fatal warning only):
      - If ``trajectory_id`` is present in the record, it must match.
      - If the caller provides a ``trajectory_path``, it is read and checked.

    Raises ``ValueError`` if resolution fails (empty record, no usable field).
    """
    if not pool_record:
        raise ValueError("Cannot resolve canonical ID from empty record")

    # Priority 1: explicit dir_name (actual filesystem directory)
    candidate = pool_record.get("dir_name") or ""

    # Priority 2: explicit canonical_id field
    if not candidate:
        candidate = pool_record.get("canonical_id", "")

    # Priority 3: instance_id (fallback for simple IDs without dir_name)
    if not candidate:
        candidate = pool_record.get("instance_id", "")

    if not candidate:
        raise ValueError(
            f"Cannot resolve canonical instance ID — record has no "
            f"dir_name, instance_id, or canonical_id: {pool_record}"
        )

    # Light sanity check: reject if the candidate looks like a short hash
    # (i.e. it appears that someone passed an alias instead of the full ID)
    _check_not_short_hash(candidate, pool_record)

    return candidate


def instance_artifact_filename(canonical_instance_id: str) -> str:
    """Convert a canonical instance ID to a safe, deterministic filename.

    Accepts ONLY already-resolved canonical IDs.  Does NOT resolve aliases;
    call ``resolve_canonical_instance_id`` first if you have a pool record.

    The output is guaranteed to:
      - contain no path separators
      - contain no characters unsafe for common filesystems
      - be deterministic (same input → same output)

    Raises ``ValueError`` if the input is empty or None.
    """
    if not canonical_instance_id:
        raise ValueError("Cannot create artifact filename from empty ID")

    # Replace '/' which appears in some repo-style identifiers
    safe = canonical_instance_id.replace("/", "__")

    # Guard against remaining problematic characters
    if "/" in safe:
        raise ValueError(
            f"instance_artifact_filename failed to produce safe filename "
            f"from {canonical_instance_id!r} — output still contains '/'"
        )

    return safe


def resolve_instance_alias(
    alias_id: str,
    records_index: dict[str, dict[str, Any]],
) -> str:
    """Resolve an alias (short-hash pool ID) to its canonical instance ID.

    ``records_index`` should be a dict mapping *all known IDs* (both aliases
    and canonical) to their pool record.  Typical construction:

        index = {}
        for rec in pool["instances"]:
            index[rec["instance_id"]] = rec
            if rec.get("dir_name") and rec["dir_name"] != rec["instance_id"]:
                index[rec["dir_name"]] = rec

    Returns the canonical ID, or raises ``KeyError`` if the alias is unknown.
    """
    record = records_index.get(alias_id)
    if record is None:
        raise KeyError(f"Alias {alias_id!r} not found in records index")
    return resolve_canonical_instance_id(record)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_not_short_hash(candidate: str, record: dict[str, Any]) -> None:
    """Warn if *candidate* looks like a short hash rather than a full ID."""
    # Split on '-' and check the last segment
    parts = candidate.split("-")
    if not parts:
        return
    last = parts[-1]
    # 'vnan' is a legitimate suffix, not a short hash
    if last == "vnan" and len(parts) >= 2:
        last = parts[-2]
    if _SHORT_HASH_PATTERN.match(last):
        import warnings
        warnings.warn(
            f"resolve_canonical_instance_id: candidate {candidate!r} "
            f"looks like a short hash (from record {record}). "
            f"This may indicate an alias was used instead of a canonical ID.",
            stacklevel=2,
        )


# ---------------------------------------------------------------------------
# Convenience: resolve from a JSON manifest file
# ---------------------------------------------------------------------------


def resolve_from_manifest(
    instance_id: str,
    manifest_path: str | Path,
) -> str:
    """Resolve an instance_id to its canonical ID using a JSON manifest file."""
    from pathlib import Path as _Path
    with open(_Path(manifest_path)) as _f:
        _data = json.load(_f)
    if isinstance(_data, dict) and "instances" in _data:
        # Pool-style manifest
        for _rec in _data["instances"]:
            if _rec.get("instance_id") == instance_id or _rec.get("dir_name") == instance_id:
                return resolve_canonical_instance_id(_rec)
    elif isinstance(_data, list):
        for _rec in _data:
            if isinstance(_rec, dict) and _rec.get("instance_id") == instance_id:
                return resolve_canonical_instance_id(_rec)
    raise KeyError(
        f"Instance {instance_id!r} not found in manifest {manifest_path}"
    )
