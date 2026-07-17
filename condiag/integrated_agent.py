"""ConDiag v4 Integrated Agent — embedded in mini-SWE-agent native repair loop.

ConDiagIntegratedAgent(DefaultAgent):
  run_episode() instead of run()
  Supports:
    - Round 1 → intermediate validation → Round 2 flow
    - Stateful Feedback (diagnosis_builder=None)
    - ConDiag (diagnosis_builder=DiagnosisPromptBuilder)
"""

import logging
from typing import Any, Callable

from minisweagent.agents.default import DefaultAgent
from minisweagent.exceptions import Submitted

from condiag.diagnosis_prompt_builder import TrajectorySnapshot


logger = logging.getLogger("condiag.integrated_agent")


class ConDiagIntegratedAgent(DefaultAgent):
    """Agent that persists repair state across intermediate validation.

    Instead of exiting on first COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT,
    intercepts the submission, runs an isolated evaluator, injects
    FailureWitness (and optionally a structured diagnosis prompt),
    then continues the same repair episode.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._submission_count = 0
        self.phase = "round1"

    def run_episode(
        self,
        task: str = "",
        *,
        evaluator: Callable | None = None,
        diagnosis_builder: Any = None,
        **kwargs,
    ) -> dict:
        """Run a persistent repair episode with optional intermediate validation."""
        self.phase = "round1"
        self._submission_count = 0

        # Initialize messages
        self.extra_template_vars |= {"task": task, **kwargs}
        self.messages = []
        self.add_messages(
            self.model.format_message(
                role="system",
                content=self._render_template(self.config.system_template),
            ),
            self.model.format_message(
                role="user",
                content=self._render_template(self.config.instance_template),
            ),
        )

        while True:
            try:
                self.step()
                self.n_consecutive_format_errors = 0
            except Submitted as e:
                self._submission_count += 1
                patch = self._extract_patch()

                if self.phase == "round1":
                    logger.info("[ConDiag] Round 1 submitted (count=%d). Running intermediate validation...", self._submission_count)

                    eval_fn = evaluator or _mock_evaluator
                    witness = eval_fn(patch)

                    if witness.get("resolved", False):
                        logger.info("[ConDiag] Round 1 already resolved. Done.")
                        self.add_messages(*e.messages)
                        break

                    # Build a tool response for the last action so the API contract is satisfied
                    tool_resp = self._build_tool_response_for_last_action()
                    if tool_resp is not None:
                        self.add_messages(tool_resp)

                    # Always inject FailureWitness
                    fw_msg = self._build_failure_witness_message(witness)
                    self.add_messages(fw_msg)

                    # Optionally inject diagnosis prompt
                    if diagnosis_builder is not None:
                        trajectory = self._trajectory_snapshot()
                        diagnosis = diagnosis_builder.build(witness, trajectory)
                        self.add_messages({"role": "user", "content": diagnosis})

                    self.phase = "round2"
                    logger.info("[ConDiag] Entering Round 2.")
                    continue  # Don't break — continue repair loop

                elif self.phase == "round2":
                    logger.info("[ConDiag] Round 2 submitted (count=%d). Final submission.", self._submission_count)
                    self.add_messages(*e.messages)
                    break

            except Exception as e:
                self.handle_uncaught_exception(e)
                raise

            finally:
                self.save(self.config.output_path)

            if self.messages and self.messages[-1].get("role") == "exit" and self.phase == "round2":
                break

        return self.messages[-1].get("extra", {}) if self.messages else {}

    def _extract_patch(self) -> str:
        try:
            output = self.env.execute({
                "command": "git diff HEAD 2>/dev/null || echo 'NO_PATCH'"
            })
            return output.get("output", "")
        except Exception:
            return "<patch extraction failed>"

    def _build_failure_witness_message(self, witness: dict) -> dict:
        failed_tests = witness.get("failed_tests", [])
        error_info = witness.get("error_message", "")
        stack_frames = witness.get("stack_frames", [])

        msg = "## Intermediate Validation Result\n\n"
        msg += "Your submitted patch did not pass validation.\n\n"
        msg += "Status: unresolved\n"

        if failed_tests:
            msg += f"\nFailed tests: {', '.join(failed_tests)}\n"
        if error_info:
            msg += f"\nError:\n```\n{error_info}\n```\n"
        if stack_frames:
            frames_text = "\n".join(stack_frames[:5])
            msg += f"\nStack frames:\n```\n{frames_text}\n```\n"

        msg += "\nPlease investigate the failure and revise your patch."

        return {"role": "user", "content": msg}

    def _build_tool_response_for_last_action(self) -> dict | None:
        """Build a tool response message for the last assistant action.

        When Submitted is raised inside env.execute(), the normal
        observation pipeline (format_observation_messages) is skipped,
        leaving the assistant message with tool_calls but no tool response.
        The OpenAI-compatible API requires a tool response to follow.
        """
        if not self.messages:
            return None
        last = self.messages[-1]
        actions = last.get("extra", {}).get("actions", [])
        if not actions:
            return None
        action = actions[-1]
        tool_call_id = action.get("tool_call_id")
        if not tool_call_id:
            # Text-based fallback — agent didn't use tool_calls
            return None
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": "(intermediate validation result will follow)",
        }

    def _trajectory_snapshot(self) -> TrajectorySnapshot:
        """Extract trajectory info as a TrajectorySnapshot."""
        viewed = []
        edited = []
        for msg in self.messages:
            extra = msg.get("extra", {}) or {}
            for action in extra.get("actions", []):
                cmd = action.get("command", "")
                for token in cmd.split():
                    if "/" in token and (".py" in token or ".js" in token):
                        viewed.append(token)
        return TrajectorySnapshot(viewed_files=viewed, edited_files=edited)

    def serialize(self, *extra_dicts) -> dict:
        return super().serialize(
            {"condiag": {"phase": self.phase, "submission_count": self._submission_count}},
            *extra_dicts,
        )


def _mock_evaluator(patch: str) -> dict:
    """Mock evaluator — always returns unresolved."""
    return {
        "resolved": False,
        "summary": "Mock validation: tests failed.",
        "failed_tests": ["test_example"],
        "error_message": "AssertionError: expected True, got False",
        "stack_frames": ["test_example.py:10: in test_example", "assert result == True"],
    }
