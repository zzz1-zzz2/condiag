"""Parser for Rust/Cargo test output."""
from __future__ import annotations

import re

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser


@register_parser("cargo_test_parser")
class CargoTestParser(FailureParser):
    """Parse Rust cargo test output.

    Cargo test output formats:
    1. Individual test failure:
       test binary::after_match1_explicit ... FAILED
    2. Panic with expected vs actual:
       thread '...' panicked at tests/file.rs:N:N:
       expected:
       ...
       got:
       ...
    3. Summary:
       test result: FAILED. 225 passed; 11 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.63s
    4. Cargo error:
       error: test failed, to rerun pass `--test integration`
    """

    framework = "cargo_test"
    priority = 100

    # Test failure: "test binary::after_match1_explicit ... FAILED"
    _CARGO_TEST_FAILED = re.compile(
        r"test\s+(\S+)\s+\.\.\.\s+FAILED", re.M
    )
    # Test result summary: "test result: FAILED. N passed; M failed..."
    _CARGO_TEST_RESULT = re.compile(
        r"test result: FAILED\.\s*(\d+) passed;\s*(\d+) failed", re.M
    )
    # Panic with location: "thread '...' panicked at tests/file.rs:N:N:"
    _CARGO_PANIC = re.compile(
        r"thread\s+'([^']+)'\s+panicked\s+at\s+(\S+):(\d+):(\d+)", re.M
    )
    # Expected block: "^expected:" line
    _CARGO_EXPECTED = re.compile(r"^expected:", re.M)
    # Got block: "^got:" line
    _CARGO_GOT = re.compile(r"^got:", re.M)
    # Separator line: ~~~~~~~~~~~~~
    _CARGO_SEPARATOR = re.compile(r"^~{10,}", re.M)

    @classmethod
    def can_parse(cls, log_text):
        if cls._CARGO_TEST_FAILED.search(log_text):
            return True
        if cls._CARGO_TEST_RESULT.search(log_text):
            return True
        return False

    @classmethod
    def parse(cls, instance_id, log_text, raw_log_path=""):
        failed_tests = cls._extract_failed_tests(log_text)
        error_msg = cls._extract_error_message(log_text, failed_tests)
        expected, actual = cls._extract_expected_actual(log_text)
        stack_frames = cls._extract_stack_frames(log_text)

        ftype = cls._classify_failure(log_text, error_msg)

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type=ftype,
            test_framework="cargo_test",
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
        for m in cls._CARGO_TEST_FAILED.finditer(text):
            tests.append(m.group(1))
        return tests

    @classmethod
    def _extract_error_message(cls, text, failed_tests):
        # Try to get panic message first
        panic_m = cls._CARGO_PANIC.search(text)
        if panic_m:
            # Get the line after the panic location
            lines = text.splitlines()
            for i, line in enumerate(lines):
                if "panicked at" in line and i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line and not next_line.startswith("~"):
                        return next_line[:500]
            return f"Panicked at {panic_m.group(2)}:{panic_m.group(3)}"

        # Summary line
        result_m = cls._CARGO_TEST_RESULT.search(text)
        if result_m:
            passed = result_m.group(1)
            failed = result_m.group(2)
            if failed_tests:
                return f"{len(failed_tests)} test(s) failed ({passed} passed, {failed} failed)"
            return f"Test run failed ({passed} passed, {failed} failed)"

        if failed_tests:
            return f"{len(failed_tests)} test(s) failed"
        return "test_failure"

    @classmethod
    def _extract_expected_actual(cls, text):
        """Extract expected/got from cargo test panic blocks.

        Format:
        ---- binary::after_match1_explicit stdout ----
        expected:
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        <expected content>
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

        got:
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        <actual content>
        ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
        """
        lines = text.splitlines()
        expected_lines = []
        actual_lines = []
        in_expected = False
        in_actual = False
        separator_count = 0

        for line in lines:
            if re.match(r"^expected:", line):
                in_expected = True
                in_actual = False
                separator_count = 0
                continue
            if re.match(r"^got:", line):
                in_expected = False
                in_actual = True
                separator_count = 0
                continue
            if re.match(r"^~{10,}", line):
                separator_count += 1
                if separator_count >= 2:
                    in_expected = False
                    in_actual = False
                continue
            if re.match(r"^note:", line):
                in_expected = False
                in_actual = False
                continue

            if in_expected:
                expected_lines.append(line.strip())
            elif in_actual:
                actual_lines.append(line.strip())

        expected = "\n".join(expected_lines[:10])[:500] if expected_lines else None
        actual = "\n".join(actual_lines[:10])[:500] if actual_lines else None
        return expected, actual

    @classmethod
    def _extract_stack_frames(cls, text):
        frames = []
        for m in cls._CARGO_PANIC.finditer(text):
            frames.append(f'File "{m.group(2)}", line {m.group(3)}')
        return frames[:20]

    @classmethod
    def _classify_failure(cls, text, error_msg):
        if "panicked" in text.lower():
            return "panic"
        if "assertion" in error_msg.lower() or "assert" in text.lower():
            return "assertion_error"
        return "test_failure"
