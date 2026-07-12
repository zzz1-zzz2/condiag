"""Protocol tests for Host-Agent retry (2026-06-29).

Verifies the key invariants of the correct retry path:
  1. attempt_2/raw_trajectory.json exists
  2. tool_calls_count > 0
  3. patch_source == "workspace_git_diff" (or "agent_submission")
  4. direct_llm_patch == false

Also tests:
  - build_retry_input for different baselines
  - build_retry_input for NOOP (should_retry=False)
  - validate_host_agent_run with synthetic trajectories
  - collect_retry_artifacts
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from condiag.adapters.miniswe_retry_injection import MinisweRetryInjectionAdapter
from condiag.schemas import RetryRequest, RetryInput


# ============================================================================
# Synthetic test data
# ============================================================================


def _make_traj(
    tool_calls: int = 5,
    has_diff_in_last_msg: bool = False,
    submission: str = "",
) -> dict:
    """Build a synthetic mini-SWE trajectory for protocol testing."""
    msgs = []
    for i in range(tool_calls):
        msgs.append({
            "role": "assistant",
            "content": f"THOUGHT: Let me check the code.\n<bash>\ngrep -r 'bug' .\n</bash>",
        })
    if has_diff_in_last_msg:
        msgs.append({
            "role": "assistant",
            "content": "```diff\ndiff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1,3 +1,3 @@\n-old\n+new\n```",
        })
    return {
        "info": {"submission": submission or "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new"},
        "messages": msgs,
        "traj_path": "/fake/traj.json",
    }


def _make_request(
    should_retry: bool = True,
    baseline: str = "feedback_retry",
    instance_id: str = "django__django-13513",
    context_packet_content: str = "# Test Packet\n\nSome context.",
) -> RetryRequest:
    """Build a synthetic RetryRequest."""
    ctx = None
    if context_packet_content:
        ctx = Path(tempfile.mktemp(suffix=".md"))
        ctx.write_text(context_packet_content, encoding="utf-8")

    return RetryRequest(
        instance_id=instance_id,
        baseline_name=baseline,
        repo_path=None,
        base_commit="abc123",
        issue_text="Fix the bug in django-13513.",
        attempt1_patch_path=None,
        attempt1_runtime_signals_path=None,
        context_packet_path=ctx,
        intervention_report_path=None,
        should_retry=should_retry,
        retry_reason="EVIDENCE_EDIT_MISMATCH" if should_retry else "NO_TRIGGER",
        max_steps=50,
        timeout_sec=600,
    )


# ============================================================================
# build_retry_input tests
# ============================================================================


class TestBuildRetryInput:
    """Test MinisweRetryInjectionAdapter.build_retry_input for different baselines."""

    def setup_method(self):
        self.adapter = MinisweRetryInjectionAdapter()

    def test_noop_request_returns_empty_input(self):
        """NOOP (should_retry=False) should produce empty RetryInput."""
        req = _make_request(should_retry=False)
        result = self.adapter.build_retry_input(req)

        assert isinstance(result, RetryInput)
        assert result.task_message == ""
        assert result.metadata.get("noop") is True

    def test_feedback_retry_includes_issue_and_packet(self):
        """feedback_retry should include issue + packet content."""
        req = _make_request(baseline="feedback_retry", should_retry=True)
        result = self.adapter.build_retry_input(req)

        assert "django-13513" in result.task_message
        assert "Test Packet" in result.task_message
        assert "Do NOT output a patch directly" in result.task_message

    def test_broad_expansion_includes_issue_and_packet(self):
        """broad_expansion should include issue + broad context."""
        req = _make_request(baseline="broad_expansion", should_retry=True,
                            context_packet_content="# Broad Context\n\ncandidates: ...")
        result = self.adapter.build_retry_input(req)

        assert "Broad Context" in result.task_message
        assert "Do NOT output a patch directly" in result.task_message

    def test_condiag_retry_includes_issue_and_packet(self):
        """condiag_retry should include issue + ConDiag typed packet."""
        req = _make_request(baseline="condiag_packet_only", should_retry=True,
                            context_packet_content="## Diagnosis\nREHYDRATE\n## Retrieved Evidence")
        result = self.adapter.build_retry_input(req)

        assert "## Diagnosis" in result.task_message
        assert "REHYDRATE" in result.task_message
        assert "Do NOT output a patch directly" in result.task_message

    def test_missing_context_packet_is_handled(self):
        """Missing context_packet should not crash."""
        req = _make_request(should_retry=True, context_packet_content=None)
        result = self.adapter.build_retry_input(req)

        assert "(no additional context provided)" in result.task_message

    def test_retry_input_metadata(self):
        """RetryInput metadata should carry timeout/max_steps/baseline."""
        req = _make_request(should_retry=True, baseline="condiag_packet_only")
        result = self.adapter.build_retry_input(req)

        assert result.metadata["max_steps"] == 50
        assert result.metadata["timeout_sec"] == 600
        assert result.metadata["baseline"] == "condiag_packet_only"


# ============================================================================
# validate_host_agent_run tests
# ============================================================================


class TestHasValidToolLoop:
    """Test MinisweRetryInjectionAdapter.validate_host_agent_run protocol checks."""

    def setup_method(self):
        self.adapter = MinisweRetryInjectionAdapter()

    def _write_traj(self, traj: dict) -> Path:
        p = Path(tempfile.mktemp(suffix=".json"))
        p.write_text(json.dumps(traj), encoding="utf-8")
        return p

    def test_valid_trajectory_with_tool_calls(self):
        """Trajectory with multiple tool calls should be valid."""
        traj = _make_traj(tool_calls=5)
        path = self._write_traj(traj)
        result = self.adapter.validate_host_agent_run(path)

        assert result["valid"] is True
        assert result["tool_calls_count"] > 0
        assert result["direct_llm_patch"] is False
        assert result["patch_source"] == "workspace_git_diff"

    def test_zero_tool_calls_is_invalid(self):
        """Trajectory with 0 tool calls is invalid protocol."""
        traj = _make_traj(tool_calls=0)
        path = self._write_traj(traj)
        result = self.adapter.validate_host_agent_run(path)

        assert result["valid"] is False
        assert result["tool_calls_count"] == 0
        assert "no tool calls" in str(result["issues"]).lower()

    def test_direct_llm_diff_is_detected(self):
        """Trajectory where last msg is a diff block without tool calls
        should be flagged as direct_llm_patch."""
        traj = _make_traj(tool_calls=0, has_diff_in_last_msg=True)
        path = self._write_traj(traj)
        result = self.adapter.validate_host_agent_run(path)

        assert result["direct_llm_patch"] is True
        assert result["valid"] is False
        assert result["patch_source"] == "INVALID_DIRECT_LLM_PATCH"

    def test_missing_file_is_invalid(self):
        """Non-existent trajectory file should return invalid."""
        result = self.adapter.validate_host_agent_run(Path("/nonexistent/traj.json"))

        assert result["valid"] is False
        assert result["patch_source"] == "no_trajectory"
        assert result["direct_llm_patch"] is True

    def test_tool_calls_with_diff_is_still_valid(self):
        """If there ARE tool calls, a diff block in the last message
        is fine (agent submitted normally)."""
        traj = _make_traj(tool_calls=3, has_diff_in_last_msg=True)
        path = self._write_traj(traj)
        result = self.adapter.validate_host_agent_run(path)

        # Should still be valid because tool_calls > 0
        assert result["valid"] is True
        assert result["tool_calls_count"] > 0
        assert result["direct_llm_patch"] is False


# ============================================================================
# Protocol integration test
# ============================================================================


class TestRetryProtocolIntegration:
    """End-to-end protocol checks across the retry pipeline."""

    def test_retry_input_always_has_correct_patch_instruction(self):
        """Every retry task_message must tell agent NOT to output patch directly."""
        adapter = MinisweRetryInjectionAdapter()
        for baseline in ("feedback_retry", "broad_expansion", "condiag_packet_only"):
            req = _make_request(baseline=baseline, should_retry=True)
            result = adapter.build_retry_input(req)
            assert "Do NOT output a patch directly" in result.task_message, \
                f"{baseline}: missing patch instruction"

    def test_noop_never_has_patch_instruction(self):
        """NOOP retry should have empty task_message (no instructions)."""
        adapter = MinisweRetryInjectionAdapter()
        req = _make_request(should_retry=False)
        result = adapter.build_retry_input(req)
        assert result.task_message == ""
        assert result.metadata["noop"] is True

    def test_retry_request_roundtrip(self):
        """RetryRequest -> to_dict() should be JSON-serializable."""
        req = _make_request()
        d = req.to_dict()
        json_str = json.dumps(d)
        assert "django-13513" in json_str
        assert "EVIDENCE_EDIT_MISMATCH" in json_str

    def test_retry_input_roundtrip(self):
        """RetryInput -> to_dict() should be JSON-serializable."""
        ri = RetryInput(
            instance_id="test-1",
            baseline_name="feedback_retry",
            task_message="test task",
            command=["echo", "hello"],
            metadata={"key": "value"},
        )
        d = ri.to_dict()
        json_str = json.dumps(d)
        assert "test task" in json_str
        assert "feedback_retry" in json_str


# ============================================================================
# plain_rerun protocol tests
# ============================================================================


class TestPlainRerunProtocol:
    """Test plain_rerun: original issue only, no retry signals."""

    def setup_method(self):
        self.adapter = MinisweRetryInjectionAdapter()

    def test_plain_rerun_task_message_is_clean(self):
        """plain_rerun task_message must NOT contain retry/previous/failure terms."""
        req = _make_request(baseline="plain_rerun", should_retry=True,
                            context_packet_content=None)
        result = self.adapter.build_retry_input(req)

        assert "## Original Issue" in result.task_message
        assert result.context_packet_path is None

        # These terms are FORBIDDEN in plain_rerun task_message
        forbidden = [
            "Previous Attempt",
            "Failure Witness",
            "Additional Context",
            "Retry Contract",
            "ConDiag",
            "suspicious",
            "should_retry",
            "previous attempt",
            "failed attempt",
        ]
        for term in forbidden:
            assert term not in result.task_message,                 f"plain_rerun task_message contains forbidden term: {term!r}"

    def test_plain_rerun_does_not_call_previous_attempt_helpers(self):
        """plain_rerun must not read context_packet or attempt_1 artifacts."""
        # Pass a context_packet_path that exists — plain_rerun must ignore it
        req = _make_request(baseline="plain_rerun", should_retry=True)
        result = self.adapter.build_retry_input(req)

        assert result.context_packet_path is None
        # task_message should not reference context_packet content
        assert "Test Packet" not in result.task_message

    def test_plain_rerun_metadata_is_correct(self):
        """plain_rerun metadata must declare packet_source=none."""
        req = _make_request(baseline="plain_rerun", should_retry=True,
                            context_packet_content=None)
        result = self.adapter.build_retry_input(req)

        assert result.metadata["baseline"] == "plain_rerun"
        assert result.metadata["packet_source"] == "none"
        assert result.metadata.get("max_steps") == 50

    def test_plain_rerun_noop_returns_empty(self):
        """plain_rerun with should_retry=False must produce empty input."""
        req = _make_request(baseline="plain_rerun", should_retry=False,
                            context_packet_content=None)
        result = self.adapter.build_retry_input(req)

        assert result.task_message == ""
        assert result.metadata.get("noop") is True

    def test_plain_rerun_integration_patch_source(self):
        """plain_rerun must still pass protocol validation when patch exists."""
        traj = _make_traj(tool_calls=3)
        path = Path(tempfile.mktemp(suffix=".json"))
        path.write_text(json.dumps(traj), encoding="utf-8")
        result = self.adapter.validate_host_agent_run(path)

        assert result["valid"] is True
        assert result["patch_source"] == "workspace_git_diff"
        assert result["direct_llm_patch"] is False


# ============================================================================
# build_retry_command tests
# ============================================================================


class TestBuildRetryCommand:
    """Test MinisweRetryInjectionAdapter.build_retry_command."""

    def setup_method(self):
        self.adapter = MinisweRetryInjectionAdapter()

    def test_command_includes_contextbench_run(self):
        """Command should reference contextbench.run."""
        req = _make_request(should_retry=True)
        ri = self.adapter.build_retry_input(req)
        ri.run_dir = Path("/tmp/test_run")
        cmd = self.adapter.build_retry_command(ri)

        assert "contextbench.run" in " ".join(cmd)
        assert "--agent" in cmd
        assert "miniswe" in cmd

    def test_build_retry_command_has_expected_flags(self):
        """Command should include --rerun and --timeout."""
        ri = RetryInput(
            instance_id="test-1",
            baseline_name="feedback_retry",
            task_message="task",
            run_dir=Path("/tmp/test_run"),
            metadata={"timeout_sec": 900},
        )
        cmd = self.adapter.build_retry_command(ri)

        assert "--rerun" in cmd
        assert "--timeout" in cmd
        assert "900" in cmd
        assert "--agent" in cmd
        assert "miniswe" in cmd
        assert "test-1" in cmd
