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
      + tool response (if last assistant has tool_calls and no tool response follows)
      + FailureWitness (user message)
      + [Diagnosis (user message, ConDiag only)]

    Args:
        checkpoint_messages: R1 messages (exit roles already stripped).
        failure_witness: FW dict (None = skip FW, for testing).
        diagnosis: Diagnosis prompt string (None or "" = skip, ConDiag only).
        style: "stateful_feedback" (FW only) or "condiag" (FW + diagnosis).

    Returns:
        Message list ready for Round 2 agent.step().
    """
    msgs = deepcopy(checkpoint_messages)

    # Clean exit roles just in case
    msgs = [m for m in msgs if m.get("role") != "exit"]

    # 1. Inject tool response for the LAST assistant message with tool_calls
    #    if no tool response already exists for that call.
    for i in range(len(msgs) - 1, -1, -1):
        prev = msgs[i]
        if prev.get("role") != "assistant":
            continue
        # Collect tool_call_ids from top-level tool_calls and extra.actions
        call_ids = _collect_call_ids(prev)
        if not call_ids:
            continue
        # Check which IDs already have responses
        existing = {m.get("tool_call_id") for m in msgs if m.get("role") == "tool"}
        missing = [tid for tid in call_ids if tid and tid not in existing]
        if missing:
            msgs.append({"role": "tool", "tool_call_id": missing[0],
                          "content": "(output)"})
        break  # only the last assistant

    # 2. Inject FailureWitness
    if failure_witness:
        msgs.append({"role": "user", "content": _format_fw(failure_witness)})

    # 3. Inject Diagnosis (ConDiag only)
    if style == "condiag" and diagnosis:
        msgs.append({"role": "user", "content": diagnosis})

    return msgs


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
