"""Parser for JUnit / Gradle / Maven test output.

Handles:
1. Gradle build failures (BUILD FAILED, FAILURE: Build failed with an exception)
2. Maven test failures (Tests run: X, Failures: Y)
3. JUnit assertion failures (java.lang.AssertionError, expected:... but was:...)
"""
from __future__ import annotations

import re

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser


@register_parser("junit_gradle_parser")
class JUnitGradleParser(FailureParser):
    """Parse JUnit / Gradle / Maven failure output."""

    framework = "junit_gradle"
    priority = 100

    _GRADLE_BUILD_FAILED = re.compile(r"BUILD FAILED", re.M)
    _GRADLE_FAILURE = re.compile(r"FAILURE:\s*Build failed with an exception", re.M)
    _GRADLE_WHAT_WRONG = re.compile(r"\*\s*What went wrong:\s*\n(.*?)(?:\n\s*\*|$)", re.M | re.DOTALL)
    _GRADLE_TASK_FAILED = re.compile(r"^>\s*Task\s+(\S+)\s+FAILED", re.M)
    _GRADLE_ERROR = re.compile(r"^(>|Execution failed for)", re.M)

    _MAVEN_TEST_RUN = re.compile(
        r"Tests run:\s*(\d+),\s*Failures:\s*(\d+),\s*Errors:\s*(\d+)", re.M
    )
    _MAVEN_BUILD_FAILURE = re.compile(r"BUILD FAILURE", re.M)
    _MAVEN_FAILED_TEST = re.compile(
        r"Failed tests:\s*\n\s+([^\n]+)", re.M
    )

    _JUNIT_ASSERTION = re.compile(
        r"(?:java\.lang\.)?(AssertionError|AssertionFailedError|ComparisonFailure):\s*(.*)",
        re.M
    )
    _JUNIT_EXPECTED_ACTUAL = re.compile(
        r"expected:\s*<(.*?)>\s*but\s*was:\s*<(.*?)>", re.M
    )
    _JUNIT_TEST_FAILED = re.compile(
        r"Tests FAILED", re.M
    )
    _JUNIT_FAILURE = re.compile(
        r"FAILURE:\s*(.*)", re.M
    )

    @classmethod
    def can_parse(cls, log_text: str) -> bool:
        if cls._GRADLE_BUILD_FAILED.search(log_text) and cls._GRADLE_FAILURE.search(log_text):
            return True
        if cls._MAVEN_BUILD_FAILURE.search(log_text) and cls._MAVEN_TEST_RUN.search(log_text):
            return True
        if cls._JUNIT_TEST_FAILED.search(log_text):
            return True
        if cls._GRADLE_TASK_FAILED.search(log_text):
            return True
        return False

    @classmethod
    def parse(cls, instance_id: str, log_text: str,
              raw_log_path: str = "") -> FailureWitness:
        # Gradle build failure
        if cls._GRADLE_BUILD_FAILED.search(log_text):
            return cls._parse_gradle(instance_id, log_text, raw_log_path)

        # Maven build failure
        if cls._MAVEN_BUILD_FAILURE.search(log_text):
            return cls._parse_maven(instance_id, log_text, raw_log_path)

        # JUnit direct output
        return cls._parse_junit(instance_id, log_text, raw_log_path)

    @classmethod
    def _parse_gradle(cls, instance_id, log_text, raw_log_path):
        error_msg = "Gradle build failed"
        ww = cls._GRADLE_WHAT_WRONG.search(log_text)
        if ww:
            error_msg = ww.group(1).strip()[:500]
        else:
            first_err = cls._GRADLE_ERROR.search(log_text)
            if first_err:
                after = log_text[first_err.end():].splitlines()
                if after:
                    error_msg = after[0].strip()[:300]

        failed_tasks = cls._GRADLE_TASK_FAILED.findall(log_text)

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="dependency_or_environment_failure",
            failure_type="build_failure",
            test_framework="junit_gradle",
            failed_tests=list(failed_tasks),
            error_message=error_msg[:2000],
            stack_trace=[],
            top_repo_frames=[],
            mode="post_validation_output",
            source="post_validation_output",
            source_type="validation_failure",
            raw_output_path=raw_log_path,
            version="v2.0",
        )

    @classmethod
    def _parse_maven(cls, instance_id, log_text, raw_log_path):
        test_runs = cls._MAVEN_TEST_RUN.findall(log_text)
        failed_tests = []
        for runs, fails, errs in test_runs:
            if int(fails) > 0 or int(errs) > 0:
                failed_tests.append(f"run={runs} fail={fails} err={errs}")

        ft = cls._MAVEN_FAILED_TEST.findall(log_text)
        for t in ft:
            for line in t.splitlines():
                line = line.strip()
                if line and not line.startswith("[") and ":" in line:
                    failed_tests.append(line)
                    break

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure" if not failed_tests else "validation_failure",
            failure_type="test_failure",
            test_framework="junit_gradle",
            failed_tests=failed_tests[:20],
            error_message=f"{len(failed_tests)} test(s) failed" if failed_tests else "build_failure",
            stack_trace=[],
            top_repo_frames=[],
            expected=None, actual=None,
            mode="post_validation_output",
            source="post_validation_output",
            source_type="validation_failure",
            raw_output_path=raw_log_path,
            version="v2.0",
        )

    @classmethod
    def _parse_junit(cls, instance_id, log_text, raw_log_path):
        expected, actual = None, None
        exp_act = cls._JUNIT_EXPECTED_ACTUAL.search(log_text)
        if exp_act:
            expected = exp_act.group(1)[:200]
            actual = exp_act.group(2)[:200]

        error_msg = ""
        for m in cls._JUNIT_ASSERTION.finditer(log_text):
            error_msg = f"{m.group(1)}: {m.group(2).strip()[:200]}"
            break

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type="assertion_error" if expected else "test_failure",
            test_framework="junit_gradle",
            failed_tests=[],  # JUnit XML output is too verbose for line parsing
            error_message=error_msg[:2000] or "test_failure",
            stack_trace=[],
            top_repo_frames=[],
            expected=expected, actual=actual,
            mode="post_validation_output",
            source="post_validation_output",
            source_type="validation_failure",
            raw_output_path=raw_log_path,
            version="v2.0",
        )
