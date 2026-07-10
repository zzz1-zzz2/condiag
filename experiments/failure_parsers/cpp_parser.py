"""Parser for C/C++ build and test output.

Handles:
1. Compile errors (gcc/clang:  file.c:N: error: ...)
2. Build failures (make/cmake:  make[N]: *** Error N)
3. Catch2 test framework output
4. Google Test output
"""
from __future__ import annotations

import re

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser


@register_parser("cpp_parser")
class CppParser(FailureParser):
    """Parse C/C++ compile/test failure output."""

    framework = "cpp_test"
    priority = 100

    _COMPILE_ERROR = re.compile(
        r"^\s*(?:/.*?)?(\S+\.(?:c|cpp|cxx|cc|h|hpp)):(\d+):(\d+):\s*error:\s*(.*)",
        re.M
    )
    _COMPILE_WARNING = re.compile(
        r"^\s*(?:/.*?)?(\S+\.(?:c|cpp|cxx|cc|h|hpp)):(\d+):(\d+):\s*warning:\s*(.*)",
        re.M
    )
    _MAKE_ERROR = re.compile(
        r"make(?:\[\d+\])?:\s*\*\*\*\s*\[.*\]\s*(?:Error|exit)\s*(\d+)", re.M
    )
    _CMAKE_ERROR = re.compile(
        r"CMake\s+(Error|Warning)\s+at", re.M
    )
    _LINK_ERROR = re.compile(
        r"(undefined reference|ld:\s*cannot find|cannot find -l|collect2:\s*error)", re.I | re.M
    )
    _CATCH2_FAILED = re.compile(
        r"FAILED\s+TEST", re.M
    )
    _CATCH2_FAIL_LINE = re.compile(
        r"^\s*(FAILED|FAIL)\s*:", re.M
    )
    _GTEST_FAIL = re.compile(
        r"^\[  FAILED  \]", re.M
    )
    _GTEST_FAILURE = re.compile(
        r"^\[  FAILED  \]\s+(.+)$", re.M
    )

    @classmethod
    def can_parse(cls, log_text: str) -> bool:
        if cls._COMPILE_ERROR.search(log_text):
            return True
        if cls._MAKE_ERROR.search(log_text):
            return True
        if cls._LINK_ERROR.search(log_text):
            return True
        if cls._CATCH2_FAILED.search(log_text) or cls._CATCH2_FAIL_LINE.search(log_text):
            return True
        if cls._GTEST_FAIL.search(log_text):
            return True
        if cls._CMAKE_ERROR.search(log_text):
            return True
        return False

    @classmethod
    def parse(cls, instance_id: str, log_text: str,
              raw_log_path: str = "") -> FailureWitness:
        compile_errors = cls._extract_compile_errors(log_text)
        link_errors = list(cls._LINK_ERROR.finditer(log_text))
        make_errors = list(cls._MAKE_ERROR.finditer(log_text))
        failed_tests = cls._extract_failed_tests(log_text)

        # Classify stage
        if compile_errors or link_errors:
            stage = "dependency_or_environment_failure"
            ftype = "compile_error"
            error_msg = compile_errors[0][:500] if compile_errors else "compile/build failure"
        elif make_errors:
            stage = "dependency_or_environment_failure"
            ftype = "build_failure"
            error_msg = f"make failed: {make_errors[0].group(0)[:200]}"
        elif failed_tests:
            stage = "validation_failure"
            ftype = "test_failure"
            error_msg = f"{len(failed_tests)} test(s) failed"
        else:
            stage = "validation_failure"
            ftype = "test_failure"
            error_msg = cls._extract_fallback(log_text)

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage=stage,
            failure_type=ftype,
            test_framework="cpp_test",
            failed_tests=failed_tests[:20],
            error_message=error_msg[:2000],
            stack_trace=cls._extract_stack(log_text),
            top_repo_frames=[],
            mode="post_validation_output",
            source="post_validation_output",
            source_type="validation_failure",
            raw_output_path=raw_log_path,
            version="v2.0",
        )

    @classmethod
    def _extract_compile_errors(cls, text: str) -> list[str]:
        errors = []
        for m in cls._COMPILE_ERROR.finditer(text):
            errors.append(f"{m.group(1)}:{m.group(2)}: error: {m.group(4).strip()[:200]}")
        return errors[:10]

    @classmethod
    def _extract_failed_tests(cls, text: str) -> list[str]:
        tests = []
        for m in cls._GTEST_FAILURE.finditer(text):
            tests.append(m.group(1).strip())
        return tests[:20]

    @classmethod
    def _extract_stack(cls, text: str) -> list[str]:
        frames = []
        for m in cls._COMPILE_ERROR.finditer(text):
            frames.append(f"File \"{m.group(1)}\", line {m.group(2)}")
        return frames[:20]

    @classmethod
    def _extract_fallback(cls, text: str) -> str:
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        for line in reversed(lines[-10:]):
            if "error" in line.lower() or "fail" in line.lower():
                return line[:500]
        return lines[-1][:500] if lines else "test_failure"
