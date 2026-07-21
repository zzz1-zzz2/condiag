"""Trajectory extractor — extract structured signals from agent trajectory."""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import PurePosixPath

from condiag.diagnosis.signals.schema import TrajectorySignals

logger = logging.getLogger("condiag.diagnosis.signals.trajectory_extractor")

# ── Patterns ────────────────────────────────────────────────────────

_RE_FILE_PATH = re.compile(r"(?:`)?(astropy|django|sympy|sphinx|matplotlib|sklearn|pydata|pytest|pylint|psf|mwaskom|pallets)/(?:[a-zA-Z_/]+\.py)\b")
_RE_BASH_COMMAND = re.compile(r"```bash\s*\n(.+?)```", re.DOTALL)
_RE_TOOL_OUTPUT_FILE = re.compile(r"/testbed/(\S+\.py)")


def extract_trajectory_signals(trajectory: dict) -> TrajectorySignals:
    """Extract structured signals from a mini-SWE-agent trajectory dict.

    Parses:
      - Assistant turns and tool calls
      - Viewed files (from tool outputs and explore_context events)
      - Format errors (from user messages)
      - Exploration patterns

    Returns:
        TrajectorySignals with populated fields.
    """
    signals = TrajectorySignals()
    messages = trajectory.get("messages", []) if isinstance(trajectory, dict) else trajectory

    tool_call_counter: Counter = Counter()
    visited_files_raw: set[str] = set()
    viewed_functions: set[str] = set()
    format_error_count = 0
    bash_command_count = 0
    test_command_count = 0

    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant":
            tc = msg.get("tool_calls") or []
            actions = (msg.get("extra") or {}).get("actions") or []
            total_calls = len(tc) + len(actions)
            signals.total_tool_calls += total_calls
            signals.assistant_turn_count += 1

            # Track tool types
            for t in tc:
                if isinstance(t, dict):
                    fn = t.get("function", {})
                    if isinstance(fn, dict):
                        tool_call_counter[fn.get("name", "unknown")] += 1
                    elif isinstance(fn, str):
                        tool_call_counter[fn] += 1
                elif isinstance(t, str):
                    tool_call_counter[t] += 1

            for act in actions:
                if isinstance(act, dict):
                    tool_call_counter[act.get("type", "bash")] += 1

            # Detect bash commands from content
            content = msg.get("content", "") or ""
            cmds = _RE_BASH_COMMAND.findall(content)
            for cmd in cmds:
                bash_command_count += 1
                if any(kw in cmd for kw in ["pytest", "python -m pytest", "python -m django", "tox"]):
                    test_command_count += 1

        elif role == "tool":
            content = str(msg.get("content", "") or "")
            # Extract file paths from tool output
            for m in _RE_TOOL_OUTPUT_FILE.finditer(content):
                visited_files_raw.add(m.group(1))

        elif role == "user":
            content = msg.get("content", "") or ""
            if "No tool calls found" in content or "Error parsing tool call" in content:
                format_error_count += 1

    signals.format_error_count = format_error_count
    signals.tool_type_counts = dict(tool_call_counter)
    signals.viewed_files = sorted(visited_files_raw)
    signals.bash_commands_run = bash_command_count
    signals.test_commands_run = test_command_count

    # Exploration concentration: ratio of top-5 most-visited files to total visited
    if visited_files_raw:
        signals.exploration_concentration = min(4 * 5.0 / len(visited_files_raw), 1.0)
    else:
        signals.exploration_concentration = 0.0

    return signals
