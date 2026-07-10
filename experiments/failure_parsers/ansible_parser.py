"""Parser for Ansible custom test output.

Ansible tests produce pytest-formatted output with ansible-specific markers.
Extends PytestParser for unit tests, handles ansible-playbook output separately.
"""
from __future__ import annotations

import re

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser
from .pytest_parser import PytestParser


@register_parser("ansible_parser")
class AnsibleParser(FailureParser):
    """Parse Ansible test output, extending pytest patterns."""

    framework = "ansible_custom"
    priority = 110  # higher than pytest_parser (100) for ansible-detected logs

    _PLAY_PATTERN = re.compile(r"^PLAY\s+\[", re.M)
    _TASK_PATTERN = re.compile(r"^TASK\s+\[", re.M)
    _FATAL_PATTERN = re.compile(r"fatal:\s*\[[^\]]+\]:\s*FAILED!", re.M)
    _ANSIBLE_MODULE = re.compile(r"ansible", re.I)

    @classmethod
    def can_parse(cls, log_text: str) -> bool:
        if cls._PLAY_PATTERN.search(log_text) or cls._TASK_PATTERN.search(log_text):
            return True
        if cls._FATAL_PATTERN.search(log_text):
            return True
        if PytestParser.can_parse(log_text) and cls._ANSIBLE_MODULE.search(log_text):
            return True
        return False

    @classmethod
    def parse(cls, instance_id: str, log_text: str,
              raw_log_path: str = "") -> FailureWitness:
        # Ansible unit tests use pytest underneath — delegate with framework override
        if PytestParser.can_parse(log_text):
            base = PytestParser.parse(instance_id, log_text, raw_log_path)
            base.test_framework = "ansible_custom"
            base.parser_name = "ansible_parser"
            # Check for ansible-specific failure patterns
            if cls._FATAL_PATTERN.search(log_text):
                base.failure_type = "ansible_task_failure"
            return base

        # Standalone ansible-playbook output
        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type="test_failure",
            test_framework="ansible_custom",
            failed_tests=[],
            error_message="test_failure",
            stack_trace=[], top_repo_frames=[],
            mode="post_validation_output", source="post_validation_output",
            source_type="validation_failure", raw_output_path=raw_log_path,
            version="v2.0",
        )
