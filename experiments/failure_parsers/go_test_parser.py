"""Parser for Go test output."""
from __future__ import annotations

import re

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser


@register_parser("go_test_parser")
class GoTestParser(FailureParser):
    """Parse Go test output, handling build failures and assertion failures.

    Go test output formats:
    1. Build failure:  FAIL package/path [build failed]
    2. Test failure:   --- FAIL: TestFoo (0.01s)
                       foo_test.go:42: expected 3, got 4
    3. Panic:          panic: runtime error: ...
    4. Compile error:  # package/path
                       file.go:N:undefined: symbol
    """

    framework = "go_test"
    priority = 100

    # --- FAIL: TestFoo (0.01s)
    _GO_FAIL_TEST = re.compile(r"^\s*---\s+FAIL:\s+(Test\w+)\s+", re.M)
    # Assertion line in Go:  file_test.go:42: message
    _GO_ASSERT_LINE = re.compile(r"^\s*(\S+\.go):(\d+):\s*(.*)", re.M)
    # Build failure
    _GO_BUILD_FAIL = re.compile(r"FAIL\s+(\S+)\s+\[build failed\]", re.M)
    # Compile error header:  # package/path
    _GO_COMPILE_ERR = re.compile(r"^#\s+(\S+)", re.M)
    # Compile error detail:  file.go:N:N: undefined: X  or  file.go:N:N: message
    _GO_COMPILE_DETAIL = re.compile(
        r"^\s*(\S+\.go):(\d+):(?:\d+:)?\s*(.*)", re.M
    )
    # Package FAIL line
    _GO_PACKAGE_FAIL = re.compile(r"^FAIL\s+(\S+)", re.M)
    # Package ok/pass
    _GO_PACKAGE_OK = re.compile(r"^ok\s+(\S+)", re.M)
    # Panic
    _GO_PANIC = re.compile(r"panic:\s*(.*)", re.M)
    # Goroutine stack: goroutine N [running]:
    _GO_GOROUTINE = re.compile(r"goroutine\s+\d+", re.M)
    # Stack frame:  path/file.go:N +0xM
    _GO_STACK_FRAME = re.compile(r"^\s*(\S+\.go):(\d+)(?:\s+\+0x[0-9a-f]+)?", re.M)

    @classmethod
    def can_parse(cls, log_text):
        # Must have Go test markers
        if cls._GO_FAIL_TEST.search(log_text):
            return True
        if cls._GO_BUILD_FAIL.search(log_text):
            return True
        # Has package-level FAIL and test file references
        if cls._GO_PACKAGE_FAIL.search(log_text) and cls._GO_ASSERT_LINE.search(log_text):
            return True
        return False

    @classmethod
    def parse(cls, instance_id, log_text, raw_log_path=""):
        log_text = log_text  # Keep reference

        # Check for build failures
        build_failures = cls._GO_BUILD_FAIL.findall(log_text)
        compile_errors = cls._extract_compile_errors(log_text)

        # Check for test failures
        failed_tests = cls._extract_failed_tests(log_text)
        assertion_lines = cls._extract_assertion_lines(log_text)
        stack_frames = cls._extract_stack_frames(log_text)

        # Error message
        error_msg = cls._extract_error_message(
            log_text, failed_tests, build_failures, compile_errors
        )

        # Expected/actual
        expected, actual = cls._extract_expected_actual(log_text)

        # Failure type
        ftype = cls._classify_failure(log_text, build_failures)

        # Determine stage
        if build_failures or compile_errors:
            stage = "dependency_or_environment_failure"
        else:
            stage = "validation_failure"

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage=stage,
            failure_type=ftype,
            test_framework="go_test",
            failed_tests=failed_tests[:20],
            error_message=error_msg[:2000],
            stack_trace=stack_frames[:20],
            expected=expected,
            actual=actual,
            mode="post_validation_output",
            source="post_validation_output",
            source_type="validation_failure",
            raw_output_path=raw_log_path,
            version="v2.0",
        )

    @classmethod
    def _extract_failed_tests(cls, text):
        tests = []
        for m in cls._GO_FAIL_TEST.finditer(text):
            tests.append(m.group(1))
        return tests

    @classmethod
    def _extract_assertion_lines(cls, text):
        """Extract assertion error messages from test output."""
        lines = []
        for m in cls._GO_ASSERT_LINE.finditer(text):
            filepath = m.group(1)
            lineno = m.group(2)
            msg = m.group(3).strip()
            if msg and len(msg) < 300:
                lines.append(msg)
        return lines

    @classmethod
    def _extract_compile_errors(cls, text):
        """Extract compile/build error messages."""
        errors = []
        current_pkg = ""
        for line in text.splitlines():
            m = cls._GO_COMPILE_ERR.match(line)
            if m:
                current_pkg = m.group(1)
                continue
            dm = cls._GO_COMPILE_DETAIL.match(line)
            if dm:
                errors.append(f"{dm.group(1)}:{dm.group(2)}: {dm.group(3)[:200]}")
        return errors

    @classmethod
    def _extract_stack_frames(cls, text):
        frames = []
        # Test assertion frames
        for m in cls._GO_ASSERT_LINE.finditer(text):
            frames.append(f'File "{m.group(1)}", line {m.group(2)}')
        # Goroutine stack frames
        for m in cls._GO_STACK_FRAME.finditer(text):
            frame = f'File "{m.group(1)}", line {m.group(2)}'
            if frame not in frames:
                frames.append(frame)
        return frames[:20]

    @classmethod
    def _extract_error_message(cls, text, failed_tests, build_failures, compile_errors):
        if compile_errors:
            return compile_errors[0][:500]
        if build_failures:
            return f"Build failed: {build_failures[0]}"
        if failed_tests:
            assertion_lines = cls._extract_assertion_lines(text)
            if assertion_lines:
                return assertion_lines[0][:500]
        # Panic
        panic_m = cls._GO_PANIC.search(text)
        if panic_m:
            return f"Panic: {panic_m.group(1)[:200]}"
        return "test_failure"

    @classmethod
    def _extract_expected_actual(cls, text):
        for m in cls._GO_ASSERT_LINE.finditer(text):
            msg = m.group(3)
            # Common Go assertion formats:
            # "expected 3, got 4"
            # "expected: X, got: Y"
            # "expected <x> to equal <y>"
            exp_got = re.search(
                r"(?:expected|expect|want)[:\s]*([^,]+)(?:,| but got| got| received)[:\s]*(.+)",
                msg, re.I
            )
            if exp_got:
                return exp_got.group(1).strip()[:200], exp_got.group(2).strip()[:200]

            # testify: assert.Equal(t, expected, actual, msg)
            testify = re.search(
                r"expected:\s*\"(.+?)\"\s*,\s*actual:\s*\"(.+?)\"",
                msg, re.I
            )
            if testify:
                return testify.group(1)[:200], testify.group(2)[:200]

        return None, None

    @classmethod
    def _classify_failure(cls, text, build_failures):
        if build_failures:
            return "build_failure"
        if cls._GO_PANIC.search(text):
            return "panic"
        if "timeout" in text.lower():
            return "timeout"
        return "test_failure"
