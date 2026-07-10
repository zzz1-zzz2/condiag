"""Agent adapter abstract base + registry.

Each subclass binds a specific repair agent (mini-SWE / Agentless /
OpenHands / SWE-agent / ...) into the ConDiag framework by translating the
agent's raw outputs into ConDiag's unified case_bundle.

Architecture boundary (v0.2, 2026-06-29):
  Agent Adapter = input side only:
    Host Agent Attempt 1 raw logs -> unified case_bundle (runtime_signals,
    patch.diff, final_patch_context, local_test_outputs).

  Retry Injection Adapter = separate concern:
    ContextPacket -> Host Agent Attempt 2 input.
    See adapters/miniswe_retry_injection.py for the mini-SWE implementation.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class AgentAdapter(ABC):
    """Abstract base for agent adapters (input side: attempt_1 -> case_bundle).

    ConDiag Core only consumes the artifacts produced by these methods; it
    never reads agent-specific formats directly.

    This class does NOT handle retry injection.  That is the responsibility
    of a separate RetryInjectionAdapter (see miniswe_retry_injection.py).
    """

    name: str = ""                # short identifier, used in runs/pilot50/<agent>/...
    display_name: str = ""        # human-readable
    status: str = "planned"       # "implemented" | "planned"

    # ----- input side: raw agent run -> unified case_bundle -----

    @abstractmethod
    def build_case_bundle(
        self,
        raw_run_dir: Path,
        instance_id: str,
        out_dir: Path,
    ) -> dict:
        """Build a unified case_bundle from raw agent outputs.

        Returns the build_report dict (also written to
        out_dir/build_report.json by the implementation).

        Output contract (out_dir contents):
            raw_trajectory.json
            runtime_signals.json
            patch.diff
            local_test_outputs.md
            final_patch_context.json
            build_report.json
        """
        ...

    @abstractmethod
    def extract_runtime_signals(self, raw_run_dir: Path) -> dict:
        """Return runtime_signals.json as a dict (no file IO side effect)."""
        ...

    @abstractmethod
    def extract_patch(self, raw_run_dir: Path) -> str:
        """Return the agent's final patch.diff content."""
        ...

    @abstractmethod
    def extract_final_patch_context(self, raw_run_dir: Path) -> dict:
        """Return final declared PATCH_CONTEXT as a dict (machine-readable)."""
        ...


# ===== registry =====

_REGISTRY: dict[str, type[AgentAdapter]] = {}


def register_adapter(adapter_cls: type[AgentAdapter]) -> type[AgentAdapter]:
    """Class decorator: register an AgentAdapter subclass by its .name."""
    if not adapter_cls.name:
        raise ValueError(f"adapter {adapter_cls.__name__} has empty .name")
    _REGISTRY[adapter_cls.name] = adapter_cls
    return adapter_cls


def get_adapter(name: str) -> AgentAdapter:
    """Instantiate a registered adapter by name."""
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown adapter '{name}'. registered: {sorted(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]()


def list_adapters() -> dict[str, dict]:
    """Return {name: {display_name, status}} for all registered adapters."""
    return {
        name: {"display_name": cls.display_name, "status": cls.status}
        for name, cls in _REGISTRY.items()
    }
