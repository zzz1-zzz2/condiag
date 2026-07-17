"""Unit test for branch_builder.build_branch_messages."""
import sys
sys.path.insert(0, "/home/swelite/condiag")
from condiag.branch_builder import build_branch_messages


def test_basic_with_tool_response():
    r1 = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_A1", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
        ]},
        {"role": "tool", "tool_call_id": "call_A1", "content": "out"},
    ]
    msgs_sf = build_branch_messages(r1, {"failed_tests": ["test_x"]}, style="stateful_feedback")
    msgs_cd = build_branch_messages(r1, {"failed_tests": ["test_x"]}, "# Diagnosis", style="condiag")

    # SF
    assert len(msgs_sf) == 5, f"expected 5 (R1=4 + FW=1), got {len(msgs_sf)}: {msgs_sf}"
    assert msgs_sf[-1]["role"] == "user"
    assert "test_x" in msgs_sf[-1]["content"]
    print(f"SF OK: {len(msgs_sf)} msgs, last={msgs_sf[-1]['content'][:50]}")

    # CD
    assert len(msgs_cd) == 6, f"expected 6 (R1=4 + FW=1 + diag=1), got {len(msgs_cd)}"
    assert msgs_cd[-1]["role"] == "user"
    assert "# Diagnosis" in msgs_cd[-1]["content"]
    print(f"CD OK: {len(msgs_cd)} msgs, last={msgs_cd[-1]['content'][:50]}")


def test_needs_tool_response_injection():
    """When R1 last assistant has tool_calls but no tool response follows, inject one."""
    r1 = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "tool_calls": [
            {"id": "call_NO_TOOL", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
        ]},
        {"role": "exit", "content": "Submitted"},  # exit role should be pruned
    ]
    msgs = build_branch_messages(r1, {"failed_tests": ["test_y"]}, style="stateful_feedback")
    # R1 has user+assistant+exit. After pruning exit: 2 msgs.
    # After build: 2 + tool_resp + FW = 4 msgs (NOT 5; we had 3 raw but exit was pruned)
    assert len(msgs) == 4, f"expected 4, got {len(msgs)}: {msgs}"
    # Find tool response after the assistant
    after_assistant = [m for m in msgs if m.get("role") == "tool"]
    assert len(after_assistant) == 1, "tool response not injected"
    assert after_assistant[0]["tool_call_id"] == "call_NO_TOOL"
    print(f"OK: exit pruned, tool response injected for call_NO_TOOL")


def test_no_tool_calls_in_last_assistant():
    """When last assistant has no tool_calls, no tool response needed."""
    r1 = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "some text response", "tool_calls": []},
    ]
    msgs = build_branch_messages(r1, None, style="stateful_feedback")
    # No tool response should be added
    tool_msgs = [m for m in msgs if m.get("role") == "tool"]
    assert len(tool_msgs) == 0
    print("OK: no tool response when no tool_calls")


def test_fw_optional():
    """FW is optional (None) — useful for testing the boundary."""
    r1 = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "tool_calls": [{"id": "call_X"}]},
    ]
    msgs = build_branch_messages(r1, None, style="stateful_feedback")
    # Just R1 + tool response = 3
    assert len(msgs) == 3, f"expected 3, got {len(msgs)}"
    print("OK: FW optional works")


def test_diagnosis_only_cd():
    """Diagnosis only when style=condiag and diagnosis is provided."""
    r1 = [{"role": "user", "content": "task"}]
    msgs_sf = build_branch_messages(r1, None, None, style="stateful_feedback")
    msgs_sf2 = build_branch_messages(r1, None, "# diag", style="stateful_feedback")
    msgs_cd = build_branch_messages(r1, None, "# diag", style="condiag")

    assert len(msgs_sf) == 1, "SF without diag should not add anything"
    assert len(msgs_sf2) == 1, "SF with diag but wrong style should not add diag"
    assert len(msgs_cd) == 2, f"CD with diag should add 1 user msg, got {len(msgs_cd)}"
    print("OK: diagnosis style-gated correctly")


def test_r1_prefix_identical():
    """R1 prefix bytes-identical between SF and CD (FAIRNESS)."""
    r1 = [
        {"role": "user", "content": "task"},
        {"role": "assistant", "tool_calls": [{"id": "call_FAIR"}]},
        {"role": "user", "content": "intermediate"},
    ]
    msgs_sf = build_branch_messages(r1, {"failed_tests": []}, None, style="stateful_feedback")
    msgs_cd = build_branch_messages(r1, {"failed_tests": []}, "# diag", style="condiag")
    sf_len = len(msgs_sf)
    cd_len = len(msgs_cd)
    # CD has one extra (diagnosis)
    assert cd_len == sf_len + 1, f"CD should be SF + 1 (extra diag)"
    assert msgs_sf[:sf_len] == msgs_cd[:sf_len], "SF vs CD prefix NOT identical"
    print(f"OK: fairness prefix identical (SF={sf_len}, CD={cd_len})")


if __name__ == "__main__":
    test_basic_with_tool_response()
    test_needs_tool_response_injection()
    test_no_tool_calls_in_last_assistant()
    test_fw_optional()
    test_diagnosis_only_cd()
    test_r1_prefix_identical()
    print("\n✅ All unit tests passed")
