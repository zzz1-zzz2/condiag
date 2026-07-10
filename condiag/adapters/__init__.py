"""ConDiag Agent Adapter Layer.

ConDiag is designed as an agent-agnostic post-failure recovery middleware.
This package defines two kinds of adapter with clearly separated roles
(v0.2, 2026-06-29):

  Agent Adapter (input side):
      Host Agent Attempt 1 raw logs -> unified case_bundle (runtime_signals +
      patch.diff + local_test_outputs.md + final_patch_context.json)

  Retry Injection Adapter (output side):
      ConDiag ContextPacket -> Host Agent Attempt 2 input (task message +
      CLI command + artifact collection + protocol validation)

Current v0 status:
  - miniswe:                     AgentAdapter, fully implemented
  - miniswe_retry_injection:     RetryInjectionAdapter, implemented
  - agentless / openhands / swe_agent: AgentAdapter skeleton (planned)
"""
from .base import AgentAdapter, register_adapter, get_adapter, list_adapters
from .miniswe import MinisweAdapter
from .miniswe_retry_injection import MinisweRetryInjectionAdapter
from .agentless import AgentlessAdapter
from .openhands import OpenhandsAdapter
from .swe_agent import SweAgentAdapter

__all__ = [
    "AgentAdapter",
    "register_adapter",
    "get_adapter",
    "list_adapters",
    "MinisweAdapter",
    "MinisweRetryInjectionAdapter",
    "AgentlessAdapter",
    "OpenhandsAdapter",
    "SweAgentAdapter",
]
