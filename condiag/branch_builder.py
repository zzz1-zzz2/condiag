"""Shared branch message builder: used by both gate test and paired runner."""
from __future__ import annotations
from copy import deepcopy
from typing import Any


def build_branch_messages(
    checkpoint_messages: list[dict],
    failure_witness: dict | None,
    diagnosis: str | None = None,
    *,
    style: str = "stateful_feedback",
) -> list[dict]:
    """Build the message sequence for a forked Round 2 branch.

    Given R1 checkpoint messages, produces:
      R1 messages (deep-copied)
      + synthetic tool responses for any ASSISTANT with missing responses
        (inserted immediately after their assistant, not at end of list)
      + FailureWitness (user message)
      + [Diagnosis (user message, ConDiag only)]

    This function repairs ALL incomplete turns, not just the last one.
    Each missing tool response is inserted right after its assistant,
    preserving the tool_call chain order for the API.
    """
    msgs = deepcopy(checkpoint_messages)

    # Clean exit roles
    msgs = [m for m in msgs if m.get("role") != "exit"]

    # Build result by processing turns in order
    result: list[dict] = []
    pending_assistant = None
    pending_call_ids: list[str] = []
    seen_tool_ids: set[str] = set()

    for m in msgs:
        role = m.get("role", "")

        if role == "assistant":
            # If we have a pending assistant, flush it (checking for missed tools)
            if pending_assistant is not None:
                _flush_turn(result, pending_assistant, pending_call_ids, seen_tool_ids)
                pending_call_ids = []
            pending_assistant = m
            # Collect call IDs from both tool_calls and extra.actions
            pending_call_ids = _collect_call_ids(m)
            # Also track any IDs seen so far in the result
            for rm in result:
                if rm.get("role") == "tool" and rm.get("tool_call_id"):
                    seen_tool_ids.add(rm.get("tool_call_id"))
        elif role == "tool":
            # CRITICAL: flush pending assistant BEFORE adding tool to result
            # Otherwise the order is [tool, assistant] violating tool protocol
            if pending_assistant is not None:
                _flush_turn(result, pending_assistant, pending_call_ids, seen_tool_ids)
                pending_assistant = None
                pending_call_ids = []
            tid = m.get("tool_call_id", "")
            if tid:
                seen_tool_ids.add(tid)
            result.append(m)
        else:
            # Flush any pending assistant first
            if pending_assistant is not None:
                _flush_turn(result, pending_assistant, pending_call_ids, seen_tool_ids)
                pending_assistant = None
                pending_call_ids = []
            result.append(m)

    # Flush final pending assistant
    if pending_assistant is not None:
        _flush_turn(result, pending_assistant, pending_call_ids, seen_tool_ids)

    # 2. Inject FailureWitness
    if failure_witness:
        result.append({"role": "user", "content": _format_fw(failure_witness)})

    # 3. Inject Diagnosis (ConDiag only)
    if style == "condiag" and diagnosis:
        result.append({"role": "user", "content": diagnosis})

    return result


def _flush_turn(
    result: list[dict],
    assistant: dict,
    call_ids: list[str],
    seen_ids: set[str],
) -> None:
    """Append the assistant message and any missing synthetic tool responses."""
    result.append(assistant)
    for tid in call_ids:
        if tid and tid not in seen_ids:
            seen_ids.add(tid)
            result.append({"role": "tool", "tool_call_id": tid, "content": "(output)"})


def _collect_call_ids(msg: dict) -> list[str]:
    """Extract tool_call_ids from both top-level tool_calls and extra.actions."""
    ids = []
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            ids.append(tc.get("id") or tc.get("tool_call_id") or "")
    for act in (msg.get("extra") or {}).get("actions", []) or []:
        if isinstance(act, dict):
            ids.append(act.get("tool_call_id") or "")
    return [t for t in ids if t]


def _format_fw(fw: dict) -> str:
    """Build failure witness user message text."""
    failed = [str(f) if not isinstance(f, str) else f
              for f in fw.get("failed_tests", [])]
    error = fw.get("error_message", "")
    parts = ["## Validation Result\n\nYour submitted patch did not pass validation.\n"]
    if failed:
        parts.append(f"Failed tests: {', '.join(failed)}\n")
    if error:
        parts.append(f"Error:\n```\n{error}\n```")
    parts.append("\nPlease investigate and revise your patch.")
    return "".join(parts)
