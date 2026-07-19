"""Branch runner: fork from R1 snapshot, inject FW/diagnosis, run until 2nd submission."""
from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from minisweagent.exceptions import Submitted, LimitsExceeded, TimeExceeded, FormatError

from condiag.branch_builder import build_branch_messages

logger = logging.getLogger("condiag.branch")

LIMITS = {"cost_limit": 3.0, "wall_time_limit_seconds": 3600, "max_consecutive_format_errors": 3}


@dataclass
class BranchResult:
    mode: str = ""                  # sf | condiag
    termination_reason: str = ""    # submitted | cost_limit | wall_timeout | repeated_format_error | error
    patch_text: str = ""
    messages: list[dict] = field(default_factory=list)
    n_calls_total: int = 0
    n_calls_incremental: int = 0
    cost_total: float = 0.0
    cost_incremental: float = 0.0
    duration_seconds: float = 0.0
    trajectory: dict = field(default_factory=dict)


def run_branch(
    *,
    agent_factory: Callable[[], Any],
    checkpoint_messages: list[dict],
    base_commit: str,
    task: str,
    patch_to_apply: str,
    r1_n_calls: int,
    r1_cost: float,
    failure_witness: dict | None,
    diagnosis: str | None = None,
    mode: str = "sf",
) -> BranchResult:
    """Restore R1 state, inject messages, run until 2nd submission.

    Args:
        mode: "sf" (FW only) or "condiag" (FW + diagnosis).
    """
    agent = agent_factory()
    agent.config.step_limit = 0
    agent.n_calls = r1_n_calls
    agent.cost = r1_cost

    # Build message sequence via shared function
    agent.messages = build_branch_messages(
        checkpoint_messages, failure_witness, diagnosis=diagnosis,
        style="stateful_feedback" if mode == "sf" else "condiag",
    )

    # Restore workspace
    _apply_patch(agent, patch_to_apply)

    t0 = time.time()
    reason = ""
    try:
        while True:
            if time.time() - t0 > LIMITS["wall_time_limit_seconds"]:
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
                    if agent.n_consecutive_format_errors >= LIMITS["max_consecutive_format_errors"]:
                        reason = "repeated_format_error"; break
                    continue
                else:
                    agent.handle_uncaught_exception(e)
                    reason = f"error:{type(e).__name__}"; break
    finally:
        result = BranchResult(
            mode=mode,
            termination_reason=reason,
            patch_text=_canonical_patch(agent, base_commit),
            messages=list(agent.messages),
            n_calls_total=agent.n_calls,
            n_calls_incremental=agent.n_calls - r1_n_calls,
            cost_total=agent.cost,
            cost_incremental=agent.cost - r1_cost,
            duration_seconds=time.time() - t0,
            trajectory=agent.serialize() if hasattr(agent, "serialize") else {},
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
