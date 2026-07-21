"""Trajectory extractor — extract structured signals from agent trajectory."""
from __future__ import annotations

import logging
import re
from collections import Counter
from pathlib import PurePosixPath

from condiag.diagnosis.signals.schema import TrajectorySignals

logger = logging.getLogger("condiag.diagnosis.signals.trajectory_extractor")

# ── Patterns ────────────────────────────────────────────────────────

_RE_BASH_COMMAND = re.compile(r"```bash\s*\n(.+?)```", re.DOTALL)
_RE_TOOL_OUTPUT_FILE = re.compile(r"/testbed/([^\s\"']+\.py)")
_RE_CAT_FILE = re.compile(r"(?:^|\s)(?:cat|head|tail|sed|nl|wc)\s+(-[a-zA-Z0-9]+\s+)?(/testbed/\S+|[^\s;|&`()]+\.py)")
_RE_VIEW_FILE = re.compile(r"(?:^|\s)(?:grep|rg|find|ack|ag)\s+(-[a-zA-Z0-9]+\s+)?['\"]?[^'\"]+['\"]?\s+(/[^\s;|&`]+\.py|[^\s;|&`]+\.py)")


def extract_trajectory_signals(trajectory: dict) -> TrajectorySignals:
    """Extract structured signals from a mini-SWE-agent trajectory dict.

    Returns:
        TrajectorySignals with populated fields.
    """
    signals = TrajectorySignals()
    messages = trajectory.get("messages", []) if isinstance(trajectory, dict) else trajectory

    tool_call_counter: Counter = Counter()
    file_view_counter: Counter = Counter()
    format_error_count = 0
    bash_command_count = 0
    test_command_count = 0
    seen_tool_events: set[str] = set()

    for msg in messages:
        role = msg.get("role", "")

        if role == "assistant":
            tc = msg.get("tool_calls") or []
            actions = (msg.get("extra") or {}).get("actions") or []
            signals.assistant_turn_count += 1

            # Get command text from tool arguments
            cmd_texts: list[str] = []

            for t in tc:
                fn = t.get("function", {}) if isinstance(t, dict) else {}
                if isinstance(fn, dict):
                    name = fn.get("name", "")
                    args_raw = fn.get("arguments", "")
                    if isinstance(args_raw, str):
                        cmd_texts.append(args_raw)
                    elif isinstance(args_raw, dict):
                        cmd_texts.append(str(args_raw))
                    tool_call_counter[name] += 1
                    # Dedup by function call id
                    tid = t.get("id", "")
                    if tid:
                        seen_tool_events.add(f"tc:{tid}")

            for act in actions:
                if isinstance(act, dict):
                    act_type = act.get("type", "bash")
                    tool_call_counter[act_type] += 1
                    aid = act.get("id", "")
                    if aid:
                        seen_tool_events.add(f"act:{aid}")
                    cmd_texts.append(act.get("command", act.get("arguments", "")))

            signals.total_tool_calls = len(seen_tool_events) if seen_tool_events else (
                len(tc) + len(actions)
            )

            # Extract viewed files from command arguments
            for txt in cmd_texts:
                if not isinstance(txt, str):
                    continue
                for m in _RE_CAT_FILE.finditer(txt):
                    fp = m.group(2) if m.group(2) else m.group(3)
                    if fp:
                        fp = fp.lstrip("/testbed/")
                        file_view_counter[fp] += 1
                for m in _RE_VIEW_FILE.finditer(txt):
                    fp = m.group(2) if m.group(2) else m.group(3)
                    if fp:
                        fp = fp.lstrip("/testbed/")
                        file_view_counter[fp] += 1

            # Detect bash commands from content
            content = msg.get("content", "") or ""
            cmds = _RE_BASH_COMMAND.findall(content)
            for cmd in cmds:
                bash_command_count += 1
                if any(kw in cmd for kw in ["pytest", "python -m pytest", "python -m django", "tox"]):
                    test_command_count += 1

        elif role == "tool":
            content = str(msg.get("content", "") or "")
            for m in _RE_TOOL_OUTPUT_FILE.finditer(content):
                fp = m.group(1)
                file_view_counter[fp] += 1

        elif role == "user":
            content = msg.get("content", "") or ""
            if "No tool calls found" in content or "Error parsing tool call" in content:
                format_error_count += 1

    signals.format_error_count = format_error_count
    signals.tool_type_counts = dict(tool_call_counter)
    signals.viewed_files = sorted(file_view_counter.keys())
    signals.file_view_counts = dict(file_view_counter)
    signals.bash_commands_run = bash_command_count
    signals.test_commands_run = test_command_count

    # Exploration concentration: ratio of top-5 most-viewed files to total views
    total_views = sum(file_view_counter.values())
    if total_views:
        top5 = sum(c for _, c in file_view_counter.most_common(5))
        signals.exploration_concentration = top5 / total_views
    else:
        signals.exploration_concentration = 0.0

    return signals
