"""Lightweight test for branch_runner — verifies agent creation and limits."""
from __future__ import annotations

from unittest.mock import MagicMock

from condiag.branch_runner import run_branch


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
    def test_agent_is_defined_before_message_building(self):
        """If this fails, agent is referenced before assignment (NameError)."""
        agent_instance = MockAgent()

        def factory():
            return agent_instance

        result = run_branch(
            agent_factory=factory,
            checkpoint_messages=[],
            base_commit="test",
            task="test task",
            patch_to_apply="",
            r1_n_calls=5,
            r1_cost=0.5,
            failure_witness=None,
            diagnosis=None,
            mode="sf",
        )
        # The branch should reach an error exit (mock agent raises), not NameError
        assert result is not None
        assert "error" in result.termination_reason

    def test_branch_accepts_protocol_config(self):
        """Verify RevisionProtocolConfig is accepted without error."""
        from condiag.agent.config import RevisionProtocolConfig
        agent_instance = MockAgent()

        def factory():
            return agent_instance

        result = run_branch(
            agent_factory=factory,
            checkpoint_messages=[],
            base_commit="test",
            task="test task",
            patch_to_apply="",
            r1_n_calls=5,
            r1_cost=0.5,
            failure_witness=None,
            diagnosis=None,
            mode="sf",
            protocol_config=RevisionProtocolConfig(),
        )
        assert result is not None
