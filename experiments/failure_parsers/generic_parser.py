"""Generic fallback parser — last resort."""
from __future__ import annotations

import re
from dataclasses import dataclass

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser


@register_parser("generic_parser")
class GenericParser(FailureParser):
    """Minimal fallback: extract whatever failure signal is available."""

    framework = "generic"
    priority = 0

    _FAIL_LINE = re.compile(r"(?:FAIL|FAILED|ERROR|fatal)\s*:?\s*(.*)", re.I | re.M)
    _TRACEBACK = re.compile(r"Traceback.*\n(?:.*\n)*.*\w+(?:Error|Exception):", re.M)
    _EXIT_CODE = re.compile(r"exit code[:\s]*(\d+)", re.I)

    @classmethod
    def can_parse(cls, log_text: str) -> bool:
        # generic parser can always try
        return True

    @classmethod
    def parse(cls, instance_id: str, log_text: str,
              raw_log_path: str = "") -> FailureWitness:
        lines = log_text.strip().splitlines()

        # extract failure lines
        failure_lines = []
        for m in cls._FAIL_LINE.finditer(log_text):
            failure_lines.append(m.group(0).strip())
        error_msg = "\n".join(failure_lines[:5]) if failure_lines else (
            lines[-1].strip() if lines else "")

        # extract traceback frames
        frames = []
        tb_match = cls._TRACEBACK.search(log_text)
        if tb_match:
            tb_text = tb_match.group(0)
            for line in tb_text.splitlines():
                if 'File "' in line:
                    frames.append(line.strip())

        # try to classify
        ftype = "test_failure"
        exit_m = cls._EXIT_CODE.search(log_text)
        if exit_m:
            ftype = f"exit_code_{exit_m.group(1)}"

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type=ftype,
            test_framework="generic",
            error_message=error_msg[:2000],
            stack_trace=frames[:20],
            top_repo_frames=[],
            mode="post_validation_output",
            source="post_validation_output",
            source_type="validation_failure",
            raw_output_path=raw_log_path,
            version="v2.0",
        )
