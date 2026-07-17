"""V2c.1b — Single-step API smoke for SF and CD branches.

Loads R1 checkpoint → injects FW/diag → starts temp container → 1 step() call each.
"""
import json, logging, os, sys, time, hashlib
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
log = logging.getLogger("v2c_smoke")

INSTANCE = "sympy__sympy-20428"
ARTIFACT_DIR = Path(f"/home/swelite/condiag/artifacts/v2c/{INSTANCE}")
SMOKE_DIR = ARTIFACT_DIR / "smoke"

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")


def sha(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


def build_fw(harness_eval_path):
    import re
    with open(harness_eval_path) as f:
        data = json.load(f)
    report = data.get("report", {})
    inst_report = report.get(INSTANCE, report)
    tests = inst_report.get("tests_status", {})
    f2p_fails = tests.get("FAIL_TO_PASS", {}).get("failure", [])
    error_msg = ""
    lp = data.get("test_log_path", "")
    if lp and Path(lp).exists():
        raw = Path(lp).read_text(encoding="utf-8", errors="replace")
        em = re.search(r"(AssertionError|TypeError|ValueError|AttributeError)[^:\n]*:\s*([^\n]+)", raw)
        if em: error_msg = em.group(0).strip()
    return {"failed_tests": list(f2p_fails), "error_message": error_msg, "stack_frames": []}


def format_witness(fw):
    failed = [str(f) if not isinstance(f, str) else f for f in fw.get("failed_tests", [])]
    error = fw.get("error_message", "")
    msg = "## Validation Result\n\nYour submitted patch did not pass validation.\n"
    if failed: msg += f"\nFailed tests: {', '.join(failed)}\n"
    if error: msg += f"\nError:\n```\n{error}\n```\n"
    msg += "\nPlease investigate and revise your patch."
    return msg


def build_diagnosis(fw):
    from condiag.diagnosis_prompt_builder import DiagnosisPromptBuilder, TrajectorySnapshot
    return DiagnosisPromptBuilder().build(fw, TrajectorySnapshot())


def inject_messages(r1_msgs_clean, fw_dict, diag=None):
    """Build the message sequence for a forked branch (same as PairedRunner._fork_from_checkpoint)."""
    msgs = deepcopy(r1_msgs_clean)
    # Tool response for last assistant tool_calls
    last = msgs[-1] if msgs else {}
    if last.get("role") == "assistant":
        tcs = last.get("tool_calls", [])
        if not tcs and last.get("extra", {}).get("actions"):
            tcs = [{"id": a.get("tool_call_id")} for a in last["extra"]["actions"] if a.get("tool_call_id")]
        for tc in tcs:
            tid = tc.get("id") or tc.get("tool_call_id", "")
            if tid and not any(m.get("role") == "tool" and m.get("tool_call_id") == tid for m in msgs):
                msgs.append({"role": "tool", "tool_call_id": tid, "content": "(output)"})
    # FW
    msgs.append({"role": "user", "content": format_witness(fw_dict)})
    # Diagnosis
    if diag:
        msgs.append({"role": "user", "content": diag})
    return msgs


def run_one_step(branch_name, messages, out_path):
    """Start a container, create agent, inject messages, call step() once, save result."""
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.litellm_model import LitellmModel
    from condiag.integrated_agent import ConDiagIntegratedAgent

    log.info("--- %s smoke start ---", branch_name)

    # Agent factory (same config as v2c_entry)
    image = "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-20428:latest"
    env = DockerEnvironment(image=image, cwd="/testbed", timeout=120)
    model = LitellmModel(model_name="deepseek/deepseek-v4-pro", model_kwargs={"temperature": 0.0, "max_tokens": 1024})
    agent = ConDiagIntegratedAgent(
        model=model, env=env,
        system_template="You are a software engineer. You can run bash commands. Read files with cat, edit with sed or python. When done, run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.",
        instance_template="{{task}}",
        step_limit=0, cost_limit=3.0, output_path=None,
    )
    # Override messages
    agent.messages = deepcopy(messages)
    agent.extra_template_vars["task"] = "smoke test placeholder"

    t0 = time.time()
    result = {"branch": branch_name, "n_calls_before": agent.n_calls, "ok": False, "error": "", "duration": 0, "step_result": {}}

    try:
        # step() = query() + execute_actions()
        step_out = agent.step()
        result["ok"] = True
        result["n_calls_after"] = agent.n_calls
        result["step_result"] = _summarize_step(step_out)
    except Exception as e:
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"
        result["n_calls_after"] = agent.n_calls
    finally:
        result["duration"] = time.time() - t0
        env.cleanup()
        if out_path:
            json.dump(result, open(out_path, "w"), indent=2)
        log.info("   %s -> %s (%.1fs)", branch_name, "OK" if result["ok"] else f"FAIL {result['error'][:80]}", result["duration"])

    return result


def _summarize_step(step_output):
    """Extract readable info from step() return value."""
    if not step_output:
        return {"n_outputs": 0}
    msgs = step_output if isinstance(step_output, list) else [step_output]
    actions = []
    for m in msgs:
        extra = m.get("extra", {})
        for act in extra.get("actions", []):
            actions.append({"action": act.get("action", ""), "tool_call_id": act.get("tool_call_id", "")[:20]})
    return {"n_messages": len(msgs), "actions": actions}


def main():
    SMOKE_DIR.mkdir(parents=True, exist_ok=True)
    log.info("=" * 60)
    log.info("V2c.1b API Smoke Test")
    log.info("=" * 60)

    # 1. Load R1 checkpoint messages
    cp_path = ARTIFACT_DIR / "round1" / "checkpoint.json"
    if not cp_path.exists():
        cp_path = ARTIFACT_DIR / "round1" / "trajectory.json"
    with open(cp_path) as f:
        cp = json.load(f)
    r1_msgs = [m for m in cp.get("messages", []) if m.get("role") != "exit"]
    r1_msgs = [m for m in r1_msgs if not (m.get("role") == "tool" and m.get("content") == "(output)")]
    log.info("R1 messages: %d, sha=%s", len(r1_msgs), sha(r1_msgs))

    # 2. Build FW + Diagnosis
    fw = build_fw(ARTIFACT_DIR / "round1" / "harness_eval.json")
    diag = build_diagnosis(fw)
    log.info("FW sha=%s, Diagnosis sha=%s", sha(fw), sha(diag))

    # 3. Construct SF/CD messages
    sf_msgs = inject_messages(r1_msgs, fw, diag=None)
    cd_msgs = inject_messages(r1_msgs, fw, diag=diag)
    log.info("SF messages: %d, CD messages: %d", len(sf_msgs), len(cd_msgs))

    # 4. Run one step each
    sf_out = run_one_step("sf", sf_msgs, SMOKE_DIR / "sf_smoke.json")
    cd_out = run_one_step("cd", cd_msgs, SMOKE_DIR / "condiag_smoke.json")

    # 5. Summary
    summary = {
        "instance": INSTANCE,
        "r1_messages_sha": sha(r1_msgs),
        "sf": {"ok": sf_out["ok"], "calls": sf_out.get("n_calls_after", 0) - sf_out.get("n_calls_before", 0),
               "error": sf_out.get("error", ""), "duration_s": round(sf_out["duration"], 2)},
        "cd": {"ok": cd_out["ok"], "calls": cd_out.get("n_calls_after", 0) - cd_out.get("n_calls_before", 0),
               "error": cd_out.get("error", ""), "duration_s": round(cd_out["duration"], 2)},
        "accepted": sf_out["ok"] and cd_out["ok"],
    }
    json.dump(summary, open(SMOKE_DIR / "smoke_summary.json", "w"), indent=2)
    log.info("\n=== SMOKE SUMMARY ===")
    log.info("SF: %s (%s)", "✅ ACCEPTED" if sf_out["ok"] else "❌ REJECTED", sf_out.get("error", "")[:80])
    log.info("CD: %s (%s)", "✅ ACCEPTED" if cd_out["ok"] else "❌ REJECTED", cd_out.get("error", "")[:80])
    log.info("Gate for pilot: %s", "✅ PASS" if summary["accepted"] else "❌ FAIL")


if __name__ == "__main__":
    main()
