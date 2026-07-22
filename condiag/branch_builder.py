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

    Repairs ALL incomplete assistant turns by inserting synthetic tool
    responses for missing tool_call_ids AFTER all real tool responses in
    the same turn. Each assistant's order is: assistant, real tools,
    synthetic tools (for any IDs not seen).

    This avoids the previous bug where real tool responses were duplicated
    by synthetic ones in the same turn.
    """
    msgs = deepcopy(checkpoint_messages)
    # Clean exit roles
    msgs = [m for m in msgs if m.get("role") != "exit"]

    result: list[dict] = []
    pending_assistant: dict | None = None
    pending_call_ids: list[str] = []
    pending_real_tools: list[dict] = []

    for m in msgs:
        role = m.get("role", "")

        if role == "assistant":
            # Flush any previous turn (new assistant = boundary)
            if pending_assistant is not None:
                _flush_turn(
                    result, pending_assistant, pending_call_ids,
                    pending_real_tools,
                )
            pending_assistant = m
            pending_call_ids = _collect_call_ids(m)
            pending_real_tools = []

        elif role == "tool":
            # Collect tool for the current turn; do NOT flush yet
            # (otherwise we'd lose the chance to add real tools after assistant)
            pending_real_tools.append(m)

        else:
            # User/system message: flush pending turn, then append this
            if pending_assistant is not None:
                _flush_turn(
                    result, pending_assistant, pending_call_ids,
                    pending_real_tools,
                )
                pending_assistant = None
                pending_call_ids = []
                pending_real_tools = []
            result.append(m)

    # Flush final pending turn
    if pending_assistant is not None:
        _flush_turn(
            result, pending_assistant, pending_call_ids, pending_real_tools,
        )

    # FailureWitness
    if failure_witness:
        result.append({"role": "user", "content": _format_fw(failure_witness)})

    # Diagnosis (ConDiag only)
    if style == "condiag" and diagnosis:
        result.append({"role": "user", "content": diagnosis})

    return result


def _flush_turn(
    result: list[dict],
    assistant: dict,
    call_ids: list[str],
    real_tools: list[dict],
) -> None:
    """Append one complete Turn in order: assistant, real tool responses,
    then synthetic tool responses for any missing call_ids.
    """
    # Assistant first
    result.append(assistant)

    # Real tool responses (preserving original order)
    real_ids: set[str] = set()
    for tool in real_tools:
        result.append(tool)
        tid = tool.get("tool_call_id", "")
        if tid:
            real_ids.add(tid)

    # Synthetic responses for any missing IDs (deduped)
    for tid in call_ids:
        if tid and tid not in real_ids:
            result.append({"role": "tool", "tool_call_id": tid, "content": "(output)"})


def _collect_call_ids(msg: dict) -> list[str]:
    """Extract tool_call_ids from both top-level tool_calls and extra.actions.

    Deduplicates by ID (preserving first occurrence order). The same call
    can be represented in both tool_calls and extra.actions, so we must
    not generate duplicate synthetic responses for the same ID.
    """
    ids = []
    for tc in msg.get("tool_calls") or []:
        if isinstance(tc, dict):
            ids.append(tc.get("id") or tc.get("tool_call_id") or "")
    for act in (msg.get("extra") or {}).get("actions", []) or []:
        if isinstance(act, dict):
            ids.append(act.get("tool_call_id") or "")
    # Dedupe, preserve order
    return list(dict.fromkeys(t for t in ids if t))


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
