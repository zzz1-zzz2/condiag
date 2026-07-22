"""Strong assertion tests for P1 final correctness hotfix."""
from __future__ import annotations

import json

from condiag.branch_builder import build_branch_messages
from condiag.diagnosis.signals.trajectory_extractor import extract_trajectory_signals


class TestBranchBuilderOrder:
    """Tool responses MUST follow their corresponding assistant in result list."""

    def _to_exchange(self, msg):
        # Build tool_calls from tool_call_id mapping
        role = msg["role"]
        if role == "assistant":
            tcs = msg.get("tool_calls") or []
            for tc in tcs:
                if "function" not in tc:
                    tc["function"] = {"name": "", "arguments": "{}"}
        return msg

    def test_tool_response_follows_assistant(self):
        """Basic case: assistant(tool_call=a) + tool(a) -> tool must be AFTER assistant."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "bash", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_1", "content": "out"},
        ]
        result = build_branch_messages(msgs, failure_witness=None)
        # Find assistant and tool positions
        asst_idx = next(i for i, m in enumerate(result) if m["role"] == "assistant")
        tool_idx = next(i for i, m in enumerate(result) if m["role"] == "tool")
        assert asst_idx < tool_idx, f"Tool must come AFTER assistant: {[(i, m['role']) for i, m in enumerate(result)]}"

    def test_synthetic_responses_preserve_order(self):
        """If assistant A is missing response, synthetic must be inserted right after A, not at end."""
        msgs = [
            {"role": "assistant", "content": "A", "tool_calls": [
                {"id": "call_a", "function": {"name": "bash", "arguments": "{}"}}
            ]},
            # missing response for call_a
            {"role": "assistant", "content": "B", "tool_calls": [
                {"id": "call_b", "function": {"name": "bash", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "call_b", "content": "out_b"},
        ]
        result = build_branch_messages(msgs, failure_witness=None)

        # Find positions: A_asst, A_synth, B_asst, B_tool
        asst_a_idx = None
        asst_b_idx = None
        for i, m in enumerate(result):
            if m["role"] == "assistant" and m["content"] == "A":
                asst_a_idx = i
            if m["role"] == "assistant" and m["content"] == "B":
                asst_b_idx = i
        # After A: must have synthetic tool (with call_a id), then B asst, then B tool
        after_a = result[asst_a_idx + 1]
        assert after_a["role"] == "tool" and after_a.get("tool_call_id") == "call_a", \
            f"After A must be synthetic tool call_a, got {after_a}"
        after_synth = result[asst_a_idx + 2]
        assert after_synth["role"] == "assistant" and after_synth["content"] == "B", \
            f"After synthetic must be B asst, got {after_synth}"
        # All assistant positions must come before all their tool positions
        for i, m in enumerate(result):
            if m["role"] == "assistant" and m.get("tool_calls"):
                # Find any of this assistant's tool calls
                for tc in m["tool_calls"]:
                    tid = tc.get("id", "")
                    # Find corresponding tool
                    for j, m2 in enumerate(result):
                        if m2["role"] == "tool" and m2.get("tool_call_id") == tid:
                            assert i < j, f"Assistant at {i} precedes tool at {j}"

    def test_failure_witness_injected_at_end(self):
        """FailureWitness must be appended as user message after all turns."""
        msgs = [
            {"role": "assistant", "content": "first", "tool_calls": [
                {"id": "c1", "function": {"name": "bash", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "out"},
        ]
        result = build_branch_messages(msgs, failure_witness={"failed_tests": ["t1"], "error_message": "err"})
        assert result[-1]["role"] == "user"
        assert "Failed tests" in result[-1]["content"] or "err" in result[-1]["content"]

    def test_existing_tool_response_not_duplicated(self):
        """Real tool response must NOT be duplicated by synthetic one."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "bash", "arguments": "ls"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "real_output"},
        ]
        result = build_branch_messages(msgs, failure_witness=None)
        c1_responses = [m for m in result
                       if m.get("role") == "tool" and m.get("tool_call_id") == "c1"]
        assert len(c1_responses) == 1, f"Expected 1 c1 response, got {len(c1_responses)}"
        assert c1_responses[0]["content"] == "real_output", \
            f"Real response must be preserved, got {c1_responses[0]['content']}"

    def test_parallel_real_responses_not_duplicated(self):
        """Multiple real tool responses must each be unique."""
        msgs = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "function": {"name": "bash", "arguments": "ls"}},
                {"id": "c2", "function": {"name": "bash", "arguments": "pwd"}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "real1"},
            {"role": "tool", "tool_call_id": "c2", "content": "real2"},
        ]
        result = build_branch_messages(msgs, failure_witness=None)
        c1 = [m for m in result if m.get("role") == "tool" and m.get("tool_call_id") == "c1"]
        c2 = [m for m in result if m.get("role") == "tool" and m.get("tool_call_id") == "c2"]
        assert len(c1) == 1 and c1[0]["content"] == "real1"
        assert len(c2) == 1 and c2[0]["content"] == "real2"


class TestTrajectoryNoIDDedup:
    """Anonymous tool calls must dedup by content, not just by source:type."""

    def test_two_anonymous_bash_different_commands_counted_separately(self):
        """Two anonymous bash with different commands must be distinct events."""
        traj = {
            "messages": [
                {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "bash", "arguments": "ls"}}
                ]},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "bash", "arguments": "pwd"}}
                ]},
            ]
        }
        signals = extract_trajectory_signals(traj)
        assert signals.total_tool_calls == 2, \
            f"Different commands should be 2 events, got {signals.total_tool_calls}"

    def test_same_anonymous_call_in_tc_and_actions_deduped(self):
        """Same call replicated in tool_calls and extra.actions must be 1 event."""
        traj = {
            "messages": [
                {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "bash", "arguments": "pytest"}}
                ], "extra": {"actions": [
                    {"type": "bash", "command": "pytest"}
                ]}},
            ]
        }
        signals = extract_trajectory_signals(traj)
        assert signals.total_tool_calls == 1, \
            f"Same call in tc+actions should be 1 event, got {signals.total_tool_calls}"

    def test_anonymous_bash_three_identical_dedup(self):
        """Three identical anonymous bash calls in same turn dedup to 1."""
        traj = {
            "messages": [
                {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "bash", "arguments": "ls"}},
                    {"function": {"name": "bash", "arguments": "ls"}},
                    {"function": {"name": "bash", "arguments": "ls"}},
                ]},
            ]
        }
        signals = extract_trajectory_signals(traj)
        assert signals.total_tool_calls == 1, \
            f"Same anonymous call 3x should dedup to 1, got {signals.total_tool_calls}"
