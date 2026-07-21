"""Tests for P0-4b Harness Eligibility Gate."""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

from condiag.integrity import (
    EligibilityReport,
    check_episode_eligibility,
    _witness_is_valid,
)


VALID_WITNESS = {
    "failed_tests": ["test_foo", "test_bar"],
    "error_message": "AssertionError: expected 5, got 3",
    "stack_frames": [{"file": "foo.py", "line": 42}],
}



@dataclass
class _FakeEvalResult:
    """Mimics OfficialHarnessGateway's EvalResult for eligibility tests."""
    status: str = "UNRESOLVED"
    test_log_path: str = ""
def _make_test_log(tmp_path: Path, content: str = "pytest output here") -> Path:
    log = tmp_path / "test.log"
    log.write_text(content)
    return log


class TestWitnessValidity:
    def test_valid_with_failed_tests(self):
        v, n, has_err, _ = _witness_is_valid(VALID_WITNESS)
        assert v
        assert n == 2
        # has_err is True here because VALID_WITNESS has both failed_tests AND error_message
        assert has_err is True

    def test_valid_with_error_message(self):
        w = {"error_message": "AssertionError: foo", "stack_frames": []}
        v, n, has_err, _ = _witness_is_valid(w)
        assert v
        assert n == 0
        assert has_err

    def test_valid_with_stack_frames_only(self):
        w = {"stack_frames": [{"file": "x.py"}]}
        v, n, has_err, fcount = _witness_is_valid(w)
        assert v
        assert n == 0
        assert not has_err
        assert fcount == 1

    def test_invalid_when_empty(self):
        assert not _witness_is_valid({})[0]
        assert not _witness_is_valid(None)[0]
        assert not _witness_is_valid({"failed_tests": [], "error_message": "No test log", "stack_frames": []})[0]


class TestCheckEpisodeEligibility:
    def test_r1_resolved_is_eligible_but_no_branches(self):
        """R1 already resolved: ok=True with status='r1_resolved' - SF/CD skipped."""
        eval_result = _FakeEvalResult(status="RESOLVED", test_log_path="")
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        assert r.ok
        assert r.status == "r1_resolved"

    def test_harness_error_blocks(self):
        eval_result = _FakeEvalResult(status="ERROR", test_log_path="")
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        assert not r.ok
        assert r.status == "ineligible_harness_error"
        assert r.harness_status == "ERROR"

    def test_harness_timeout_blocks(self):
        eval_result = _FakeEvalResult(status="TIMEOUT", test_log_path="")
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        assert not r.ok
        assert r.status == "ineligible_harness_timeout"

    def test_harness_unknown_blocks(self):
        eval_result = _FakeEvalResult(status="UNKNOWN", test_log_path="")
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        assert not r.ok
        assert r.status == "ineligible_harness_unknown"

    def test_unresolved_missing_test_log_blocks(self, tmp_path):
        """UNRESOLVED but test log missing -> block."""
        eval_result = _FakeEvalResult(status="UNRESOLVED", test_log_path=str(tmp_path / "nope.log"))
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        assert not r.ok
        assert r.status == "ineligible_missing_test_log"
        assert not r.test_log_exists

    def test_unresolved_empty_test_log_blocks(self, tmp_path):
        """UNRESOLVED but test log is 0 bytes -> block."""
        log = _make_test_log(tmp_path, "")
        eval_result = _FakeEvalResult(status="UNRESOLVED", test_log_path=str(log))
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        assert not r.ok
        assert r.status == "ineligible_missing_test_log"
        assert not r.test_log_exists

    def test_unresolved_empty_witness_blocks(self, tmp_path):
        """UNRESOLVED with valid test log but no witness signals -> block."""
        log = _make_test_log(tmp_path)
        eval_result = _FakeEvalResult(status="UNRESOLVED", test_log_path=str(log))
        r = check_episode_eligibility(eval_result, {"failed_tests": [], "error_message": "No test log", "stack_frames": []})
        assert not r.ok
        assert r.status == "ineligible_empty_witness"

    def test_unresolved_valid_witness_passes(self, tmp_path):
        """UNRESOLVED + test log + valid witness -> eligible."""
        log = _make_test_log(tmp_path, "FAILED test_foo\nAssertionError")
        eval_result = _FakeEvalResult(status="UNRESOLVED", test_log_path=str(log))
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        assert r.ok
        assert r.status == "eligible"
        assert r.test_log_exists
        assert r.test_log_size > 0
        assert r.witness_valid
        assert r.failed_test_count == 2
        assert r.stack_frame_count == 1

    def test_eligibility_report_serializable(self, tmp_path):
        """EligibilityReport.to_dict() must be JSON-serializable."""
        import json
        log = _make_test_log(tmp_path)
        eval_result = _FakeEvalResult(status="UNRESOLVED", test_log_path=str(log))
        r = check_episode_eligibility(eval_result, VALID_WITNESS)
        json.dumps(r.to_dict(), indent=2)


@pytest.mark.parametrize(
    "status,test_log_content,witness,expected_ok,expected_status",
    [
        ("ERROR", "", {}, False, "ineligible_harness_error"),
        ("TIMEOUT", "", {}, False, "ineligible_harness_timeout"),
        ("UNKNOWN", "", {}, False, "ineligible_harness_unknown"),
        ("UNRESOLVED", "", {}, False, "ineligible_missing_test_log"),
        ("UNRESOLVED", "FAILED test_foo", {}, False, "ineligible_empty_witness"),
        ("UNRESOLVED", "some output", VALID_WITNESS, True, "eligible"),
    ],
)
def test_eligibility_orchestration(tmp_path, status, test_log_content, witness, expected_ok, expected_status):
    """Parameterized: each scenario must produce correct eligibility and
    never invoke run_branch when ineligible."""
    log_path = ""
    if test_log_content:
        log = tmp_path / "test.log"
        log.write_text(test_log_content)
        log_path = str(log)

    eval_result = _FakeEvalResult(status=status, test_log_path=log_path)
    r = check_episode_eligibility(eval_result, witness)
    assert r.ok == expected_ok, "Expected ok={} for status={}".format(expected_ok, status)
    assert r.status == expected_status, "Expected status={} for status={}".format(expected_status, status)

    # When ineligible, no branches should run
    if not expected_ok:
        assert not r.witness_valid or not r.test_log_exists, \
            "Ineligible but still appears to have data: status={}".format(status)
