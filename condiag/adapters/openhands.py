"""OpenHands adapter (skeleton).

OpenHands uses a tool-call event stream + workspace state + custom submit
protocol. The adapter must normalize OpenHands events into ConDiag's
runtime_signals.json (viewed spans, edited files, test runs, etc.).

ContextBench has shown OpenHands outputs can be adapted to produce
machine-readable <PATCH_CONTEXT>; this adapter will follow the same shape.

v0 status: planned. NotImplementedError on all methods.
"""
from __future__ import annotations

from pathlib import Path

from .base import AgentAdapter, register_adapter


@register_adapter
class OpenhandsAdapter(AgentAdapter):
    name = "openhands"
    display_name = "OpenHands"
    status = "planned"

    def build_case_bundle(self, raw_run_dir: Path, instance_id: str, out_dir: Path) -> dict:
        raise NotImplementedError(
            "OpenhandsAdapter is planned for v1; "
            "use MinisweAdapter for current Pilot50 work."
        )

    def extract_runtime_signals(self, raw_run_dir: Path) -> dict:
        raise NotImplementedError("OpenhandsAdapter.extract_runtime_signals: planned for v1")

    def extract_patch(self, raw_run_dir: Path) -> str:
        raise NotImplementedError("OpenhandsAdapter.extract_patch: planned for v1")

    def extract_final_patch_context(self, raw_run_dir: Path) -> dict:
        raise NotImplementedError("OpenhandsAdapter.extract_final_patch_context: planned for v1")

    def build_retry_input(self, context_packet_path: Path, task_metadata: dict) -> dict:
        raise NotImplementedError(
            "OpenhandsAdapter.build_retry_input: planned for v1. "
            "Planned shape: {agent: 'openhands', retry_input_kind: 'event_stream_injection', ...}"
        )
