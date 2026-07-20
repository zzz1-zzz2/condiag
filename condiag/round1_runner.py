"""Round 1 runner: initialize agent, run until natural submission, return Round1Result."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from minisweagent.exceptions import Submitted, LimitsExceeded, TimeExceeded, FormatError

from condiag.agent.config import redact_trajectory

logger = logging.getLogger("condiag.round1")

DEFAULT_LIMITS = {"cost_limit": 3.0, "wall_time_limit_seconds": 3600, "max_consecutive_format_errors": 15}


@dataclass
class Round1Result:
    termination_reason: str = ""    # submitted | cost_limit | wall_timeout | repeated_format_error | error
    patch_text: str = ""
    messages: list[dict] = field(default_factory=list)
    n_calls: int = 0
    cost: float = 0.0
    duration_seconds: float = 0.0
    trajectory: dict = field(default_factory=dict)
    workspace_snapshot: Any = None  # WorkspaceSnapshot captured at R1 completion


def run_round1(*, agent_factory: Callable[[], Any], task: str,
               base_commit: str = "",
               protocol_config: Any = None,
               snapshot_dir: str | Path | None = None) -> Round1Result:
    """Run agent until natural Submitted or terminal error.

    Args:
        protocol_config: RevisionProtocolConfig — overrides DEFAULT_LIMITS.
    """
    # Resolve limits from protocol_config or fallback to defaults
    limits = dict(DEFAULT_LIMITS)
    if protocol_config:
        limits["wall_time_limit_seconds"] = getattr(protocol_config, "r1_wall_time_limit_seconds", limits["wall_time_limit_seconds"])
        limits["max_consecutive_format_errors"] = getattr(protocol_config, "r1_max_consecutive_format_errors", limits["max_consecutive_format_errors"])

    agent = agent_factory()
    agent.config.step_limit = 0  # unlimited — we control wall/time limits externally

    agent.extra_template_vars |= {"task": task}
    agent.messages = []
    agent.add_messages(
        agent.model.format_message(role="system", content=agent._render_template(agent.config.system_template)),
        agent.model.format_message(role="user", content=agent._render_template(agent.config.instance_template)),
    )

    t0 = time.time()
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
                elif isinstance(e, LimitsExceeded):
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
        # Capture workspace snapshot (before container dies)
        snapshot = _capture_snapshot(agent, base_commit, Path(snapshot_dir) if snapshot_dir else None)
        result = Round1Result(
            termination_reason=reason,
            patch_text=_canonical_patch(agent, base_commit),
            messages=[m for m in agent.messages if m.get("role") != "exit"],
            n_calls=agent.n_calls,
            cost=agent.cost,
            duration_seconds=time.time() - t0,
            trajectory=redact_trajectory(agent.serialize()) if hasattr(agent, "serialize") else {},
            workspace_snapshot=snapshot,
        )

    logger.info("R1: reason=%s calls=%d cost=%.4f patch=%dch",
                 reason, result.n_calls, result.cost, len(result.patch_text))
    return result


def _canonical_patch(agent, base_commit: str = "") -> str:
    try:
        b = base_commit or "HEAD"
        agent.env.execute({"command": "cd /testbed && git add -N . 2>/dev/null; true"})
        r = agent.env.execute({"command": f"cd /testbed && git diff --binary {b} 2>/dev/null"})
        agent.env.execute({"command": "cd /testbed && git reset -N . 2>/dev/null; true"})
        return r.get("output", "")
    except Exception:
        return ""


def _capture_snapshot(agent, base_commit: str, snapshot_dir: Path | None = None) -> Any:
    """Capture full workspace snapshot (tracked + untracked) from live container."""
    from condiag.workspace import capture_workspace_snapshot
    try:
        return capture_workspace_snapshot(agent, base_commit, snapshot_dir)
    except Exception as e:
        logger.error("Failed to capture workspace snapshot: %s", e)
        return None
