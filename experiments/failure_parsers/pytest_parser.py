"""Parser for pytest/unittest failure output."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser


@register_parser("pytest_parser")
class PytestParser(FailureParser):
    """Parse pytest/unittest failure output."""

    framework = "pytest"
    priority = 100

    # pytest FAILURES header
    _PYTEST_FAILURES = re.compile(
        r"^=+\s*FAILURES\s*=", re.M
    )
    # unittest FAIL: / ERROR: lines
    _UNITTEST_FAIL = re.compile(
        r"^(?:FAIL|ERROR):\s+", re.M
    )
    # pytest FAILED line:  FAILED test_file.py::test_name - AssertionError: ...
    _PYTEST_FAILED = re.compile(
        r"FAILED\s+(\S+?)::(\S+?)(?:\s+-?\s*(.*))?$", re.M
    )
    # pytest test file:  test_file.py::test_name
    _PYTEST_TEST = re.compile(r"(\S+\.py)::(\S+)")
    # traceback line:  File "/path/to/file.py", line N, in func
    _TRACEBACK_FILE = re.compile(
        r'^\s*File\s+"([^"]+)",\s+line\s+(\d+)', re.M
    )
    # in-function context line
    _TRACEBACK_FUNC = re.compile(r"in\s+(\w+)")
    # AssertionError with !=
    _ASSERT_NE = re.compile(
        r"(?:AssertionError|assert):?\s+(.*?)\s*!=\s*(.*)", re.M
    )
    # generic AssertionError
    _ASSERT_MSG = re.compile(
        r"(?:AssertionError|assert):?\s*(.*)", re.M
    )
    # Exception with message
    _EXCEPTION = re.compile(
        r"(\w+(?:Error|Exception)):\s*(.*)"
    )

    @classmethod
    def can_parse(cls, log_text: str) -> bool:
        return bool(cls._PYTEST_FAILURES.search(log_text) or
                    cls._PYTEST_FAILED.search(log_text) or
                    cls._UNITTEST_FAIL.search(log_text))

    @classmethod
    def parse(cls, instance_id: str, log_text: str,
              raw_log_path: str = "") -> FailureWitness:
        failed_tests = cls._extract_failed_tests(log_text)
        stack_frames = cls._extract_stack_frames(log_text)
        error_msg = cls._extract_error_message(log_text, stack_frames)
        expected, actual = cls._extract_expected_actual(log_text)
        top_repo = cls._extract_top_repo_frames(stack_frames, instance_id)
        failure_type = cls._classify_failure(log_text)

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type=failure_type,
            test_framework="pytest",
            failed_tests=failed_tests,
            error_message=error_msg[:2000] if error_msg else "",
            stack_trace=stack_frames,
            top_repo_frames=top_repo,
            expected=expected,
            actual=actual,
            mode="post_validation_output",
            source="post_validation_output",
            source_type="validation_failure",
            raw_output_path=raw_log_path,
            version="v2.0",
        )

    @classmethod
    def _extract_failed_tests(cls, text: str) -> list[str]:
        tests = []
        for m in cls._PYTEST_FAILED.finditer(text):
            test_path = m.group(1)
            test_name = m.group(2)
            tests.append(f"{test_path}::{test_name}")
        if not tests:
            for m in cls._PYTEST_TEST.finditer(text):
                tests.append(f"{m.group(1)}::{m.group(2)}")
        if not tests:
            # unittest format: FAIL: test_name (module.class)
            _unittest_fail = re.compile(r"^FAIL:\s+(\S+)", re.M)
            for m in _unittest_fail.finditer(text):
                tests.append(m.group(1))
        return tests

    @classmethod
    def _extract_stack_frames(cls, text: str) -> list[str]:
        frames = []
        for m in cls._TRACEBACK_FILE.finditer(text):
            frames.append(f"File \"{m.group(1)}\", line {m.group(2)}")
        return frames

    @classmethod
    def _extract_error_message(cls, text: str,
                                frames: list[str]) -> str:
        # Try to find error after traceback
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = cls._ASSERT_NE.search(line)
            if m:
                return line.strip()[:500]
            m = cls._ASSERT_MSG.search(line)
            if m:
                return line.strip()[:500]
            m = cls._EXCEPTION.search(line)
            if m:
                return line.strip()[:500]
        # Last few lines often have the error
        tail = "\n".join(lines[-10:])
        for m in cls._ASSERT_NE.finditer(tail):
            return m.group(0)[:500]
        for m in cls._ASSERT_MSG.finditer(tail):
            return m.group(0)[:500]
        for m in cls._EXCEPTION.finditer(tail):
            return m.group(0)[:500]
        if lines:
            return lines[-1].strip()[:500]
        return ""

    @classmethod
    def _extract_expected_actual(cls, text: str
                                  ) -> tuple[str | None, str | None]:
        for m in cls._ASSERT_NE.finditer(text):
            expected = m.group(1).strip()
            actual = m.group(2).strip()
            if len(expected) < 500 and len(actual) < 500:
                return (expected, actual)
        return (None, None)

    @classmethod
    def _extract_top_repo_frames(cls, frames: list[str],
                                  instance_id: str) -> list[str]:
        """Return frames pointing to /testbed/ (the repo checkout)."""
        result = []
        for f in frames:
            if "/testbed/" in f:
                result.append(f)
        return result[:10]

    @classmethod
    def _classify_failure(cls, text: str) -> str:
        if "AssertionError" in text:
            return "assertion_error"
        if "Timeout" in text:
            return "timeout"
        if "ImportError" in text:
            return "import_error"
        if "TypeError" in text:
            return "type_error"
        if "ValueError" in text:
            return "value_error"
        if "AttributeError" in text:
            return "attribute_error"
        if "KeyError" in text:
            return "key_error"
        if "IndexError" in text:
            return "index_error"
        return "test_failure"
