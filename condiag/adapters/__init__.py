"""ConDiag Agent Adapter Layer.

ConDiag is designed as an agent-agnostic post-failure recovery middleware.
This package defines the boundary between heterogeneous repair agents
(mini-SWE / Agentless / OpenHands / SWE-agent / ...) and ConDiag Core.

Two responsibilities per adapter:
  - Input side:  raw agent run -> unified case_bundle (runtime_signals.json +
                 patch.diff + local_test_outputs.md + final_patch_context.json)
  - Output side: ConDiag context_packet.md -> agent-specific retry input

ConDiag Core only consumes unified case_bundle artifacts; it never imports
agent-specific modules.

Current v0 status:
  - miniswe:   fully implemented (wraps tools/build_case_bundle.py)
  - agentless: skeleton, NotImplementedError
  - openhands: skeleton, NotImplementedError
  - swe_agent: skeleton, NotImplementedError

All four are imported here so the registry sees them. Check `.status` field
("implemented" vs "planned") to see which can actually run.
"""
from .base import AgentAdapter, register_adapter, get_adapter, list_adapters
from .miniswe import MinisweAdapter
from .agentless import AgentlessAdapter
from .openhands import OpenhandsAdapter
from .swe_agent import SweAgentAdapter

__all__ = [
    "AgentAdapter",
    "register_adapter",
    "get_adapter",
    "list_adapters",
    "MinisweAdapter",
    "AgentlessAdapter",
    "OpenhandsAdapter",
    "SweAgentAdapter",
]
