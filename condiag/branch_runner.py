"""Branch runner: fork from R1 snapshot, inject FW/diagnosis, run until 2nd submission."""
from __future__ import annotations

import json
import logging
import os
import shlex
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
    """Result of workspace restore operation.

    blocking fields:
      - ok, reason, workspace_sha: gate the next agent step
    audit fields (filled even on success):
      - untracked_manifest_count, untracked_manifest_sha,
        untracked_archive_expected, untracked_archive_present,
        untracked_archive_extracted, untracked_restore_status
    """

    ok: bool = False
    workspace_sha: str = ""
    reason: str = ""
    # Audit-only fields (untracked restore state)
    untracked_manifest_count: int = 0
    untracked_manifest_sha: str = ""
    untracked_archive_expected: bool = False
    untracked_archive_present: bool = False
    untracked_archive_extracted: bool = False
    untracked_restore_status: str = "skipped"  # ok | failed | skipped | not_applicable
    base_commit: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "workspace_sha": self.workspace_sha,
            "reason": self.reason,
            "untracked_manifest_count": self.untracked_manifest_count,
            "untracked_manifest_sha": self.untracked_manifest_sha,
            "untracked_archive_expected": self.untracked_archive_expected,
            "untracked_archive_present": self.untracked_archive_present,
            "untracked_archive_extracted": self.untracked_archive_extracted,
            "untracked_restore_status": self.untracked_restore_status,
            "base_commit": self.base_commit,
        }


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


def restore_workspace(agent, snapshot, base_commit: str,
                      fairness_debug_dir: str | None = None) -> RestoreResult:
    """Restore a WorkspaceSnapshot into the agent's container.

    5 protections:
      1. Clean workspace (git reset --hard base_commit, then git clean -fd)
      2. Verify HEAD == base_commit
      3. Apply tracked diff with --check before actual apply
      4. Restore untracked archive IF manifest present (fail-fast if archive missing)
      5. Validate restored workspace_state via single fingerprint capture

    Untracked state is treated as audit metadata, not a blocking gate:
      - manifest non-empty + archive path empty/missing  -> BLOCK
      - manifest non-empty + archive present              -> extract, audit
      - manifest empty                                    -> no-op, audit "not_applicable"
    """
    from condiag.workspace import capture_workspace_fingerprint

    manifest = snapshot.untracked_manifest if snapshot else []
    restore = RestoreResult(
        base_commit=base_commit,
        untracked_manifest_count=len(manifest),
        untracked_manifest_sha=(snapshot.untracked_manifest_sha if snapshot else "") or "",
        untracked_archive_expected=bool(manifest),
        untracked_archive_present=False,
        untracked_archive_extracted=False,
        untracked_restore_status="not_applicable",
    )

    temp_path = None
    try:
        cid = agent.env.container_id
        if not cid:
            restore.reason = "no_container_id"
            return restore

        # Protection 1a: Reset to base_commit FIRST (SWE-bench images may not
        # have HEAD == base_commit after build).
        agent.env.execute({"command": f"cd /testbed && git reset --hard {base_commit} 2>/dev/null"})
        agent.env.execute({"command": "cd /testbed && git clean -fd 2>/dev/null"})

        # Protection 2: Verify HEAD is now at base_commit
        head_r = agent.env.execute({"command": "cd /testbed && git rev-parse HEAD 2>/dev/null"})
        if head_r.get("returncode") != 0 or head_r.get("output", "").strip() != base_commit:
            restore.reason = (
                f"HEAD mismatch after reset: expected {base_commit}, "
                f"got {head_r.get('output', '').strip()!r}"
            )
            return restore

        if not snapshot or (not snapshot.tracked_diff and not manifest):
            restore.ok = True
            restore.workspace_sha = "clean_base"
            restore.reason = "no_diff_or_untracked"
            restore.untracked_restore_status = "not_applicable"
            return restore

        # Protection 3: Apply tracked diff (if any)
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
                restore.reason = (
                    f"docker cp tracked diff failed: "
                    f"{r1.stderr.decode(errors='replace')[:200]}"
                )
                return restore

            r2 = agent.env.execute({"command": "cd /testbed && git apply --check --index /tmp/restore.patch 2>&1"})
            if r2.get("returncode") != 0:
                restore.reason = "git apply --check failed for tracked diff"
                return restore

            r3 = agent.env.execute({"command": "cd /testbed && git apply --whitespace=nowarn --index /tmp/restore.patch 2>&1"})
            if r3.get("returncode") != 0:
                restore.reason = "git apply failed for tracked diff"
                return restore

        # Protection 4: Untracked archive handling
        archive_path = snapshot.untracked_archive_path if snapshot else ""
        if manifest:
            # Manifest is non-empty -> archive MUST be present and loadable.
            if not archive_path:
                restore.untracked_restore_status = "failed"
                restore.reason = (
                    f"untracked manifest has {len(manifest)} entries but "
                    f"untracked_archive_path is empty"
                )
                return restore
            if not os.path.exists(archive_path):
                restore.untracked_restore_status = "failed"
                restore.reason = (
                    f"untracked archive missing on disk: {archive_path!r}"
                )
                return restore

            restore.untracked_archive_present = True
            cp_r = subprocess.run(
                ["docker", "cp", archive_path, f"{cid}:/tmp/untracked.tar"],
                capture_output=True, timeout=10,
            )
            if cp_r.returncode != 0:
                restore.untracked_restore_status = "failed"
                restore.reason = (
                    f"docker cp untracked archive failed: "
                    f"{cp_r.stderr.decode(errors='replace')[:200]}"
                )
                return restore

            tar_r = agent.env.execute({"command": "cd /testbed && tar xf /tmp/untracked.tar 2>&1"})
            if tar_r.get("returncode") != 0:
                restore.untracked_restore_status = "failed"
                restore.reason = (
                    f"untracked tar extract failed: "
                    f"{tar_r.get('output', '')[:200]}"
                )
                return restore

            restore.untracked_archive_extracted = True
            restore.untracked_restore_status = "ok"
        else:
            restore.untracked_restore_status = "not_applicable"

        # Protection 5: Validate restored workspace via unified fingerprint.
        # Single call: captures HEAD, tracked diff, AND untracked manifest.
        restored_cr = capture_workspace_fingerprint(agent, base_commit)
        if not restored_cr.ok or restored_cr.snapshot is None:
            restore.reason = (
                f"fingerprint failed after restore: {restored_cr.reason}"
            )
            restore.untracked_restore_status = restore.untracked_restore_status or "failed"
            return restore

        if restored_cr.snapshot.tracked_diff_sha != snapshot.tracked_diff_sha:
            restore.reason = (
                f"tracked_diff SHA mismatch after restore: "
                f"{restored_cr.snapshot.tracked_diff_sha} != "
                f"{snapshot.tracked_diff_sha}"
            )
            # Dump diagnostic artifacts before returning.
            if fairness_debug_dir:
                dump_fairness_debug(
                    fairness_debug_dir,
                    snapshot,
                    restored_cr.snapshot,
                    base_commit,
                    label=restore.base_commit or "restore",
                )
            return restore

        restore.ok = True
        restore.workspace_sha = restored_cr.snapshot.tracked_diff_sha
        return restore

    except Exception as e:
        restore.reason = f"exception: {type(e).__name__}: {e}"
        restore.untracked_restore_status = restore.untracked_restore_status or "failed"
        return restore
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except Exception:
                pass


def capture_workspace_sha(agent, base_commit: str) -> str:
    """Pre-step workspace SHA via unified fingerprint function.
    Returns tracked_diff_sha only (untracked files may vary).
    Kept for API stability — internally wraps capture_workspace_fingerprint."""
    from condiag.workspace import capture_workspace_fingerprint
    cr = capture_workspace_fingerprint(agent, base_commit)
    if cr.ok and cr.snapshot is not None:
        return cr.snapshot.tracked_diff_sha
    return ""


def _extract_filenames_from_diff(diff_text: str) -> list[str]:
    """Extract b/ filenames from unified diff headers (mirror of integrity.extract_changed_files)."""
    if not diff_text:
        return []
    files: list[str] = []
    for line in diff_text.splitlines():
        if not line.startswith("diff --git "):
            continue
        try:
            parts = shlex.split(line)
        except ValueError:
            continue
        if len(parts) != 4:
            continue
        if not parts[3].startswith("b/"):
            continue
        candidate = parts[3][2:]
        if candidate:
            files.append(candidate)
    return files


def dump_fairness_debug(
    debug_dir: str,
    expected_snapshot,
    actual_snapshot,
    base_commit: str,
    label: str = "sf",
) -> None:
    """Persist both sides of a tracked-SHA mismatch to disk for root-cause analysis.

    Layout (relative to debug_dir):
      expected_r1_tracked.diff      expected_files.json
      restored_preflight_tracked.diff    actual_files.json
      expected_sha.txt        actual_sha.txt
      expected_head.txt       actual_head.txt
      base_commit.txt
      untracked_expected.json untracked_actual.json
      manifest.txt (freeform summary)
    Returns silently on filesystem errors — this is best-effort diagnostics.
    """
    if not debug_dir:
        return
    try:
        d = Path(debug_dir)
        d.mkdir(parents=True, exist_ok=True)

        expected_diff = expected_snapshot.tracked_diff if expected_snapshot else ""
        actual_diff = actual_snapshot.tracked_diff if actual_snapshot else ""
        (d / "expected_r1_tracked.diff").write_text(expected_diff or "")
        (d / "restored_preflight_tracked.diff").write_text(actual_diff or "")

        (d / "expected_sha.txt").write_text(
            (expected_snapshot.tracked_diff_sha if expected_snapshot else "") or ""
        )
        (d / "actual_sha.txt").write_text(
            (actual_snapshot.tracked_diff_sha if actual_snapshot else "") or ""
        )

        (d / "expected_head.txt").write_text(
            (expected_snapshot.base_commit_sha if expected_snapshot else "") or ""
        )
        (d / "actual_head.txt").write_text(
            (actual_snapshot.base_commit_sha if actual_snapshot else "") or ""
        )
        (d / "base_commit.txt").write_text(base_commit or "")

        expected_files = _extract_filenames_from_diff(expected_diff)
        actual_files = _extract_filenames_from_diff(actual_diff)
        (d / "expected_files.json").write_text(
            json.dumps({"files": expected_files, "count": len(expected_files)}, indent=2)
        )
        (d / "actual_files.json").write_text(
            json.dumps({"files": actual_files, "count": len(actual_files)}, indent=2)
        )

        (d / "untracked_expected.json").write_text(
            json.dumps(
                [u.to_dict() for u in (expected_snapshot.untracked_manifest or [])]
                if expected_snapshot else [],
                indent=2,
            )
        )
        (d / "untracked_actual.json").write_text(
            json.dumps(
                [u.to_dict() for u in (actual_snapshot.untracked_manifest or [])]
                if actual_snapshot else [],
                indent=2,
            )
        )

        lines = [
            f"label: {label}",
            f"base_commit: {base_commit}",
            f"expected_tracked_sha: {(expected_snapshot.tracked_diff_sha if expected_snapshot else '') or ''}",
            f"actual_tracked_sha:   {(actual_snapshot.tracked_diff_sha if actual_snapshot else '') or ''}",
            f"expected_diff_len:    {len(expected_diff)}",
            f"actual_diff_len:      {len(actual_diff)}",
            f"expected_files_count: {len(expected_files)}",
            f"actual_files_count:   {len(actual_files)}",
            f"expected_head: {(expected_snapshot.base_commit_sha if expected_snapshot else '') or ''}",
            f"actual_head:   {(actual_snapshot.base_commit_sha if actual_snapshot else '') or ''}",
            "expected_files_list: " + ", ".join(expected_files[:20]),
            "actual_files_list:   " + ", ".join(actual_files[:20]),
        ]
        (d / "manifest.txt").write_text("\n".join(lines) + "\n")
        logger.warning(
            "[%s] fairness mismatch: expected_tracked_sha=%s actual_tracked_sha=%s "
            "expected_diff_len=%d actual_diff_len=%d "
            "expected_files=%d actual_files=%d",
            label.upper(),
            (expected_snapshot.tracked_diff_sha if expected_snapshot else "") or "",
            (actual_snapshot.tracked_diff_sha if actual_snapshot else "") or "",
            len(expected_diff), len(actual_diff),
            len(expected_files), len(actual_files),
        )
    except Exception as e:
        logger.warning("dump_fairness_debug failed: %s", e)


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
    fairness_debug_dir: str | None = None,
) -> BranchResult:
    """Restore R1 state, inject messages, run until 2nd submission.

    Args:
        mode: "sf" (FW only) or "condiag" (FW + diagnosis).
        protocol_config: RevisionProtocolConfig — overrides LIMITS defaults.
        workspace_snapshot: WorkspaceSnapshot to restore before first step.
                           If None, workspace restore is skipped.
        fairness_debug_dir: directory to dump diagnostic artifacts when the
                           preflight SHA mismatches R1 snapshot. The caller
                           typically passes `<inst_dir>/fairness_debug`.
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
    restore = RestoreResult(ok=True, reason="no_snapshot", base_commit=base_commit)
    ws_before = ""
    preflight_actual = None  # populated only if we run a preflight fingerprint
    if workspace_snapshot:
        restore = restore_workspace(agent, workspace_snapshot, base_commit,
                                      fairness_debug_dir=fairness_debug_dir)
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

        # Single preflight fingerprint capture. Combines the previous
        # `capture_workspace_sha` + `capture_workspace_fingerprint` double
        # invocation into one atomic container call. The result provides:
        #   - ws_before            : tracked_diff_sha (used for log/comparison)
        #   - preflight_actual     : full snapshot (used for mismatch diagnostics)
        from condiag.workspace import capture_workspace_fingerprint
        preflight_cr = capture_workspace_fingerprint(agent, base_commit)
        if not preflight_cr.ok or preflight_cr.snapshot is None:
            return BranchResult(
                mode=mode,
                termination_reason=f"preflight_fairness_failed:fingerprint_failed:{preflight_cr.reason}",
                restore_result=RestoreResult(
                    ok=False, reason=f"preflight fingerprint failed: {preflight_cr.reason}",
                    base_commit=base_commit,
                    untracked_restore_status="failed",
                ),
                messages=list(agent.messages),
                n_calls_total=r1_n_calls, n_calls_incremental=0,
                cost_total=r1_cost, cost_incremental=0.0,
                duration_seconds=time.time() - t0,
            )

        preflight_actual = preflight_cr.snapshot
        ws_before = preflight_actual.tracked_diff_sha
        if not ws_before:
            return BranchResult(
                mode=mode, termination_reason="preflight_fairness_failed:empty_sha",
                restore_result=RestoreResult(
                    ok=False, reason="preflight SHA empty",
                    base_commit=base_commit,
                ),
                messages=list(agent.messages),
                n_calls_total=r1_n_calls, n_calls_incremental=0,
                cost_total=r1_cost, cost_incremental=0.0,
                duration_seconds=time.time() - t0,
            )

        if ws_before != workspace_snapshot.tracked_diff_sha:
            # Mismatch — dump diagnostics then return.
            if fairness_debug_dir:
                dump_fairness_debug(
                    fairness_debug_dir,
                    workspace_snapshot,
                    preflight_actual,
                    base_commit,
                    label=mode,
                )
            return BranchResult(
                mode=mode,
                termination_reason="preflight_fairness_failed:tracked_mismatch",
                restore_result=RestoreResult(
                    ok=False,
                    reason=(
                        f"tracked SHA {ws_before} != expected "
                        f"{workspace_snapshot.tracked_diff_sha}"
                    ),
                    base_commit=base_commit,
                ),
                messages=list(agent.messages),
                n_calls_total=r1_n_calls, n_calls_incremental=0,
                cost_total=r1_cost, cost_incremental=0.0,
                duration_seconds=time.time() - t0,
            )

        logger.info(
            "[%s] Workspace restored, pre-step SHA=%s "
            "(expected=%s, untracked_manifest=%d)",
            mode.upper(), ws_before, workspace_snapshot.tracked_diff_sha,
            len(workspace_snapshot.untracked_manifest),
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
