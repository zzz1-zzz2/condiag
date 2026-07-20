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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

LOCKED_CONFIG_PATH = Path(__file__).parent.parent.parent / "configs" / "locked" / "minisweagent_swebench_v2.4.1.yaml"

# Hardcoded SHA256 of the locked YAML.
# If the locked YAML is updated (new mini-swe-agent version), this must be
# recalculated and updated EXPLICITLY — auto-follow is forbidden.
LOCKED_YAML_SHA256 = "229f178a07faa109"


class ConfigDriftError(RuntimeError):
    """Raised when the locked YAML config has drifted from expected SHA."""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class AgentConfig:
    """Immutable agent configuration with protocol tracking.

    config_sha covers ALL fields below — not just a subset.
    source_yaml_sha is auto-filled from the locked YAML on construction.
    """

    protocol_name: str = "persistent_revision"
    protocol_version: str = "1.0"
    model_name: str = "openai/deepseek-v4-pro"
    temperature: float = 0.0
    max_tokens: int = 4096
    step_limit: int = 0
    cost_limit: float = 5.0
    config_sha: str = ""
    source_yaml_sha: str = ""

    def __post_init__(self):
        # Auto-fill source_yaml_sha from locked YAML
        if not self.source_yaml_sha and LOCKED_CONFIG_PATH.exists():
            raw = LOCKED_CONFIG_PATH.read_text("utf-8")
            object.__setattr__(self, "source_yaml_sha", _sha(raw))

        # Compute config SHA covering ALL protocol-relevant fields
        if not self.config_sha:
            raw = (
                f"protocol={self.protocol_name}:{self.protocol_version}"
                f"|model={self.model_name}"
                f"|temp={self.temperature}"
                f"|maxtok={self.max_tokens}"
                f"|step={self.step_limit}"
                f"|cost={self.cost_limit}"
                f"|yaml={self.source_yaml_sha}"
            )
            object.__setattr__(self, "config_sha", _sha(raw))


def load_locked_yaml() -> dict:
    """Load the locked YAML, verify its SHA, return parsed config dict."""
    if not LOCKED_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Locked config not found: {LOCKED_CONFIG_PATH}")

    raw = LOCKED_CONFIG_PATH.read_text(encoding="utf-8")
    actual_sha = _sha(raw)

    if actual_sha != LOCKED_YAML_SHA256:
        raise ConfigDriftError(
            f"Locked YAML SHA mismatch: expected {LOCKED_YAML_SHA256}, got {actual_sha}. "
            f"If you intentionally upgraded the locked YAML, update LOCKED_YAML_SHA256 in config.py. "
            f"Locked file: {LOCKED_CONFIG_PATH}"
        )

    return yaml.safe_load(raw)


def build_agent_factory(
    config: AgentConfig,
    instance_id: str,
) -> Callable[[], Any]:
    """Build and return a callable that creates a new agent instance.

    Each call to the returned factory creates a fresh agent with its own
    Docker container — suitable for forked branches in Round 2.

    The agent is a plain minisweagent.agents.default.DefaultAgent,
    NOT ConDiagIntegratedAgent (frozen v4 architecture).

    Usage:
        factory = build_agent_factory(config, instance_id)
        agent = factory()  # new container, new agent
    """
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.litellm_model import LitellmModel
    from minisweagent.run.benchmarks.swebench import get_swebench_docker_image_name

    pred = {"instance_id": instance_id}
    image_name = get_swebench_docker_image_name(pred)

    yaml_config = load_locked_yaml()
    agent_cfg = yaml_config.get("agent", {})
    env_cfg = yaml_config.get("environment", {})
    model_cfg = yaml_config.get("model", {})

    system_template = agent_cfg.get("system_template", "")
    instance_template = agent_cfg.get("instance_template", "")

    # Read YAML environment settings
    env_timeout = env_cfg.get("timeout", 120)
    env_interpreter = env_cfg.get("interpreter", ["bash", "-c"])
    env_vars = env_cfg.get("env", {})
    env_cwd = env_cfg.get("cwd", "/testbed")

    # Read YAML model settings
    model_kwargs = dict(model_cfg.get("model_kwargs", {}))
    model_kwargs.update({
        "temperature": config.temperature,
        "max_tokens": config.max_tokens,
    })
    observation_template = model_cfg.get("observation_template", "")
    format_error_template = model_cfg.get("format_error_template", "")

    def factory():
        env = DockerEnvironment(
            image=image_name,
            cwd=env_cwd,
            timeout=env_timeout,
            interpreter=env_interpreter,
        )
        # Apply environment variables
        for k, v in env_vars.items():
            env.config.env[k] = v

        model = LitellmModel(
            model_name=config.model_name,
            model_kwargs=model_kwargs,
        )
        # Apply model observation/error templates from YAML
        if observation_template:
            model.config.observation_template = observation_template
        if format_error_template:
            model.config.format_error_template = format_error_template

        agent = DefaultAgent(
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
