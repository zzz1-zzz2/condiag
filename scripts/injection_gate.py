"""V2c.1a — Self-contained injection gate using shared branch_builder.

Uses synthetic R1 checkpoints to verify the function works correctly.
No external artifacts needed.
"""
import hashlib, json, logging, sys
import copy

sys.path.insert(0, "/home/swelite/condiag")
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
log = logging.getLogger("v2c_gate")

from condiag.branch_builder import build_branch_messages


def sha(d): return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def make_fw(): return {"failed_tests": ["test_motivation", "test_oracle"], "error_message": "AssertionError: expected context", "stack_frames": []}


def main():
    log.info("=" * 60)
    log.info("V2c.1a Injection Gate (self-contained)")
    log.info("=" * 60)
    errors = []

    # Synthetic R1: agent explored, ran a bash command, captured output
    r1_msgs = [
        {"role": "system", "content": "You are a SWE."},
        {"role": "user", "content": "Fix the bug."},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_R1_01", "type": "function", "function": {"name": "bash", "arguments": "{}"}
        }]},
        {"role": "tool", "tool_call_id": "call_R1_01", "content": "looking at code.py..."},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_R1_02", "type": "function", "function": {"name": "bash", "arguments": "{}"}
        }]},
        {"role": "tool", "tool_call_id": "call_R1_02", "content": "found at line 42"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "call_SUBMIT", "type": "function", "function": {"name": "bash", "arguments": "{}"}
        }]},
        # Submit echoed "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT", env raised Submitted,
        # trajectory should already have a tool response (env.execute output)
        {"role": "tool", "tool_call_id": "call_SUBMIT", "content": "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"},
        {"role": "exit", "content": "Submission: ", "extra": {"exit_status": "Submitted"}},
    ]
    r1_len = len(r1_msgs)
    r1_sha = sha(r1_msgs)
    log.info("Synthetic R1: %d msgs sha=%s", r1_len, r1_sha)

    fw = make_fw()
    diag = "# Diagnosis content here. Investigate missing context."

    sf_msgs = build_branch_messages(r1_msgs, fw, diagnosis=None, style="stateful_feedback")
    cd_msgs = build_branch_messages(r1_msgs, fw, diagnosis=diag, style="condiag")
    sf_len = len(sf_msgs); cd_len = len(cd_msgs)
    log.info("SF: %d msgs, CD: %d msgs (expect CD = SF + 1)", sf_len, cd_len)

    # === Assertions ===
    log.info("\n--- Assertions ---")

    # r1_clean = r1_msgs minus exit = 8 messages
    r1_clean = [m for m in r1_msgs if m.get("role") != "exit"]
    r1_clean_len = len(r1_clean)

    # A. CD prefix == SF prefix (FAIRNESS)
    if sf_msgs[:sf_len] == cd_msgs[:sf_len]:
        log.info("✅ A: SF == CD prefix (same checkpoint messages)")
    else:
        errors.append("FAIRNESS: SF != CD prefix")

    # B. SF = r1_clean + (optional tool_resp if missing) + FW
    #    Since R1 already had a tool response for submit at r1_clean[7], no extra added.
    #    Expected SF = 8 + 1 (FW) = 9
    if sf_len == r1_clean_len + 1:
        log.info(f"✅ B: SF {sf_len} = r1_clean({r1_clean_len}) + FW(1) = {r1_clean_len+1}")
    else:
        errors.append(f"SF {sf_len} != r1_clean({r1_clean_len}) + 1 (FW)")

    # C. CD = SF + 1 (diagnosis)
    if cd_len == sf_len + 1:
        log.info("✅ C: CD count = SF + 1 (diagnosis appended)")
    else:
        errors.append(f"CD {cd_len} != SF {sf_len} + 1")

    # D. Tool response BEFORE FW
    # Find the FW position (last user msg with "Validation" in content)
    fw_idx_sf = next((i for i, m in enumerate(sf_msgs)
                       if m.get("role") == "user" and "Validation" in str(m.get("content", ""))),
                      -1)
    tool_indices_sf = [i for i, m in enumerate(sf_msgs) if m.get("role") == "tool"]
    last_tool_idx = max(tool_indices_sf) if tool_indices_sf else -1
    if last_tool_idx >= 0 and last_tool_idx < fw_idx_sf:
        log.info(f"✅ D: tool @{last_tool_idx} < FW @{fw_idx_sf}")
    else:
        errors.append(f"Tool @{last_tool_idx} not before FW @{fw_idx_sf}")

    # E. No exit role in either
    if not any(m.get("role") == "exit" for m in sf_msgs + cd_msgs):
        log.info("✅ E: no exit role in either branch")
    else:
        errors.append("exit role leaked")

    # F. FW content present
    fw_in = any("test_motivation" in str(m.get("content", "")) for m in sf_msgs if m.get("role") == "user")
    if fw_in:
        log.info("✅ F: FW content present")
    else:
        errors.append("FW content not found")

    # G. Diagnosis content present in CD
    diag_in = any("Diagnosis content" in str(m.get("content", "")) for m in cd_msgs if m.get("role") == "user")
    if diag_in:
        log.info("✅ G: Diagnosis content present in CD")
    else:
        errors.append("Diagnosis not in CD")

    # H. Diagnosis NOT in SF
    diag_not_in_sf = not any("Diagnosis content" in str(m.get("content", "")) for m in sf_msgs)
    if diag_not_in_sf:
        log.info("✅ H: Diagnosis NOT in SF (style-gated)")
    else:
        errors.append("Diagnosis leaked into SF")

    # Manifest
    manifest = {
        "test": "V2c.1a (self-contained)",
        "instance": "synthetic",
        "r1_message_count": r1_len,
        "r1_messages_sha": r1_sha,
        "failure_witness_sha": sha(fw),
        "diagnosis_sha": sha(diag),
        "stateful_feedback": {"msg_count": sf_len, "messages_sha": sha(sf_msgs)},
        "condiag": {"msg_count": cd_len, "messages_sha": sha(cd_msgs)},
        "assertions_passed": len(errors) == 0,
        "errors": errors,
    }
    import os
    out_path = "/home/swelite/condiag/artifacts/v2c/synthetic_branch_manifest.json"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(manifest, f, indent=2)
    log.info("Manifest: %s", out_path)

    if errors:
        log.error("\n❌ GATE FAILED: %d errors", len(errors))
        for e in errors: log.error("  - %s", e)
        sys.exit(1)
    log.info("\n✅ INJECTION GATE PASSED (8/8 assertions)")


if __name__ == "__main__":
    main()
