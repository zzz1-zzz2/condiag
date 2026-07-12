"""Host-Agent Retry Runner — lightweight experiment orchestrator.

This is NOT a ConDiag Core method module.  It is an experiment script that
wires together:
  - case bundle (from packet_only runs)
  - RetryInjectionAdapter (ContextPacket -> attempt_2 input)
  - mini-SWE Host Agent attempt_2
  - artifact collection + protocol validation

Architecture boundary:
  - ConDiag Core stops at ContextPacketAssembler
  - This runner calls MinisweRetryInjectionAdapter to inject the packet
  - Official eval is ONLY called at the final evaluation-only plane

Usage:
    python -m experiments.host_agent_retry_runner \
        --instance django__django-13513 \
        --baseline feedback_retry \
        --mode smoke
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from condiag.adapters.base import get_adapter
from condiag.adapters.miniswe_retry_injection import MinisweRetryInjectionAdapter
from condiag.schemas import RetryRequest, RetryInput


# ============================================================================
# Path conventions
# ============================================================================


def _first_existing_path(*paths: str) -> Path:
    """Return the first path that exists on this filesystem, or the first arg."""
    for p in paths:
        q = Path(p)
        if q.exists():
            return q
    return Path(paths[0])


def _resolve_default_runs_root() -> Path:
    if os.environ.get("CONDIAG_RUNS_ROOT"):
        return Path(os.environ["CONDIAG_RUNS_ROOT"])
    return _first_existing_path(
        "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs",
        "/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs",
    )


def _resolve_default_manifest() -> Path:
    if os.environ.get("CONDIAG_MANIFEST"):
        return Path(os.environ["CONDIAG_MANIFEST"])
    return _first_existing_path(
        "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/manifest.csv",
        "/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/manifest.csv",
    )


DEFAULT_RUNS_ROOT = _resolve_default_runs_root()
DEFAULT_MANIFEST = _resolve_default_manifest()
CONTEXTBENCH_ROOT = Path(
    os.environ.get(
        "CONTEXTBENCH_ROOT",
        os.path.expanduser("~/condiag/ContextBench"),
    )
)

AGENT_FRAMEWORKS = CONTEXTBENCH_ROOT / "agent-frameworks"
MINISWE_SRC = (
    AGENT_FRAMEWORKS
    / "mini-swe-agent"
    / "multi-poly-pro-verified"
    / "mini-swe-agent"
    / "src"
)
MINISWE_CONFIG = (
    AGENT_FRAMEWORKS
    / "mini-swe-agent"
    / "multi-poly-pro-verified"
    / "configs"
    / "swebench_following_context.yaml"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# E1 contract rendering
# ---------------------------------------------------------------------------


def _render_e1_contract(instance_id: str) -> str:
    """Render the E1 CONDIAG RETRY CONTRACT block for *instance_id*.

    Reads the contract JSON from experiments/packet_consumption/e1_prompt_contracts/
    and formats it as a text block appended to the retry task message.
    Returns empty string if the contract file is not found.
    """
    contract_dir = Path(
        __file__).resolve().parent / "packet_consumption" / "e1_prompt_contracts"
    contract_file = contract_dir / f"{instance_id}.json"

    if not contract_file.is_file():
        # Fallback: try with double-underscore format
        contract_file = contract_dir / f"{instance_id.replace('__django__', '__')}.json"
    if not contract_file.is_file():
        return ""

    try:
        contract = json.loads(contract_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""

    meta = contract.get("meta", {})
    provenance = contract.get("provenance", [])
    required_actions = contract.get("required_actions", [])
    behavioral_objective = contract.get("behavioral_objective", "")
    forbidden = contract.get("forbidden", [])

    lines = [
        "\n\nCONDIAG RETRY CONTRACT — REQUIRED BEFORE EDITING",
        "",
        "Before making any code edit, you must complete the following required actions.",
        "If any required action cannot be completed, explain why.",
        "",
    ]

    # Provenance
    if provenance:
        lines.append("Provenance:")
        for p in provenance:
            src = p.get("source", "?")
            desc = p.get("description", "")
            lines.append(f"- {src}: {desc}")
        lines.append("")

    # Required actions
    if required_actions:
        lines.append("Required actions:")
        for ra in required_actions:
            action = ra.get("action", "")
            rtype = ra.get("type", "")
            prefix = {"inspect_test": "[INSPECT TEST]",
                      "inspect_file": "[INSPECT FILE]",
                      "reasoning": "[STATE CONSTRAINT]",
                      "analysis": "[ANALYZE]",
                      "boundary": "[STAY IN BOUNDARY]"}.get(rtype, "[DO]")
            lines.append(f"  {ra['id']}. {prefix} {action}")
        lines.append("")

    # Behavioral objective
    if behavioral_objective:
        lines.append(f"Behavior-level objective: {behavioral_objective}")
        lines.append("")

    # Forbidden
    if forbidden:
        lines.append("Forbidden:")
        for f_item in forbidden:
            lines.append(f"- {f_item}")
        lines.append("")

    lines.append(
        "Do not produce a patch before completing the required actions above."
    )

    return "\n".join(lines)


# ============================================================================
# RetryRequest builder (from packet_only run artifacts)
# ============================================================================


def _extract_issue_from_trajectory(traj_path: Path) -> str:
    """Extract PR description / issue text from trajectory first user message."""
    import json, re
    if not traj_path.is_file():
        return ""
    try:
        traj = json.loads(traj_path.read_text(encoding="utf-8"))
        for msg in traj.get("messages", []):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    # Extract between <pr_description> tags
                    m = re.search(r"<pr_description>\s*(.+?)\s*</pr_description>", content, re.DOTALL)
                    if m:
                        return m.group(1).strip()
                    return content[:500]
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            m = re.search(r"<pr_description>\s*(.+?)\s*</pr_description>", text, re.DOTALL)
                            if m:
                                return m.group(1).strip()
                            return text[:500]
    except Exception:
        pass
    return ""



def build_retry_request(
    instance_id: str,
    baseline: str,
    runs_root: Path = DEFAULT_RUNS_ROOT,
    manifest_csv: Optional[Path] = None,
    agent: str = "miniswe",
    max_steps: int = 50,
    timeout_sec: int = 1800,
    packet_source: Optional[str] = None,
) -> RetryRequest:
    """Build a RetryRequest from a packet_only run's artifacts.

    Reads:
      - <runs_root>/<agent>/<packet_source>/<instance>/intervention/*
      - <runs_root>/<agent>/base_miniswe/<instance>/attempt_1/*
      - manifest CSV for repo/base_commit/issue

    packet_source defaults to baseline, but can differ (e.g. condiag_retry
    reads its context_packet from condiag_packet_only directory).
    """
    # plain_rerun has no context packet, no packet_source override
    if baseline == "plain_rerun":
        pkt_src = "plain_rerun"
    else:
        pkt_src = packet_source or baseline
    packet_run = runs_root / agent / pkt_src / instance_id
    base_run = runs_root / agent / "base_miniswe" / instance_id

    # Load intervention report
    ireport_path = packet_run / "intervention" / "intervention_report.json"
    if ireport_path.is_file():
        ireport = json.loads(ireport_path.read_text(encoding="utf-8"))
        should_retry = bool(ireport.get("should_retry"))
        retry_reason = ireport.get("trigger_type", "")
    elif baseline == "plain_rerun":
        should_retry = True
        retry_reason = "PLAIN_RERUN"
    else:
        should_retry = False
        retry_reason = "no_intervention_report"

    # Paths
    ctx_pkt = packet_run / "intervention" / "context_packet.md"
    ctx_pkt = ctx_pkt if ctx_pkt.is_file() else None

    attempt1_patch = base_run / "attempt_1" / "patch.diff"
    attempt1_patch = attempt1_patch if attempt1_patch.is_file() else None

    attempt1_rs = base_run / "attempt_1" / "runtime_signals.json"
    attempt1_rs = attempt1_rs if attempt1_rs.is_file() else None

    # Manifest: repo / base_commit / issue
    repo_path = None
    base_commit = ""
    issue_text = ""

    if manifest_csv and manifest_csv.is_file():
        import csv
        with open(str(manifest_csv), "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("instance_id") == instance_id:
                    rp = row.get("repo_base_path") or ""
                    if rp and Path(rp).is_dir():
                        repo_path = Path(rp)
                    base_commit = row.get("base_commit") or ""
                    break

    # Issue text: try to get from manifest / SWE-bench
    if not issue_text and manifest_csv and manifest_csv.is_file():
        try:
            from experiments.manifest_builder import get_problem_statement
            issue = get_problem_statement(instance_id)
            if issue:
                issue_text = issue
        except ImportError:
            pass

    # Fallback: extract issue text from trajectory if still empty
    if not issue_text:
        traj_issue = _extract_issue_from_trajectory(base_run / "attempt_1" / "trajectory.json")
        if traj_issue:
            issue_text = traj_issue

    if not issue_text:
        issue_text = f"Fix the bug in {instance_id}."

    return RetryRequest(
        instance_id=instance_id,
        baseline_name=baseline,
        repo_path=repo_path,
        base_commit=base_commit,
        issue_text=issue_text,
        attempt1_patch_path=attempt1_patch,
        attempt1_runtime_signals_path=attempt1_rs,
        context_packet_path=ctx_pkt,
        intervention_report_path=ireport_path if ireport_path.is_file() else None,
        should_retry=should_retry,
        retry_reason=retry_reason,
        max_steps=max_steps,
        timeout_sec=timeout_sec,
    )


# ============================================================================
# Mini-SWE retry launcher
# ============================================================================


def _write_retry_wrapper_script(
    task_message: str,
    instance_id: str,
    output_dir: Path,
    config_path: Path = MINISWE_CONFIG,
) -> Path:
    """Write a standalone Python script that runs mini-SWE with a custom task.

    The script loads the original SWE-bench instance (for Docker image info),
    replaces the problem_statement with our retry task, and runs the agent.
    """
    script = textwrap.dedent(f"""\
        \"\"\"Auto-generated retry wrapper for {instance_id}.\"\"\"
        import json, sys, traceback
        from pathlib import Path

        from datasets import load_dataset
        import yaml

        from minisweagent.agents.interactive import InteractiveAgent
        from minisweagent.config import get_config_path
        from minisweagent.models import get_model
        from minisweagent.run.extra.swebench import get_sb_environment
        from minisweagent.run.utils.save import save_traj

        INSTANCE_ID = {instance_id!r}
        TASK_FILE = {str(output_dir / "retry_task.md")!r}
        OUTPUT = {str(output_dir / "traj.json")!r}
        CONFIG = {str(config_path)!r}
        SUBSET = "verified"
        SPLIT = "test"

        # Load original instance (for Docker image / env info)
        instances = {{
            inst["instance_id"]: inst
            for inst in load_dataset("princeton-nlp/SWE-bench_Verified", split=SPLIT)
        }}
        if INSTANCE_ID not in instances:
            print(f"ERROR: {{INSTANCE_ID}} not found in Verified dataset", file=sys.stderr)
            sys.exit(1)
        instance = instances[INSTANCE_ID]

        # Replace problem_statement with our retry task
        task = Path(TASK_FILE).read_text(encoding="utf-8")
        instance["problem_statement"] = task

        # Setup agent
        config_path = get_config_path(CONFIG)
        config = yaml.safe_load(config_path.read_text())
        env = get_sb_environment(config, instance)
        agent = InteractiveAgent(
            get_model(None, config.get("model", {{}})),
            env,
            **({{"mode": "yolo"}} | config.get("agent", {{}})),
        )

        # Workspace cleanliness: capture git state INSIDE the Docker container
        def _run_git_in_env(git_cmd):
            try:
                out = env.execute(git_cmd)
                return out.get("output", "").strip() if out.get("returncode") == 0 else ""
            except Exception:
                return ""

        run_before_head = _run_git_in_env("cd /testbed && git rev-parse HEAD")
        run_before_status = _run_git_in_env("cd /testbed && git status --porcelain")
        run_before_diff_stat = _run_git_in_env("cd /testbed && git diff --stat")
        workspace_clean_before = (run_before_status == "")

        exit_status, result = None, None
        try:
            exit_status, result = agent.run(task, explore_context=None)
        except Exception as e:
            exit_status, result = type(e).__name__, str(e)
            print(f"ERROR: {{e}}", file=sys.stderr)
        finally:
            run_after_status = _run_git_in_env("cd /testbed && git status --porcelain")
            # Capture git diff DIRECTLY — don''t rely on agent submission alone
            patch_text = _run_git_in_env("cd /testbed && git diff HEAD")
            patch_file = {str(output_dir / "patch.diff")!r}
            if patch_text:
                Path(patch_file).write_text(patch_text, encoding="utf-8")
            workspace_info = {{
                "run_before_head": run_before_head,
                "run_before_status": run_before_status,
                "run_before_diff_stat": run_before_diff_stat,
                "workspace_clean_before": workspace_clean_before,
                "run_after_status": run_after_status,
                "run_after_diff_chars": len(patch_text),
            }}
            extra_info = {{**workspace_info}}
            save_traj(agent, Path(OUTPUT),
                      exit_status=exit_status, result=result,
                      extra_info=extra_info)
    """)
    script_path = output_dir / "_retry_wrapper.py"
    script_path.write_text(script, encoding="utf-8")
    return script_path


def _clear_miniswe_output(output_dir: Path, instance_id: str) -> None:
    """Clear previous mini-SWE outputs for this instance to allow --rerun."""
    import shutil
    base = output_dir / "miniswe" / "Verified"
    if not base.exists():
        return
    for pattern in ["preds.json", "minisweagent.log"]:
        for path in base.glob(pattern):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
    for path in base.glob(f"{instance_id}*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def _capture_docker_state(label: str, output_dir: Path) -> str:
    """Snapshot `docker ps` into output_dir/docker_ps_<label>.txt. Returns path.

    Used pre-launch and at-timeout to diagnose container accumulation.
    Non-fatal: writes error message to file if docker unavailable.
    """
    out_file = output_dir / f"docker_ps_{label}.txt"
    try:
        r = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}\t{{.Image}}\t{{.Status}}\t{{.Names}}"],
            capture_output=True, text=True, timeout=10,
        )
        content = (
            f"=== docker ps ({label}) ===\n"
            f"returncode: {r.returncode}\n"
            f"--- stdout ---\n{r.stdout}\n"
            f"--- stderr ---\n{r.stderr}\n"
        )
        # Also capture docker stats (non-blocking, may fail)
        try:
            s = subprocess.run(
                ["docker", "stats", "--no-stream",
                 "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
                capture_output=True, text=True, timeout=15,
            )
            content += f"\n=== docker stats ({label}) ===\n{returncode: s.returncode}\n{s.stdout}\n{s.stderr}\n"
        except Exception as se:
            content += f"\n(docker stats failed: {se})\n"
        out_file.write_text(content, encoding="utf-8")
    except Exception as e:
        out_file.write_text(f"docker ps failed ({label}): {e}", encoding="utf-8")
    return str(out_file)


def launch_miniswe_retry(
    retry_input: RetryInput,
    instance_id: str,
    output_dir: Path,
    timeout_sec: int = 1800,
) -> dict:
    """Launch mini-SWE with a custom retry task.

    Writes the task_message to disk, generates a wrapper script that
    overrides the problem_statement, and runs it via Python.

    Returns {
        "ok": bool, "traj_path": str|None, "error": str|None,
        "timeout_stage": str|None, "timeout_seconds": int|None,
        "stdout_log_path": str|None, "stderr_log_path": str|None,
        "docker_state_path": str|None,
    }.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Write the retry task message
    task_file = output_dir / "retry_task.md"
    task_file.write_text(retry_input.task_message, encoding="utf-8")

    # 2. Write and run the wrapper script
    wrapper = _write_retry_wrapper_script(
        retry_input.task_message,
        instance_id,
        output_dir,
    )

    # 3. Build environment (inherit from current, ensure PYTHONPATH)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{MINISWE_SRC}:{env.get('PYTHONPATH', '')}"

    # 3b. Capture Docker state BEFORE launch (detect container accumulation)
    docker_pre_path = _capture_docker_state("pre_launch", output_dir)

    # 4. Run
    cmd = [sys.executable, str(wrapper)]
    try:
        result = subprocess.run(
            cmd,
            cwd=str(MINISWE_SRC),
            env=env,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as e:
        # Save partial output for forensic analysis
        # TimeoutExpired.stdout / .stderr hold partial output captured before timeout
        timeout_dir = output_dir
        (timeout_dir / "timeout_exception.txt").write_text(
            f"TimeoutExpired after {timeout_sec}s\n"
            f"cmd: {' '.join(cmd)}\n"
            f"cwd: {MINISWE_SRC}\n"
            f"timeout_seconds: {timeout_sec}\n"
            f"stdout_chars: {len(e.stdout) if e.stdout else 0}\n"
            f"stderr_chars: {len(e.stderr) if e.stderr else 0}\n"
            f"timeout_stage: subprocess_run\n",
            encoding="utf-8",
        )
        stdout_log = None
        stderr_log = None
        if e.stdout:
            stdout_log = str(timeout_dir / "timeout_stdout.log")
            # TimeoutExpired.stdout may be bytes even with text=True
            stdout_text = e.stdout if isinstance(e.stdout, str) else e.stdout.decode("utf-8", errors="replace")
            timeout_dir.joinpath("timeout_stdout.log").write_text(
                stdout_text, encoding="utf-8", errors="replace")
        if e.stderr:
            stderr_log = str(timeout_dir / "timeout_stderr.log")
            stderr_text = e.stderr if isinstance(e.stderr, str) else e.stderr.decode("utf-8", errors="replace")
            timeout_dir.joinpath("timeout_stderr.log").write_text(
                stderr_text, encoding="utf-8", errors="replace")
        # Capture Docker state AT timeout
        docker_at_path = _capture_docker_state("at_timeout", output_dir)
        return {
            "ok": False, "traj_path": None,
            "error": f"Timeout after {timeout_sec}s",
            "timeout_stage": "subprocess_run",
            "timeout_seconds": timeout_sec,
            "stdout_log_path": stdout_log,
            "stderr_log_path": stderr_log,
            "docker_state_path": docker_at_path,
            "docker_pre_state_path": docker_pre_path,
        }

    # 5. Non-timeout failure: also save stdout/stderr for diagnosis
    traj_path = output_dir / "traj.json"
    if result.returncode != 0 or not traj_path.is_file():
        # Save full stdout/stderr (not just stderr[:500])
        stdout_log = None
        stderr_log = None
        if result.stdout:
            stdout_log = str(output_dir / "process_stdout.log")
            output_dir.joinpath("process_stdout.log").write_text(
                result.stdout, encoding="utf-8", errors="replace")
        if result.stderr:
            stderr_log = str(output_dir / "process_stderr.log")
            output_dir.joinpath("process_stderr.log").write_text(
                result.stderr, encoding="utf-8", errors="replace")
        err = (result.stderr or "")[:500] if result.stderr else f"exit {result.returncode}"
        return {
            "ok": False, "traj_path": None, "error": err,
            "timeout_stage": None,
            "timeout_seconds": None,
            "stdout_log_path": stdout_log,
            "stderr_log_path": stderr_log,
            "docker_state_path": None,
            "docker_pre_state_path": docker_pre_path,
        }

    return {
        "ok": True, "traj_path": str(traj_path), "error": None,
        "timeout_stage": None,
        "timeout_seconds": None,
        "stdout_log_path": None,
        "stderr_log_path": None,
        "docker_state_path": None,
        "docker_pre_state_path": docker_pre_path,
    }


# ============================================================================
# Main runner
# ============================================================================


def run_host_agent_retry(
    instance_id: str,
    baseline: str,
    *,
    agent: str = "miniswe",
    runs_root: Optional[Path] = None,
    manifest_csv: Optional[Path] = None,
    out_root: Optional[Path] = None,
    mode: str = "smoke",
    timeout_sec: int = 1800,
    max_steps: int = 50,
) -> dict:
    """Run a Host-Agent retry for one instance × baseline.

    Flow:
      1. Load packet_only artifacts -> RetryRequest
      2. adapter.build_retry_input(request) -> RetryInput
      3. Write task_file, launch mini-SWE retry
      4. Collect trajectory, extract git diff
      5. Protocol check (has_valid_tool_loop)
      6. Write run_report.json / final_report.json

    Returns a run_report-like dict.
    """
    runs_root = Path(runs_root or DEFAULT_RUNS_ROOT)
    manifest_csv = Path(manifest_csv or DEFAULT_MANIFEST)
    out_root = Path(out_root or runs_root)

    agent_adapter = get_adapter(agent)
    if agent_adapter.status != "implemented":
        return {
            "status": "aborted",
            "error": f"agent '{agent}' status is '{agent_adapter.status}'",
        }
    retry_adapter = MinisweRetryInjectionAdapter()

    # 1. Build RetryRequest from packet_only artifacts
    if baseline == "condiag_retry":
        pkt_src = "condiag_packet_only"
    elif baseline == "condiag_retry_v2_alpha":
        pkt_src = "context_packet_v2_alpha"
    elif baseline == "packet_consumption_e1":
        pkt_src = "condiag_packet_only"
    else:
        pkt_src = None
    request = build_retry_request(
        instance_id=instance_id,
        baseline=baseline,
        runs_root=runs_root,
        manifest_csv=manifest_csv,
        agent=agent,
        max_steps=max_steps,
        timeout_sec=timeout_sec,
        packet_source=pkt_src,
    )

    run_dir = out_root / agent / baseline / instance_id
    run_dir.mkdir(parents=True, exist_ok=True)

    report = {
        "schema_version": "condiag.run_report.v0",
        "agent": agent,
        "baseline": baseline,
        "instance_id": instance_id,
        "packet_source": request.metadata.get("packet_source") if hasattr(request, "metadata") else None,
        "failure_witness_used": baseline not in ("plain_rerun",),
        "context_packet_used": baseline in ("condiag_retry", "condiag_packet_only", "condiag_retry_v2_alpha", "condiag_contract_retry"),
        "api_navigation_used": baseline in ("condiag_retry", "condiag_retry_v2_alpha"),
        "e1_contract_injected": baseline == "packet_consumption_e1",
        "mode": mode,
        "status": "started",
        "started_at": _now_iso(),
        "finished_at": None,
        "has_attempt_1": True,
        "has_intervention": True,
        "has_attempt_2": False,
        "has_final": False,
        "attempt_1_status": "completed",  # packet_only already ran
        "attempt_2_status": None,
        "final_source": None,
        "should_retry": request.should_retry,
        "retry_reason": request.retry_reason,
        "errors": [],
        "warnings": [],
    }

    # Save the request for debugging
    (run_dir / "retry_request.json").write_text(
        json.dumps(request.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 2. NOOP check
    if not request.should_retry:
        report["status"] = "completed_noop"
        report["attempt_2_status"] = "skipped_no_retry"
        report["final_source"] = "attempt_1"
        report["finished_at"] = _now_iso()
        _write_final_report(run_dir, request, attempt=1, reason="NOOP: not triggered", retry_no_change=False)
        (run_dir / "run_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return report

    # 3. Build RetryInput via retry injection adapter
    retry_input = retry_adapter.build_retry_input(request)
    retry_input.run_dir = run_dir / "attempt_2"
    retry_input.run_dir.mkdir(parents=True, exist_ok=True)

    # 3.b E1 contract injection: append CONDIAG RETRY CONTRACT to task_message
    if baseline == "packet_consumption_e1":
        e1_block = _render_e1_contract(instance_id)
        if e1_block:
            combined = retry_input.task_message + e1_block
            retry_input = RetryInput(
                instance_id=retry_input.instance_id,
                baseline_name=retry_input.baseline_name,
                repo_path=retry_input.repo_path,
                task_message=combined,
                context_packet_path=retry_input.context_packet_path,
                run_dir=retry_input.run_dir,
                command=retry_input.command,
                metadata=retry_input.metadata,
            )

    (run_dir / "retry_input.json").write_text(
        json.dumps(retry_input.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # 4. Launch mini-SWE retry
    launch_result = launch_miniswe_retry(
        retry_input,
        instance_id,
        run_dir / "attempt_2",
        timeout_sec=timeout_sec,
    )

    if not launch_result["ok"]:
        report["status"] = "aborted"
        report["errors"].append(f"launch failed: {launch_result['error']}")
        # Propagate timeout/diagnostic fields for forensic audit
        report["timeout_stage"] = launch_result.get("timeout_stage")
        report["timeout_seconds"] = launch_result.get("timeout_seconds")
        report["stdout_log_path"] = launch_result.get("stdout_log_path")
        report["stderr_log_path"] = launch_result.get("stderr_log_path")
        report["docker_state_path"] = launch_result.get("docker_state_path")
        report["docker_pre_state_path"] = launch_result.get("docker_pre_state_path")
        report["finished_at"] = _now_iso()
        (run_dir / "run_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return report

    report["has_attempt_2"] = True

    # 5. Collect artifacts
    traj_path = Path(launch_result["traj_path"])
    attempt_2_dir = run_dir / "attempt_2"

    # Copy trajectory
    raw_traj_path = attempt_2_dir / "raw_trajectory.json"
    raw_traj_path.write_text(traj_path.read_text(encoding="utf-8"))

    # Extract patch — prefer direct git diff (captured by wrapper), fallback to
    # agent submission (which can be lost on EOFError / early termination).
    try:
        with traj_path.open("r", encoding="utf-8") as f:
            traj_data = json.load(f)

        # Primary: direct git diff captured by wrapper (written to patch.diff)
        wrapper_patch_path = attempt_2_dir / "patch.diff"
        if wrapper_patch_path.is_file() and wrapper_patch_path.stat().st_size > 0:
            submission = wrapper_patch_path.read_text(encoding="utf-8")
            patch_source = "workspace_git_diff"
        else:
            # Fallback: agent submission
            submission = (traj_data.get("info") or {}).get("submission") or ""
            if submission:
                patch_path = attempt_2_dir / "patch.diff"
                patch_path.write_text(submission, encoding="utf-8")
                patch_source = "agent_submission"
            else:
                patch_source = "none"

        patch_chars = len(submission)
    except Exception as e:
        patch_source = f"error: {e}"
        patch_chars = 0

    # Parse runtime_signals from trajectory
    try:
        rs_dict = agent_adapter.extract_runtime_signals(traj_path.parent, instance_id)
        rs_path = attempt_2_dir / "runtime_signals.json"
        rs_path.write_text(
            json.dumps(rs_dict, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        report["warnings"].append(f"runtime_signals parse failed: {e}")

    # 6. Protocol check
    protocol = retry_adapter.validate_host_agent_run(raw_traj_path)
    report["protocol_check"] = protocol

    if not protocol.get("valid"):
        report["warnings"].append(
            f"INVALID_RETRY_PROTOCOL: {protocol.get('issues', [])}"
        )

    # Extract workspace cleanliness from trajectory info.
    # save_traj() flattens extra_info into info at top level, so read directly.
    info = traj_data.get("info") or {}
    ws_info = info if isinstance(info, dict) else {}
    if isinstance(ws_info, dict) and ws_info:
        repo_base_commit = ws_info.get("run_before_head", "")
        workspace_clean_before = ws_info.get("workspace_clean_before", None)
        run_before_status = ws_info.get("run_before_status", "")
        run_after_status = ws_info.get("run_after_status", "")
        changed_files = []
        for line in run_after_status.splitlines():
            if line.strip():
                changed_files.append(line.strip())
    else:
        repo_base_commit = ""
        workspace_clean_before = None
        run_before_status = ""
        run_after_status = ""
        changed_files = []

    # 7. Attempt report
    has_patch = patch_chars > 0
    no_change_reason = ""
    if not has_patch:
        # Determine why agent didn't produce a patch
        submission_check = (traj_data.get("info") or {}).get("submission") or ""
        exit_status_check = (traj_data.get("info") or {}).get("exit_status") or ""
        if exit_status_check in ("no_change", "submitted_no_change", "no_fix_needed"):
            no_change_reason = "agent declared no change needed"
        elif not submission_check:
            no_change_reason = "no submission in trajectory"
        else:
            no_change_reason = "empty submission"

    attempt_report = {
        "schema_version": "condiag.attempt_report.v0",
        "attempt": "attempt_2",
        "instance_id": instance_id,
        "agent": agent,
        "baseline": baseline,
        "packet_source": request.metadata.get("packet_source") if hasattr(request, "metadata") else None,
        "failure_witness_used": baseline not in ("plain_rerun",),
        "context_packet_used": baseline in ("condiag_retry", "condiag_packet_only", "condiag_retry_v2_alpha", "condiag_contract_retry"),
        "api_navigation_used": baseline in ("condiag_retry", "condiag_retry_v2_alpha"),
        "e1_contract_injected": baseline == "packet_consumption_e1",
        "source": "host_agent_retry",
        "repo_base_commit": repo_base_commit,
        "workspace_clean_before_run": workspace_clean_before,
        "run_before_status": run_before_status,
        "run_after_status": run_after_status,
        "patch_source": patch_source,
        "patch_chars": patch_chars,
        "patch_bytes": patch_chars,
        "has_patch": has_patch,
        "changed_files": changed_files,
        "changed_files_count": len(changed_files),
        "tool_calls_count": protocol.get("tool_calls_count", 0),
        "valid_protocol": protocol.get("valid", False),
        "retry_contract": "failed_or_suspicious_attempt",
        "retry_no_change": not has_patch and protocol.get("valid", False),
        "no_patch_reason": no_change_reason if not has_patch else "",
        "official_retry_success_candidate": has_patch and protocol.get("valid", False),
        "started_at": report["started_at"],
        "finished_at": _now_iso(),
    }
    (attempt_2_dir / "attempt_report.json").write_text(
        json.dumps(attempt_report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # 8. Final report
    if has_patch and protocol.get("valid"):
        final_attempt = 2
        final_reason = "attempt_2 produced valid patch via agent tool loop"
        _copy_to_final(attempt_2_dir, run_dir / "final")
    elif protocol.get("valid") and not has_patch:
        # Agent ran tool loop but produced no patch — retry_no_change
        final_attempt = 1
        final_reason = "attempt_2 valid protocol but no patch (retry_no_change); fallback to attempt_1"
        report["warnings"].append("retry_no_change: agent ran tool loop but produced no patch")
        base_run = runs_root / agent / "base_miniswe" / instance_id
        _copy_to_final(base_run / "attempt_1", run_dir / "final")
    else:
        final_attempt = 1
        final_reason = "attempt_2 invalid protocol; fallback to attempt_1"
        base_run = runs_root / agent / "base_miniswe" / instance_id
        _copy_to_final(base_run / "attempt_1", run_dir / "final")

    retry_no_change = (not has_patch and protocol.get("valid", False))
    _write_final_report(run_dir, request, attempt=final_attempt, reason=final_reason,
                        retry_no_change=retry_no_change)

    report["status"] = "completed"
    report["attempt_2_status"] = "completed" if launch_result["ok"] else "failed"
    report["final_source"] = f"attempt_{final_attempt}"
    report["has_final"] = True
    report["retry_no_change"] = (not has_patch and protocol.get("valid", False))
    report["finished_at"] = _now_iso()

    (run_dir / "run_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


# ============================================================================
# Helpers
# ============================================================================


def _copy_to_final(src_dir: Path, final_dir: Path) -> None:
    """Copy patch.diff and runtime_signals.json from src to final."""
    import shutil
    final_dir.mkdir(parents=True, exist_ok=True)
    for name in ["patch.diff", "runtime_signals.json"]:
        src = src_dir / name
        if src.is_file():
            shutil.copyfile(src, final_dir / name)


def _write_final_report(
    run_dir: Path, request: RetryRequest, attempt: int, reason: str,
    retry_no_change: bool = False,
) -> None:
    """Write final/final_report.json."""
    final_dir = run_dir / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    patch_path = final_dir / "patch.diff"
    report = {
        "schema_version": "condiag.final_report.v0",
        "instance_id": request.instance_id,
        "agent": "miniswe",
        "baseline": request.baseline_name,
        "mode": "retry",
        "selected_attempt": attempt,
        "selected_attempt_dir": f"attempt_{attempt}",
        "selection_reason": reason,
        "has_final_patch": patch_path.is_file() and patch_path.stat().st_size > 0,
        "final_patch_chars": patch_path.stat().st_size if patch_path.is_file() else 0,
        "retry_no_change": retry_no_change,
        "finalized_at": _now_iso(),
    }
    (final_dir / "final_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )


# ============================================================================
# CLI
# ============================================================================


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Host-Agent Retry Runner — correct retry via mini-SWE tool loop",
    )
    parser.add_argument(
        "--instance", required=True,
        help="instance_id (e.g. django__django-13513)",
    )
    parser.add_argument(
        "--baseline", required=True,
        choices=["plain_rerun", "feedback_retry", "broad_expansion", "condiag_retry", "condiag_retry_v2_alpha", "packet_consumption_e1", "condiag_contract_retry"],
        help="baseline to run (plain_rerun = original issue only; condiag_retry reads packet from condiag_packet_only)",
    )
    parser.add_argument(
        "--agent", default="miniswe",
        help="agent name (default: miniswe)",
    )
    parser.add_argument(
        "--runs-root",
        default=str(DEFAULT_RUNS_ROOT),
        help="root dir of packet_only runs",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="path to manifest CSV",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="output root (default: same as --runs-root)",
    )
    parser.add_argument(
        "--mode", default="smoke",
        choices=["smoke", "full"],
        help="run mode",
    )
    parser.add_argument(
        "--timeout", type=int, default=1800,
        help="timeout per instance in seconds",
    )
    parser.add_argument(
        "--max-steps", type=int, default=50,
        help="max agent steps",
    )

    args = parser.parse_args(argv)

    print(f"=== host_agent_retry_runner ===")
    print(f"  instance:  {args.instance}")
    print(f"  baseline:  {args.baseline}")
    print(f"  agent:     {args.agent}")
    print(f"  runs_root: {args.runs_root}")
    print(f"  mode:      {args.mode}")
    print()

    result = run_host_agent_retry(
        instance_id=args.instance,
        baseline=args.baseline,
        agent=args.agent,
        runs_root=Path(args.runs_root),
        manifest_csv=Path(args.manifest) if args.manifest else None,
        out_root=Path(args.out) if args.out else None,
        mode=args.mode,
        timeout_sec=args.timeout,
        max_steps=args.max_steps,
    )

    print(f"  status:        {result.get('status')}")
    print(f"  should_retry:  {result.get('should_retry')}")
    print(f"  final_source:  {result.get('final_source')}")
    protocol = result.get("protocol_check") or {}
    if protocol:
        print(f"  tool_calls:    {protocol.get('tool_calls_count', 0)}")
        print(f"  valid_protocol: {protocol.get('valid', False)}")
    if result.get("retry_no_change"):
        print(f"  retry_no_change: True (agent ran tool loop but produced no patch)")
    if result.get("errors"):
        for e in result["errors"]:
            print(f"  ERROR: {e}")
    if result.get("warnings"):
        for w in result["warnings"]:
            print(f"  WARN: {w}")

    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
