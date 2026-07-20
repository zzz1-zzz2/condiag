"""Tests for AgentConfig module — no model calls."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from condiag.agent.config import (
    LOCKED_CONFIG_PATH,
    LOCKED_YAML_SHA256,
    AgentConfig,
    ConfigDriftError,
    _sha,
    load_locked_yaml,
)


class TestConfigSHA:
    def test_locked_yaml_exists(self):
        assert LOCKED_CONFIG_PATH.exists(), f"Locked YAML not found: {LOCKED_CONFIG_PATH}"

    def test_locked_yaml_sha_matches(self):
        """If this fails, the locked YAML was modified.
        If intentional, update LOCKED_YAML_SHA256 in config.py."""
        raw = LOCKED_CONFIG_PATH.read_text("utf-8")
        actual = _sha(raw)
        assert actual == LOCKED_YAML_SHA256, (
            f"Locked YAML SHA mismatch: expected {LOCKED_YAML_SHA256}, got {actual}. "
            "If you upgraded the locked config, update LOCKED_YAML_SHA256 in config.py"
        )

    def test_load_locked_yaml_returns_dict(self):
        cfg = load_locked_yaml()
        assert isinstance(cfg, dict)
        assert "agent" in cfg
        assert "environment" in cfg
        assert "model" in cfg


class TestAgentConfig:
    def test_default_config(self):
        c = AgentConfig()
        assert c.protocol_name == "persistent_revision"
        assert c.protocol_version == "1.0"
        assert c.config_sha != ""
        assert c.source_yaml_sha != ""

    def test_config_sha_covers_all_protocol_fields(self):
        c1 = AgentConfig(step_limit=0, cost_limit=5.0)
        c2 = AgentConfig(step_limit=250, cost_limit=3.0)
        assert c1.config_sha != c2.config_sha, \
            "Different protocol params must produce different config_sha"

    def test_config_sha_changes_with_yaml_sha(self):
        c1 = AgentConfig(source_yaml_sha="abc123")
        c2 = AgentConfig(source_yaml_sha="def456")
        assert c1.config_sha != c2.config_sha

    def test_different_protocols_different_sha(self):
        c1 = AgentConfig(protocol_name="baseline_reproduction")
        c2 = AgentConfig(protocol_name="persistent_revision")
        assert c1.config_sha != c2.config_sha

    def test_source_yaml_sha_auto_filled(self):
        c = AgentConfig()
        assert c.source_yaml_sha != "", "source_yaml_sha should be auto-filled"
        assert len(c.source_yaml_sha) == 16
