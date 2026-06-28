"""Agentless adapter (skeleton).

Agentless runs as a 4-stage pipeline (file localization -> element
localization -> edit localization -> repair generation), not as an open-ended
trajectory. The adapter must map those stages onto ConDiag's runtime_signals
schema:

    searched_queries    <- localization prompts / issue keywords
    viewed_files        <- file localization candidates
    viewed_spans        <- element localization / related locations
    edited_files        <- generated patch diff
    patch.diff          <- repair output
    local_test_outputs  <- validation logs
    final_patch_context <- repair prompt context / selected locations

Agentless is a particularly good fit for RELOCALIZE: its file localization
stage is a clean, named failure point. ContextBench case django-11630 is a
known Agentless file-localization failure (agent misled by "db table
collision" wording, missed error code E028).

v0 status: planned. NotImplementedError on all methods.
"""
from __future__ import annotations

from pathlib import Path

from .base import AgentAdapter, register_adapter


@register_adapter
class AgentlessAdapter(AgentAdapter):
    name = "agentless"
    display_name = "Agentless"
    status = "planned"

    def build_case_bundle(self, raw_run_dir: Path, instance_id: str, out_dir: Path) -> dict:
        raise NotImplementedError(
            "AgentlessAdapter is planned for v1; "
            "use MinisweAdapter for current Pilot50 work."
        )

    def extract_runtime_signals(self, raw_run_dir: Path) -> dict:
        raise NotImplementedError("AgentlessAdapter.extract_runtime_signals: planned for v1")

    def extract_patch(self, raw_run_dir: Path) -> str:
        raise NotImplementedError("AgentlessAdapter.extract_patch: planned for v1")

    def extract_final_patch_context(self, raw_run_dir: Path) -> dict:
        raise NotImplementedError("AgentlessAdapter.extract_final_patch_context: planned for v1")

    def build_retry_input(self, context_packet_path: Path, task_metadata: dict) -> dict:
        raise NotImplementedError(
            "AgentlessAdapter.build_retry_input: planned for v1. "
            "Planned shape: {agent: 'agentless', retry_input_kind: 'localized_candidates', "
            "files: [...], spans: [...]}"
        )
