"""SWE-agent adapter (skeleton).

SWE-agent has its own prompt format, submit protocol, and command/action
stream. The adapter must map SWE-agent actions onto ConDiag's
runtime_signals.json.

v0 status: planned. NotImplementedError on all methods.
"""
from __future__ import annotations

from pathlib import Path

from .base import AgentAdapter, register_adapter


@register_adapter
class SweAgentAdapter(AgentAdapter):
    name = "swe_agent"
    display_name = "SWE-agent"
    status = "planned"

    def build_case_bundle(self, raw_run_dir: Path, instance_id: str, out_dir: Path) -> dict:
        raise NotImplementedError(
            "SweAgentAdapter is planned for v1; "
            "use MinisweAdapter for current Pilot50 work."
        )

    def extract_runtime_signals(self, raw_run_dir: Path) -> dict:
        raise NotImplementedError("SweAgentAdapter.extract_runtime_signals: planned for v1")

    def extract_patch(self, raw_run_dir: Path) -> str:
        raise NotImplementedError("SweAgentAdapter.extract_patch: planned for v1")

    def extract_final_patch_context(self, raw_run_dir: Path) -> dict:
        raise NotImplementedError("SweAgentAdapter.extract_final_patch_context: planned for v1")

    def build_retry_input(self, context_packet_path: Path, task_metadata: dict) -> dict:
        raise NotImplementedError(
            "SweAgentAdapter.build_retry_input: planned for v1. "
            "Planned shape: {agent: 'swe_agent', retry_input_kind: 'instruction_injection', ...}"
        )
