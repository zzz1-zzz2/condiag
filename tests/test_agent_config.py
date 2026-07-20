"""Tests for AgentConfig module — no model calls."""
from __future__ import annotations

from pathlib import Path

import pytest

from condiag.agent.config import (
    LOCKED_CONFIG_PATH,
    LOCKED_YAML_SHA256,
    AgentConfig,
    RevisionProtocolConfig,
    sha256_full,
    sha256_short,
    load_locked_yaml,
)


class TestSHA:
    def test_full_length(self):
        h = sha256_full("hello")
        assert len(h) == 64

    def test_short_length(self):
        h = sha256_short("hello")
        assert len(h) == 16

    def test_deterministic(self):
        assert sha256_full("hello") == sha256_full("hello")
        assert sha256_full("hello") != sha256_full("world")


class TestConfigSHA:
    def test_locked_yaml_exists(self):
        assert LOCKED_CONFIG_PATH.exists(), f"Locked YAML not found: {LOCKED_CONFIG_PATH}"

    def test_locked_yaml_sha_matches(self):
        """If this fails, the locked YAML was modified.
        If intentional, update LOCKED_YAML_SHA256 in config.py."""
        raw = LOCKED_CONFIG_PATH.read_text("utf-8")
        actual = sha256_full(raw)
        assert actual == LOCKED_YAML_SHA256, (
            f"Locked YAML SHA mismatch:\n"
            f"  expected: {LOCKED_YAML_SHA256[:32]}...\n"
            f"  actual:   {actual[:32]}..."
        )

    def test_load_locked_yaml_returns_dict(self):
        cfg = load_locked_yaml()
        assert isinstance(cfg, dict)
        assert "agent" in cfg
        assert "environment" in cfg
        assert "model" in cfg


class TestRevisionProtocolConfig:
    def test_defaults(self):
        r = RevisionProtocolConfig()
        assert r.r1_wall_time_limit_seconds == 3600
        assert r.r2_wall_time_limit_seconds == 3600
        assert r.r1_max_consecutive_format_errors == 15
        assert r.r2_max_consecutive_format_errors == 3

    def test_sha_changes_with_params(self):
        r1 = RevisionProtocolConfig(r1_max_consecutive_format_errors=15)
        r2 = RevisionProtocolConfig(r1_max_consecutive_format_errors=3)
        assert r1.sha != r2.sha


class TestAgentConfig:
    def test_default_config(self):
        c = AgentConfig()
        assert c.protocol_name == "persistent_revision"
        assert c.config_sha != ""
        assert c.source_yaml_sha != ""

    def test_source_yaml_sha_is_full_length(self):
        c = AgentConfig()
        assert len(c.source_yaml_sha) == 64 or len(c.source_yaml_sha) == 0

    def test_config_sha_covers_step_limit(self):
        c1 = AgentConfig(step_limit=0, cost_limit=5.0)
        c2 = AgentConfig(step_limit=250, cost_limit=3.0)
        assert c1.config_sha != c2.config_sha

    def test_config_sha_16_chars(self):
        c = AgentConfig()
        assert len(c.config_sha) == 16

    def test_config_sha_includes_revision_protocol(self):
        r1 = RevisionProtocolConfig(r1_max_consecutive_format_errors=15)
        r2 = RevisionProtocolConfig(r1_max_consecutive_format_errors=3)
        c1 = AgentConfig(revision_protocol=r1)
        c2 = AgentConfig(revision_protocol=r2)
        assert c1.config_sha != c2.config_sha

    def test_different_protocols_different_sha(self):
        c1 = AgentConfig(protocol_name="baseline_reproduction")
        c2 = AgentConfig(protocol_name="persistent_revision")
        assert c1.config_sha != c2.config_sha


class TestRedaction:
    def test_redact_trajectory_removes_api_key(self):
        from condiag.agent.config import redact_trajectory
        traj = {
            "info": {
                "config": {
                    "model": {
                        "model_kwargs": {
                            "api_key": "sk-should-be-redacted",
                            "temperature": 0.0,
                        }
                    }
                }
            },
            "messages": [],
        }
        result = redact_trajectory(traj)
        kwargs = result["info"]["config"]["model"]["model_kwargs"]
        assert kwargs["api_key"] == "***REDACTED***"
        assert kwargs["temperature"] == 0.0
        assert "sk-" not in str(result)
        # Original unchanged
        assert traj["info"]["config"]["model"]["model_kwargs"]["api_key"] == "sk-should-be-redacted"

    def test_redact_trajectory_no_key(self):
        from condiag.agent.config import redact_trajectory
        traj = {"info": {"config": {"model": {"model_kwargs": {"temperature": 0.0}}}}}
        result = redact_trajectory(traj)
        assert result["info"]["config"]["model"]["model_kwargs"]["temperature"] == 0.0
