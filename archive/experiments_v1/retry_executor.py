"""DEPRECATED — direct-LLM retry executor (archived 2026-06-29).

This module has been replaced. The direct-LLM approach bypassed the Host
Agent tool-use loop and is NOT valid for official repair-rate evaluation.

See:
  experiments/archived/retry_executor.py  — historical reference
  condiag/adapters/miniswe.py             — MinisweAdapter.build_retry_input()
  experiments/host_agent_retry_runner.py  — correct Host-Agent retry runner

Importing this module will raise an error to prevent accidental use.
"""
raise ImportError(
    "retry_executor.py has been archived. "
    "Use experiments.host_agent_retry_runner instead. "
    "See experiments/archived/retry_executor.py for historical reference."
)
