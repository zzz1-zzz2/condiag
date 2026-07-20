"""Lightweight test for branch_runner — verifies agent creation, limits, restore."""
from __future__ import annotations

from unittest.mock import MagicMock

from condiag.branch_runner import (
    RestoreResult,
    run_branch,
    restore_workspace,
)


class MockAgent:
    """Minimal agent stub that exercises the run_branch loop."""

    def __init__(self):
        self.config = type("obj", (object,), {"step_limit": 0})
        self.messages = []
        self.n_calls = 0
        self.cost = 0.0
        self.n_consecutive_format_errors = 0
        self._start_time = 0
        self.env = MagicMock()
        self.env.execute.return_value = {"output": "", "returncode": 0}
        self.env.container_id = "mock_container"

    def add_messages(self, *msgs):
        self.messages.extend(msgs)
        return list(msgs)

    def step(self):
        raise Exception("mock_agent_should_not_step")

    def serialize(self):
        return {"messages": self.messages, "info": {"config": {"model": {"model_kwargs": {}}}}}

    def handle_uncaught_exception(self, e):
        return self.add_messages({"role": "exit", "content": str(e)})


class TestBranchRunner:
    def test_agent_is_defined(self):
        """If this fails, agent is referenced before assignment (NameError)."""
        result = run_branch(
            agent_factory=lambda: MockAgent(),
            checkpoint_messages=[],
            base_commit="test",
            task="test task",
            r1_n_calls=5,
            r1_cost=0.5,
            failure_witness=None,
            diagnosis=None,
            mode="sf",
        )
        assert result is not None
        assert result.termination_reason != ""

    def test_accepts_protocol_config(self):
        from condiag.agent.config import RevisionProtocolConfig
        result = run_branch(
            agent_factory=lambda: MockAgent(),
            checkpoint_messages=[],
            base_commit="test",
            task="test task",
            r1_n_calls=5,
            r1_cost=0.5,
            failure_witness=None,
            diagnosis=None,
            mode="sf",
            protocol_config=RevisionProtocolConfig(),
        )
        assert result is not None

    def test_restore_in_branch_result(self):
        """BranchResult should contain restore_result and workspace_sha."""
        result = run_branch(
            agent_factory=lambda: MockAgent(),
            checkpoint_messages=[],
            base_commit="test",
            task="test task",
            r1_n_calls=5,
            r1_cost=0.5,
            failure_witness=None,
            diagnosis=None,
            mode="sf",
        )
        assert hasattr(result, "restore_result")
        assert hasattr(result, "workspace_sha_before_first_step")


class TestRestoreWorkspace:
    def test_no_container_id(self):
        agent = MockAgent()
        agent.env.container_id = ""
        r = restore_workspace(agent, None, "test")
        assert not r.ok
        assert "no_container_id" in r.reason

    def test_head_mismatch(self):
        agent = MockAgent()
        agent.env.execute.return_value = {"output": "wrong_sha", "returncode": 0}
        from condiag.workspace import WorkspaceSnapshot
        r = restore_workspace(agent, WorkspaceSnapshot(base_commit_sha="expected"), "expected")
        assert not r.ok
        assert "HEAD mismatch" in r.reason
