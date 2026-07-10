"""Mini-SWE Retry Injection Adapter (v0.2, 2026-06-29).

Architecture boundary:
  This adapter sits on the RIGHT side of the Runtime Plane:
    ContextPacket -> Host Agent Attempt 2 input

  It does NOT do:
    - Direct LLM calls
    - Patch generation / diff extraction from assistant text
    - Gold / eval / ContextBench metrics
    - ConDiag diagnosis or retrieval

  It ONLY does:
    - Translate ContextPacket + original issue + attempt_1 summary
      into a mini-SWE attempt_2 task message
    - Generate the CLI command to launch mini-SWE normally
    - Collect attempt_2 artifacts (trajectory, git diff)
    - Validate that the trajectory shows real Host Agent tool use
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from ..schemas import RetryRequest, RetryInput


class MinisweRetryInjectionAdapter:
    """Translate ConDiag ContextPacket into mini-SWE attempt_2 input.

    This is the retry-side counterpart of MinisweAdapter (which reads
    attempt_1 trajectory).  Together they form the complete mini-SWE
    adapter layer, but with clearly separated responsibilities:
      - MinisweAdapter:           attempt_1 -> runtime_signals
      - MinisweRetryInjectionAdapter:  context_packet -> attempt_2 input
    """

    def build_retry_input(self, request: RetryRequest) -> RetryInput:
        """Translate RetryRequest into a mini-SWE retry task message.

        Handles all baseline types:
          - NOOP / should_retry=False:  empty task (runner skips)
          - feedback_retry:             issue + test feedback + patch summary
          - broad_expansion:            issue + broad context packet
          - condiag_retry:              issue + ConDiag typed packet

        The task message includes a retry contract that prevents the agent
        from incorrectly no-opping when the previous attempt was classified
        as failed or suspicious.
        """
        if not request.should_retry:
            return RetryInput(
                instance_id=request.instance_id,
                baseline_name=request.baseline_name,
                repo_path=request.repo_path,
                task_message="",
                context_packet_path=request.context_packet_path,
                metadata={"noop": True, "reason": request.retry_reason},
            )

        # Plain rerun: original issue ONLY, no previous attempt, no retry contract
        if request.baseline_name == "plain_rerun":
            issue_section = request.issue_text or f"Fix the bug in {request.instance_id}."
            task_message = f"""## Original Issue

{issue_section}

Please inspect the repository, locate the root cause, make the necessary
changes, and submit using the standard submission mechanism."""
            return RetryInput(
                instance_id=request.instance_id,
                baseline_name=request.baseline_name,
                repo_path=request.repo_path,
                task_message=task_message,
                context_packet_path=None,
                metadata={
                    "baseline": "plain_rerun",
                    "packet_source": "none",
                    "max_steps": request.max_steps,
                    "timeout_sec": request.timeout_sec,
                },
            )

        # Read context_packet if available
        packet_content = ""
        if request.context_packet_path and request.context_packet_path.is_file():
            packet_content = request.context_packet_path.read_text(encoding="utf-8")

        # Build issue section
        issue_section = request.issue_text or f"Fix the bug in {request.instance_id}."

        # Build previous attempt summary (with runtime-visible failure signals)
        prev_section = _build_previous_attempt_section(request)

        # Build retry contract based on intervention report
        retry_contract = _build_retry_contract(request)

        # Read intervention report for richer context (trigger_type, diagnosis)
        intervention_context = _build_intervention_context(request)

        task_message = f"""## Original Issue

{issue_section}

## Previous Attempt (Attempt 1)

{prev_section}

{intervention_context}

## Additional Context for This Retry

{packet_content if packet_content else '(no additional context provided)'}

---

{retry_contract}
"""

        return RetryInput(
            instance_id=request.instance_id,
            baseline_name=request.baseline_name,
            repo_path=request.repo_path,
            task_message=task_message,
            context_packet_path=request.context_packet_path,
            command=[],
            metadata={
                "max_steps": request.max_steps,
                "timeout_sec": request.timeout_sec,
                "base_commit": request.base_commit,
                "baseline": request.baseline_name,
                "trigger_type": request.retry_reason,
            },
        )

    def build_retry_command(self, retry_input: RetryInput) -> list[str]:
        """Generate the CLI command to launch mini-SWE via contextbench.run.

        The command pattern follows the existing scripts/run_miniswe_smoke.sh:
            python -m contextbench.run --agent miniswe --bench Verified
            --instances <id> --output <dir> --rerun --timeout <sec>
        """
        run_dir = retry_input.run_dir or Path(".")

        return [
            "python", "-m", "contextbench.run",
            "--agent", "miniswe",
            "--bench", "Verified",
            "--instances", retry_input.instance_id,
            "--output", str(run_dir),
            "--rerun",
            "--timeout", str(retry_input.metadata.get("timeout_sec", 1800)),
        ]

    def collect_attempt2_artifacts(self, run_dir: Path, repo_dir: Path) -> dict:
        """Collect attempt_2 artifacts after mini-SWE finishes.

        Produces:
          - attempt_2/raw_trajectory.json  (find and copy from mini-SWE output)
          - attempt_2/patch.diff           (git diff from workspace)
          - attempt_2/runtime_signals.json (parsed from trajectory)

        patch.diff MUST come from workspace git diff, NOT from:
          - assistant message ```diff block
          - LLM output full file
          - normalizer-corrected patch

        Returns a dict with collection status.
        """
        attempt_2 = run_dir / "attempt_2"
        attempt_2.mkdir(parents=True, exist_ok=True)

        result = {
            "trajectory_path": None,
            "patch_path": None,
            "patch_source": "none",
            "runtime_signals_path": None,
            "has_tool_use": False,
            "error": None,
        }

        # 1. Find and copy trajectory from mini-SWE output
        traj_path = _find_traj_in_output(run_dir, attempt_2)
        if traj_path:
            result["trajectory_path"] = str(traj_path)
        else:
            result["error"] = "no trajectory found in mini-SWE output"
            return result

        # 2. Extract patch via git diff from workspace
        if repo_dir and repo_dir.is_dir():
            try:
                diff_output = subprocess.check_output(
                    ["git", "-C", str(repo_dir), "diff"],
                    text=True, timeout=30,
                )
                patch_path = attempt_2 / "patch.diff"
                patch_path.write_text(diff_output, encoding="utf-8")
                result["patch_path"] = str(patch_path)
                result["patch_source"] = "workspace_git_diff"
            except subprocess.CalledProcessError as e:
                result["error"] = f"git diff failed: {e}"
            except subprocess.TimeoutExpired:
                result["error"] = "git diff timed out"
        else:
            result["error"] = f"repo_dir not accessible: {repo_dir}"

        # 3. Parse runtime_signals from trajectory
        if traj_path:
            try:
                from .miniswe import MinisweAdapter
                adapter = MinisweAdapter()
                rs = adapter.extract_runtime_signals(traj_path.parent, None)
                rs_path = attempt_2 / "runtime_signals.json"
                rs_path.write_text(
                    json.dumps(rs, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                result["runtime_signals_path"] = str(rs_path)
            except Exception as e:
                if not result["error"]:
                    result["error"] = f"runtime_signals parse failed: {e}"

        # 4. Protocol check
        protocol = self.validate_host_agent_run(traj_path)
        result["has_tool_use"] = protocol.get("valid", False)
        result["tool_calls_count"] = protocol.get("tool_calls_count", 0)
        if protocol.get("direct_llm_patch"):
            result["patch_source"] = "INVALID_DIRECT_LLM_PATCH"

        return result

    def validate_host_agent_run(self, trajectory_path: Path) -> dict:
        """Hard gate: check that the trajectory shows real agent tool use.

        A valid mini-SWE trajectory must have:
          - At least one assistant message with tool calls
          - Shell commands or file operations in the tool calls
          - The submission should NOT be a raw diff dumped by the LLM

        If this check fails, the run is marked INVALID_RETRY_PROTOCOL and
        must NOT enter official repair-rate statistics.

        Returns:
          {"valid": bool, "tool_calls_count": int, "patch_source": str,
           "issues": [...], "direct_llm_patch": bool}
        """
        if not trajectory_path.is_file():
            return {
                "valid": False,
                "tool_calls_count": 0,
                "patch_source": "no_trajectory",
                "issues": ["trajectory file not found"],
                "direct_llm_patch": True,
            }

        try:
            with trajectory_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            return {
                "valid": False,
                "tool_calls_count": 0,
                "patch_source": "parse_error",
                "issues": [f"trajectory parse error: {e}"],
                "direct_llm_patch": True,
            }

        msgs = data.get("messages") or []
        tool_calls_count = 0
        has_shell = False
        has_file_op = False
        direct_llm_suspect = False
        issues = []

        for m in msgs:
            if m.get("role") != "assistant":
                continue
            content = m.get("content") or ""
            # mini-SWE tool calls are in the content as <bash> or function calls
            if "<bash>" in content or "tool_calls" in str(m):
                tool_calls_count += 1
            if "grep" in content or "cat " in content or "find " in content:
                has_shell = True
            if "sed " in content or "python " in content or "edit" in content.lower():
                has_file_op = True

        # Heuristic: if the last assistant message contains ```diff, suspect
        # direct LLM patch output (bypassing agent tool loop)
        for m in reversed(msgs):
            if m.get("role") == "assistant":
                content = m.get("content") or ""
                if "```diff" in content or "diff --git" in content:
                    if tool_calls_count == 0:
                        direct_llm_suspect = True
                        issues.append(
                            "last assistant message contains diff block "
                            "but trajectory has zero tool calls — "
                            "likely direct LLM patch, not agent tool loop"
                        )
                break

        if tool_calls_count == 0:
            issues.append("no tool calls found in trajectory")
        if not has_shell:
            issues.append("no shell commands (grep/cat/find) found")

        valid = tool_calls_count > 0 and not direct_llm_suspect

        return {
            "valid": valid,
            "tool_calls_count": tool_calls_count,
            "patch_source": "workspace_git_diff" if not direct_llm_suspect else "INVALID_DIRECT_LLM_PATCH",
            "has_shell_commands": has_shell,
            "has_file_operations": has_file_op,
            "issues": issues,
            "direct_llm_patch": direct_llm_suspect,
        }


# =========================================================================
# Helpers
# =========================================================================


def _find_traj_in_output(run_dir: Path, attempt_2_dir: Path) -> Optional[Path]:
    """Find the traj.json produced by mini-SWE and copy to attempt_2/.

    ContextBench outputs traj.json to <output>/<instance_id>.traj.json or
    <output>/traj.json.  We copy it to attempt_2/raw_trajectory.json.
    """
    candidates = [
        run_dir / "traj.json",
        run_dir / "miniswe" / "traj.json",
    ]
    candidates.extend(sorted(run_dir.glob("*.traj.json")))

    for src in candidates:
        if src.is_file() and src != attempt_2_dir / "raw_trajectory.json":
            dst = attempt_2_dir / "raw_trajectory.json"
            dst.write_text(src.read_text(encoding="utf-8"))
            return dst
    return None


def _build_previous_attempt_section(request: RetryRequest) -> str:
    """Build a summary of attempt_1 for the retry task message.

    Includes runtime-visible failure signals only — NO oracle/eval data.
    """
    parts = []

    # Patch summary
    if request.attempt1_patch_path and request.attempt1_patch_path.is_file():
        patch_text = request.attempt1_patch_path.read_text(encoding="utf-8", errors="ignore")
        files = []
        added = removed = 0
        for line in patch_text.splitlines():
            if line.startswith("diff --git"):
                parts_line = line.split()
                if len(parts_line) >= 4:
                    files.append(parts_line[-1].lstrip("b/"))
            elif line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        if files:
            parts.append(f"Edited files: {', '.join(files[:8])}")
            parts.append(f"Changes: +{added} / -{removed} lines")
        else:
            parts.append("(no patch produced in attempt 1)")
    else:
        parts.append("(no patch file found for attempt 1)")

    # Runtime signals summary
    if request.attempt1_runtime_signals_path and request.attempt1_runtime_signals_path.is_file():
        try:
            rs = json.loads(request.attempt1_runtime_signals_path.read_text(encoding="utf-8"))
            exit_status = rs.get("exit_status", "unknown")
            parts.append(f"Exit status: {exit_status}")
            test_failures = rs.get("test_failures_count", 0)
            if test_failures:
                parts.append(f"Test failures: {test_failures}")
                # Include sample failure output if available (runtime-visible only)
                failure_samples = rs.get("test_failures") or []
                if failure_samples:
                    parts.append(f"Failed tests: {', '.join(failure_samples[:5])}")
            # Indicate whether tests were run at all
            test_commands = rs.get("test_commands") or []
            test_runs_count = rs.get("test_runs_count", 0)
            if test_runs_count > 0:
                parts.append(f"Test runs: {test_runs_count}")
            # Flag if submitted without running tests
            if rs.get("submitted_without_tests"):
                parts.append("WARNING: agent submitted without running tests")
        except Exception:
            pass

    if not parts:
        parts.append("(no attempt_1 summary available)")

    return "\n".join(f"- {p}" for p in parts)


def _build_retry_contract(request: RetryRequest) -> str:
    """Build the retry contract — hard instructions that prevent incorrect no-op.

    This is the key guard against agents that inspect the workspace, find
    some passing tests, and conclude "no fix needed" when the previous
    attempt was actually classified as failed or suspicious.
    """
    trigger_type = request.retry_reason or "FAILED_OR_SUSPICIOUS"

    return f"""## Retry Contract

**This is a retry after a previous attempt classified as: `{trigger_type}`.**

The previous attempt was flagged because it likely did NOT fully resolve the
issue.  Do NOT treat this as a confirmation run to verify current repository
state.  Do NOT conclude the issue is already fixed just because one ad-hoc
test passes.

**Your task:**
1. Read the "Additional Context" section above — it contains evidence about
   what the previous attempt may have missed.
2. Inspect the repository using your normal tools (grep, cat, find, python,
   pytest) to locate the root cause.
3. Make a minimal, targeted fix to the workspace files.
4. Validate your changes with the relevant tests.
5. Submit using your standard submission mechanism.

**Critical rules:**
- Do NOT output a patch directly.  Modify workspace files and submit normally.
- Do NOT declare "no change needed" unless you have thoroughly verified the
  fix against the specific tests named in the original issue.
- A single passing ad-hoc test is NOT sufficient evidence that the issue is
  resolved when the previous attempt was already flagged as suspicious.
- The final patch will be collected from `git diff` after your run completes.
- If you truly cannot find anything to fix after thorough investigation,
  explain exactly which tests you ran and why they prove the issue is fixed."""


def _build_intervention_context(request: RetryRequest) -> str:
    """Build a section summarizing why retry was triggered.

    Reads the intervention_report for trigger_type, trigger_reasons,
    and (for condiag) diagnosis info.  Only includes runtime-visible data.
    """
    if not request.intervention_report_path:
        return ""

    if not request.intervention_report_path.is_file():
        return ""

    try:
        ireport = json.loads(request.intervention_report_path.read_text(encoding="utf-8"))
    except Exception:
        return ""

    lines = ["## Why This Retry Was Triggered"]
    lines.append(f"- Trigger: **{ireport.get('trigger_type', 'UNKNOWN')}**")

    reasons = ireport.get("trigger_reason") or []
    if reasons:
        for r in reasons:
            lines.append(f"  - {r}")

    # ConDiag-specific diagnosis (runtime-visible only, no oracle data)
    diagnosis = ireport.get("diagnosis") or {}
    if diagnosis:
        pathology = diagnosis.get("pathology", "")
        retry_intent = diagnosis.get("retry_intent", "")
        if pathology:
            lines.append(f"- Pathology: {pathology}")
        if retry_intent:
            lines.append(f"- Retry intent: {retry_intent}")

    # Packet kind for context
    packet_kind = ireport.get("context_packet_kind", "")
    if packet_kind:
        lines.append(f"- Context type: {packet_kind}")

    return "\n".join(lines) + "\n"
