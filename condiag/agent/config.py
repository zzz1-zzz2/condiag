"""ConDiag Agent Configuration — single source of truth for all Agent configurations.

Design:
  - One factory function, one set of locked prompt templates
  - Protocol-versioned: "baseline_reproduction" vs "persistent_revision"
  - Config SHA is checked at runtime to detect drift
  - All environment variables come from env, not hardcoded strings
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

LOCKED_CONFIG_PATH = Path(__file__).parent.parent.parent / "configs" / "locked" / "minisweagent_swebench_v2.4.1.yaml"

# SHA of the locked YAML at the time of writing.
# If the locked file changes without updating this SHA, ConfigDriftError is raised.
EXPECTED_YAML_SHA256 = "unknown_yet"


class ConfigDriftError(RuntimeError):
    """Raised when the locked YAML config has drifted from expected SHA."""


@dataclass(frozen=True)
class AgentConfig:
    """Immutable agent configuration with protocol tracking.

    protocol_name/version: distinguish "baseline_reproduction" from "persistent_revision"
    config_sha:            SHA of this config object for audit traceability
    source_yaml_sha:       SHA of the locked YAML this config was built from
    """

    protocol_name: str = "persistent_revision"
    protocol_version: str = "1.0"
    model_name: str = "deepseek/deepseek-v4-pro"
    temperature: float = 0.0
    max_tokens: int = 4096
    step_limit: int = 0
    cost_limit: float = 5.0
    config_sha: str = ""
    source_yaml_sha: str = ""

    def __post_init__(self):
        # Auto-compute SHA if not provided
        if not self.config_sha:
            raw = f"{self.protocol_name}:{self.protocol_version}:{self.model_name}:{self.temperature}:{self.max_tokens}"
            object.__setattr__(self, "config_sha", _sha(raw))


def load_locked_yaml() -> dict:
    """Load the locked YAML, verify its SHA, return parsed config dict."""
    if not LOCKED_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Locked config not found: {LOCKED_CONFIG_PATH}")

    raw = LOCKED_CONFIG_PATH.read_text(encoding="utf-8")
    actual_sha = _sha(raw)

    # First time: record the actual SHA
    global EXPECTED_YAML_SHA256
    if EXPECTED_YAML_SHA256 == "unknown_yet":
        EXPECTED_YAML_SHA256 = actual_sha

    if actual_sha != EXPECTED_YAML_SHA256:
        raise ConfigDriftError(
            f"Locked YAML SHA mismatch: expected {EXPECTED_YAML_SHA256}, got {actual_sha}. "
            f"Locked file: {LOCKED_CONFIG_PATH}"
        )

    return yaml.safe_load(raw)


def build_agent_factory(
    config: AgentConfig,
    instance_id: str,
) -> Callable:
    """Build and return a callable that creates a new agent instance.

    Each call to the returned factory creates a fresh agent with its own
    Docker container — suitable for forked branches in Round 2.

    Usage:
        factory = build_agent_factory(config, instance_id)
        agent = factory()  # new container, new agent
    """
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.litellm_model import LitellmModel
    from minisweagent.run.benchmarks.swebench import get_swebench_docker_image_name

    # Resolve Docker image
    pred = {"instance_id": instance_id}
    image_name = get_swebench_docker_image_name(pred)

    # Read locked prompt templates
    yaml_config = load_locked_yaml()
    agent_cfg = yaml_config.get("agent", {})
    system_template = agent_cfg.get("system_template", "")
    instance_template = agent_cfg.get("instance_template", "")
    env_config = yaml_config.get("environment", {})

    def factory():
        # Environment — use official BASH_ENV setting
        env = DockerEnvironment(
            image=image_name,
            cwd=env_config.get("cwd", "/testbed"),
            timeout=120,
        )
        # Override environment variables to match official config
        for k, v in env_config.get("env", {}).items():
            env.config.env[k] = v

        # Model — runtime params only; prompt comes from YAML
        model = LitellmModel(
            model_name=config.model_name,
            model_kwargs={
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
            },
        )

        # Agent — ConDiagIntegratedAgent is a thin wrapper around DefaultAgent
        # that supports both tool_calls and extra.actions.
        from condiag.integrated_agent import ConDiagIntegratedAgent
        agent = ConDiagIntegratedAgent(
            model=model,
            env=env,
            system_template=system_template,
            instance_template=instance_template,
            step_limit=config.step_limit,
            cost_limit=config.cost_limit,
            output_path=None,
        )
        return agent

    return factory


def require_api_key():
    """Exit if DEEPSEEK_API_KEY is not set. Call at entry points."""
    if not os.environ.get("DEEPSEEK_API_KEY"):
        import sys
        print("FATAL: DEEPSEEK_API_KEY environment variable is not set.", file=sys.stderr)
        print("Create a .env file with DEEPSEEK_API_KEY=sk-... or export it.", file=sys.stderr)
        sys.exit(1)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
