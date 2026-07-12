"""Execution policy: unified frozen-eligible override for paired retries.

Centralizes the Task 17 paired-pilot execution policy so that both
feedback_retry and condiag_contract_retry use the SAME eligibility
verification and override logic.

Key design:
  - The frozen pool manifest (condiag_dev_pool.json) is the SINGLE source
    of truth for eligibility.  The caller cannot bypass pool membership.
  - Raw trigger output is NEVER modified — it is preserved verbatim as
    metadata alongside the effective decision.
  - If the instance is NOT in the frozen pool but the caller requested
    override, the policy FAILS CLOSED (does NOT force retry).
  - All override metadata is returned as a flat dict suitable for merging
    into intervention_report.json.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


# Default frozen pool path (Dev-10)
_DEFAULT_FROZEN_POOL = Path(
    "/mnt/d/condiag-artifacts/condiag/pool/condiag_dev_pool.json"
)


@dataclass
class ExecutionPolicyDecision:
    """Result of resolve_retry_execution_policy()."""

    # --- Original trigger results (preserved verbatim) ---
    raw_trigger_type: str
    raw_should_retry: bool

    # --- Effective decision ---
    effective_should_retry: bool

    # --- Override metadata ---
    frozen_eligible_override_requested: bool = False
    frozen_eligible_override_applied: bool = False
    override_reason: Optional[str] = None
    eligibility_source: Optional[str] = None
    eligibility_manifest_hash: Optional[str] = None

    # --- Pool membership details ---
    pool_instance_id: Optional[str] = None
    pool_canonical_id: Optional[str] = None
    pool_canonical_match: bool = False
    pool_eligible: bool = False

    # --- Fail-closed diagnostics ---
    fail_closed_reason: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_frozen_pool(pool_path: Path) -> dict[str, Any]:
    """Load and return the frozen pool manifest."""
    with open(pool_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _manifest_hash(pool_data: dict[str, Any]) -> str:
    """Compute a stable hash of the pool manifest for audit."""
    canonical = json.dumps(pool_data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def resolve_retry_execution_policy(
    instance_id: str,
    raw_should_retry: bool,
    raw_trigger_type: str,
    override_requested: bool = False,
    frozen_pool_path: Optional[Path] = None,
) -> ExecutionPolicyDecision:
    """Unified execution policy: resolve effective retry decision.

    Args:
        instance_id: Canonical runtime instance ID.
        raw_should_retry: Original trigger result (NOT modified).
        raw_trigger_type: Original trigger type (NOT modified).
        override_requested: Whether the caller REQUESTED paired execution.
        frozen_pool_path: Path to frozen pool manifest JSON.

    Returns:
        ExecutionPolicyDecision with both raw and effective fields.
    """
    if frozen_pool_path is None:
        frozen_pool_path = _DEFAULT_FROZEN_POOL

    # Always load the pool and verify membership
    pool_data = _load_frozen_pool(frozen_pool_path)
    manifest_hash = _manifest_hash(pool_data)

    # Build lookup index from pool instances
    pool_instances = pool_data.get("instances", [])
    task17_config = pool_data.get("task17", {})
    eligible_ids = set(task17_config.get("eligible_instance_ids", []))
    canonical_map = task17_config.get("canonical_map", {})

    # Check if instance_id is in the frozen pool
    pool_entry = None
    pool_canonical_id = None
    pool_instance_id = None
    pool_eligible = False
    pool_canonical_match = False

    for inst in pool_instances:
        cid = inst.get("dir_name") or inst["instance_id"]
        if cid == instance_id:
            pool_entry = inst
            pool_canonical_id = cid
            pool_instance_id = inst["instance_id"]
            pool_eligible = inst.get("task17_eligible", False)
            pool_canonical_match = (cid == instance_id)
            break

    # Check if instance_id is in the canonical map (alias resolution)
    if pool_entry is None and instance_id in canonical_map:
        canonical_id = canonical_map[instance_id]
        for inst in pool_instances:
            cid = inst.get("dir_name") or inst["instance_id"]
            if cid == canonical_id:
                pool_entry = inst
                pool_canonical_id = cid
                pool_instance_id = inst["instance_id"]
                pool_eligible = inst.get("task17_eligible", False)
                pool_canonical_match = False  # alias, not canonical
                break

    # Determine if override should be applied
    in_frozen_pool = pool_entry is not None
    eligible = in_frozen_pool and pool_eligible and instance_id in eligible_ids

    # Default: use raw decision
    effective_should_retry = raw_should_retry
    override_applied = False
    override_reason = None
    fail_closed_reason = None

    if override_requested:
        if not in_frozen_pool:
            # FAIL CLOSED: instance not in frozen pool
            fail_closed_reason = (
                f"instance_id={instance_id!r} not found in frozen pool "
                f"at {frozen_pool_path}"
            )
        elif not eligible:
            # FAIL CLOSED: instance in pool but not eligible
            fail_closed_reason = (
                f"instance_id={instance_id!r} found in frozen pool but "
                f"task17_eligible={pool_eligible}, in eligible_ids={instance_id in eligible_ids}"
            )
        elif not raw_should_retry:
            # Apply override: instance IS frozen-eligible but trigger said NO
            effective_should_retry = True
            override_applied = True
            override_reason = "frozen_eligible_paired_experiment"
        # else: raw_should_retry is already True, no override needed

    return ExecutionPolicyDecision(
        raw_trigger_type=raw_trigger_type,
        raw_should_retry=raw_should_retry,
        effective_should_retry=effective_should_retry,
        frozen_eligible_override_requested=override_requested,
        frozen_eligible_override_applied=override_applied,
        override_reason=override_reason,
        eligibility_source=str(frozen_pool_path),
        eligibility_manifest_hash=manifest_hash,
        pool_instance_id=pool_instance_id,
        pool_canonical_id=pool_canonical_id,
        pool_canonical_match=pool_canonical_match,
        pool_eligible=pool_eligible,
        fail_closed_reason=fail_closed_reason,
    )
