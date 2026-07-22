"""Branch runner: fork from R1 snapshot, inject FW/diagnosis, run until 2nd submission."""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from minisweagent.exceptions import Submitted, LimitsExceeded, TimeExceeded, FormatError

from condiag.branch_builder import build_branch_messages
from condiag.agent.config import redact_trajectory

logger = logging.getLogger("condiag.branch")

LIMITS = {"cost_limit": 3.0, "wall_time_limit_seconds": 3600, "max_consecutive_format_errors": 3}


@dataclass
class RestoreResult:
    """Result of workspace restore operation."""
    ok: bool = False
    workspace_sha: str = ""
    reason: str = ""


@dataclass
class BranchResult:
    mode: str = ""                  # sf | condiag
    termination_reason: str = ""    # submitted | cost_limit | wall_timeout | repeated_format_error | error
    restore_result: RestoreResult = field(default_factory=RestoreResult)
    workspace_sha_before_first_step: str = ""
    patch_text: str = ""  # Cumulative workspace diff (vs base_commit)
    messages: list[dict] = field(default_factory=list)
    n_calls_total: int = 0
    n_calls_incremental: int = 0
    cost_total: float = 0.0
    cost_incremental: float = 0.0
    duration_seconds: float = 0.0
    trajectory: dict = field(default_factory=dict)
    # P0-3: Patch provenance fields
    agent_submission: Any = None  # AgentSubmission object
    final_evaluation_patch: str = ""  # What Harness actually receives


def restore_workspace(agent, snapshot, base_commit: str) -> RestoreResult:
    """Restore a WorkspaceSnapshot into the agent's container.

    5 protections:
      1. Verify container HEAD == base_commit
      2. Clean workspace (git reset --hard + git clean -fd)
      3. Apply tracked diff with --check before actual apply
      4. Validate restored workspace_state_sha
      5. Cleanup temp files in finally
    """
    temp_path = None
    try:
        cid = agent.env.container_id
        if not cid:
            return RestoreResult(ok=False, reason="no_container_id")

        # Protection 1: Clean workspace to base_commit FIRST
        # (SWE-bench images may have HEAD at a different commit; we reset to known state)
        agent.env.execute({"command": f"cd /testbed && git reset --hard {base_commit} 2>/dev/null"})
        agent.env.execute({"command": "cd /testbed && git clean -fd 2>/dev/null"})

        # Protection 2: Verify HEAD is now at base_commit
        head_r = agent.env.execute({"command": "cd /testbed && git rev-parse HEAD 2>/dev/null"})
        if head_r.get("returncode") != 0 or head_r.get("output", "").strip() != base_commit:
            return RestoreResult(ok=False, reason=f"HEAD mismatch after reset: expected {base_commit}")

        if not snapshot or (not snapshot.tracked_diff and not snapshot.untracked_manifest):
            return RestoreResult(ok=True, workspace_sha="clean_base", reason="no_diff_or_untracked")

        # Protection 3: Apply tracked diff (if any)
        temp_path = None
        if snapshot.tracked_diff:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".patch", delete=False) as f:
                f.write(snapshot.tracked_diff)
                f.flush()
                temp_path = f.name

            r1 = subprocess.run(
                ["docker", "cp", temp_path, f"{cid}:/tmp/restore.patch"],
                capture_output=True, timeout=10,
            )
            if r1.returncode != 0:
                return RestoreResult(ok=False, reason=f"docker cp failed: {r1.stderr.decode(errors='replace')[:200]}")

            r2 = agent.env.execute({"command": "cd /testbed && git apply --check /tmp/restore.patch 2>&1"})
            if r2.get("returncode") != 0:
                return RestoreResult(ok=False, reason=f"git apply --check failed")

            r3 = agent.env.execute({"command": "cd /testbed && git apply --whitespace=nowarn /tmp/restore.patch 2>&1"})
            if r3.get("returncode") != 0:
                return RestoreResult(ok=False, reason=f"git apply failed")

        # Restore untracked files if archive exists
        if snapshot.untracked_archive_path and os.path.exists(snapshot.untracked_archive_path):
            cp_r = subprocess.run(
                ["docker", "cp", snapshot.untracked_archive_path, f"{cid}:/tmp/untracked.tar"],
                capture_output=True, timeout=10,
            )
            if cp_r.returncode == 0:
                tar_r = agent.env.execute({"command": "cd /testbed && tar xf /tmp/untracked.tar 2>&1"})
                if tar_r.get("returncode") != 0:
                    return RestoreResult(ok=False, reason=f"untracked tar extract failed")

        # Protection 4: Validate workspace via unified fingerprint
        from condiag.workspace import capture_workspace_fingerprint
        restored_cr = capture_workspace_fingerprint(agent, base_commit)
        if not restored_cr.ok or restored_cr.snapshot is None:
            return RestoreResult(ok=False, reason=f"fingerprint failed after restore: {restored_cr.reason}")

        if restored_cr.snapshot.tracked_diff_sha != snapshot.tracked_diff_sha:
            return RestoreResult(
                ok=False,
                reason=f"tracked_diff SHA mismatch: {restored_cr.snapshot.tracked_diff_sha} != {snapshot.tracked_diff_sha}",
            )

        return RestoreResult(ok=True, workspace_sha=restored_cr.snapshot.workspace_state_sha)

    except Exception as e:
        return RestoreResult(ok=False, reason=f"exception: {type(e).__name__}: {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def capture_workspace_sha(agent, base_commit: str) -> str:
    """Pre-step workspace SHA via unified fingerprint function."""
    from condiag.workspace import capture_workspace_fingerprint
    cr = capture_workspace_fingerprint(agent, base_commit)
    if cr.ok and cr.snapshot is not None:
        return cr.snapshot.workspace_state_sha
    return ""


def run_branch(
    *,
    agent_factory: Callable[[], Any],
    checkpoint_messages: list[dict],
    base_commit: str,
    task: str,
    r1_n_calls: int,
    r1_cost: float,
    failure_witness: dict | None,
    diagnosis: str | None = None,
    mode: str = "sf",
    protocol_config: Any = None,
    workspace_snapshot: Any = None,  # WorkspaceSnapshot from R1
) -> BranchResult:
    """Restore R1 state, inject messages, run until 2nd submission.

    Args:
        mode: "sf" (FW only) or "condiag" (FW + diagnosis).
        protocol_config: RevisionProtocolConfig — overrides LIMITS defaults.
        workspace_snapshot: WorkspaceSnapshot to restore before first step.
                           If None, workspace restore is skipped.
    """
    # Resolve limits from protocol_config or fallback to defaults
    limits = dict(LIMITS)
    if protocol_config:
        limits["wall_time_limit_seconds"] = getattr(protocol_config, "r2_wall_time_limit_seconds", limits["wall_time_limit_seconds"])
        limits["max_consecutive_format_errors"] = getattr(protocol_config, "r2_max_consecutive_format_errors", limits["max_consecutive_format_errors"])

    agent = agent_factory()
    agent.config.step_limit = 0
    agent.n_calls = r1_n_calls
    agent.cost = r1_cost

    # Build message sequence via shared function
    agent.messages = build_branch_messages(
        checkpoint_messages, failure_witness, diagnosis=diagnosis,
        style="stateful_feedback" if mode == "sf" else "condiag",
    )

    # Restore workspace from snapshot
    t0 = time.time()
    restore = RestoreResult(ok=True, reason="no_snapshot")
    ws_before = ""
    if workspace_snapshot:
        restore = restore_workspace(agent, workspace_snapshot, base_commit)
        if not restore.ok:
            result = BranchResult(
                mode=mode,
                termination_reason=f"workspace_restore_failed:{restore.reason}",
                restore_result=restore,
                messages=list(agent.messages),
                n_calls_total=r1_n_calls,
                n_calls_incremental=0,
                cost_total=r1_cost,
                cost_incremental=0.0,
                duration_seconds=time.time() - t0,
            )
            logger.warning("[%s] Workspace restore failed: %s", mode.upper(), restore.reason)
            return result
        # Capture workspace SHA before first agent.step()
        ws_before = capture_workspace_sha(agent, base_commit)
        logger.info("[%s] Workspace restored, pre-step SHA=%s", mode.upper(), ws_before)

        # Pre-step fairness gate: verify SHA matches expected snapshot
        if not ws_before:
            return BranchResult(
                mode=mode, termination_reason="preflight_fairness_failed:empty_sha",
                restore_result=RestoreResult(ok=False, reason="preflight SHA empty"),
                messages=list(agent.messages),
                n_calls_total=r1_n_calls, n_calls_incremental=0,
                cost_total=r1_cost, cost_incremental=0.0,
                duration_seconds=time.time() - t0,
            )
        if ws_before != workspace_snapshot.workspace_state_sha:
            return BranchResult(
                mode=mode, termination_reason=f"preflight_fairness_failed:sha_mismatch",
                restore_result=RestoreResult(ok=False, reason=f"preflight SHA {ws_before} != expected {workspace_snapshot.workspace_state_sha}"),
                messages=list(agent.messages),
                n_calls_total=r1_n_calls, n_calls_incremental=0,
                cost_total=r1_cost, cost_incremental=0.0,
                duration_seconds=time.time() - t0,
            )

    reason = ""
    try:
        while True:
            if time.time() - t0 > limits["wall_time_limit_seconds"]:
                reason = "wall_timeout"; break
            try:
                agent.step()
                agent.n_consecutive_format_errors = 0
            except Exception as e:
                if isinstance(e, Submitted):
                    reason = "submitted"; agent.add_messages(*e.messages); break
                elif isinstance(e, (LimitsExceeded,)):
                    reason = "cost_limit"; agent.add_messages(*e.messages); break
                elif isinstance(e, TimeExceeded):
                    reason = "wall_timeout"; agent.add_messages(*e.messages); break
                elif isinstance(e, FormatError):
                    agent.n_consecutive_format_errors += 1
                    agent.add_messages(*e.messages)
                    if agent.n_consecutive_format_errors >= limits["max_consecutive_format_errors"]:
                        reason = "repeated_format_error"; break
                    continue
                else:
                    agent.handle_uncaught_exception(e)
                    reason = f"error:{type(e).__name__}"; break
    finally:
        # P0-3: Collect AgentSubmission and final_evaluation_patch
        from condiag.patch_artifacts import (
            collect_agent_submission, canonicalize_patch,
        )
        workspace_diff = _canonical_patch(agent, base_commit)
        br_sub = collect_agent_submission(agent_messages=list(agent.messages))
        if br_sub.selected_patch.strip():
            final_eval = canonicalize_patch(br_sub.selected_patch)
        else:
            final_eval = canonicalize_patch(workspace_diff)
            br_sub.selected_source = "workspace_diff_fallback"
            br_sub.selected_patch = final_eval
            br_sub.consistency_status = "fallback_used"

        result = BranchResult(
            mode=mode,
            termination_reason=reason,
            restore_result=restore,
            workspace_sha_before_first_step=ws_before,
            patch_text=workspace_diff,
            messages=list(agent.messages),
            n_calls_total=agent.n_calls,
            n_calls_incremental=agent.n_calls - r1_n_calls,
            cost_total=agent.cost,
            cost_incremental=agent.cost - r1_cost,
            duration_seconds=time.time() - t0,
            trajectory=redact_trajectory(agent.serialize()) if hasattr(agent, "serialize") else {},
            agent_submission=br_sub,
            final_evaluation_patch=final_eval,
        )

    logger.info("[%s] reason=%s incr_calls=%d total_calls=%d patch=%dch",
                 mode.upper(), reason, result.n_calls_incremental,
                 result.n_calls_total, len(result.patch_text))
    return result


def _apply_patch(agent, patch_text: str):
    if not patch_text:
        return
    try:
        subprocess.run(
            ["docker", "cp", "-", f"{agent.env.container_id}:/tmp/restore.diff"],
            input=patch_text, text=True, timeout=10, capture_output=True,
        )
        agent.env.execute({"command": "cd /testbed && git apply --whitespace=nowarn /tmp/restore.diff 2>&1"})
    except Exception as e:
        logger.warning("Patch restore: %s", e)


def _canonical_patch(agent, base_commit: str = "") -> str:
    try:
        b = base_commit or "HEAD"
        agent.env.execute({"command": "cd /testbed && git add -N . 2>/dev/null; true"})
        r = agent.env.execute({"command": f"cd /testbed && git diff --binary {b} 2>/dev/null"})
        agent.env.execute({"command": "cd /testbed && git reset -N . 2>/dev/null; true"})
        return r.get("output", "")
    except Exception:
        return ""
