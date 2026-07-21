"""Pytest test_log extractor — parse SWE-bench evaluation stdout for pytest instances.

Extracts structured signals from the raw test_output.txt produced by
swebench.harness.run_instance() when the project uses pytest.

Two frame formats handled:
  1. Pytest short format:  astropy/x.py:42: in func_name  (was MISSED in v4)
  2. File format:          File "/path/x.py", line 42, in func_name (pip build frames only)

The key fix over v4's extract_witness:
  - v4 only matched format 2 (all 14 frames were pip build frames, 0 test frames extracted)
  - v5 matches BOTH formats and separates build frames from test failure frames
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from condiag.diagnosis.signals.enums import ErrorType, TestFramework
from condiag.diagnosis.signals.schema import (
    StackFrame,
    TestLogSignals,
)
from condiag.diagnosis.signals.frame_normalizer import normalize_frame

logger = logging.getLogger("condiag.diagnosis.signals.pytest_extractor")

# ── Patterns ────────────────────────────────────────────────────────

# Test result lines
_RE_FAILED = re.compile(r"^FAILED\s+(\S+(?:::\S+)?(?:\S*)?)")
_RE_PASSED = re.compile(r"^PASSED\s+(\S+(?:::\S+)?)\s*$")

# Stack frame: pytest short format (the main test failure format)
# Example: astropy/coordinates/baseframe.py:1202: in transform_to
_RE_PYTEST_FRAME = re.compile(r"^(\S+?)\.py:(\d+): in (\w+)")

# Stack frame: File "..." format (pip/build errors, NOT test failures)
# Example: File "/opt/.../build_meta.py", line 317, in run_setup
_RE_FILE_FRAME = re.compile(
    r'File\s+"([^"]+)",\s*line\s+(\d+)(?:,\s*in\s+(\w+))?'
)

# Error lines
_RE_ERROR_LINE = re.compile(r"E\s+(?:\s+)?(\w+(?:Error|Exception|Failure))(?::\s*(.*))?")
_RE_ERROR_GENERIC = re.compile(
    r"(AssertionError|AttributeError|TypeError|ValueError|ImportError|"
    r"ModuleNotFoundError|KeyError|IndexError|RuntimeError|OSError|"
    r"StopIteration)[^:\n]*:\s*([^\n]+)"
)

# Assertion detail (the source code line that failed)
_RE_ASSERTION_LINE = re.compile(r"^>\s+\S")


def extract_test_log(test_log_path: str | Path) -> TestLogSignals:
    """Extract all signals from a pytest-format test_log file.

    Args:
        test_log_path: Path to test_output.txt from SWE-bench evaluation.

    Returns:
        TestLogSignals with all extractable fields populated.
    """
    raw = Path(test_log_path).read_text(encoding="utf-8", errors="replace")
    lines = raw.split("\n")

    signals = TestLogSignals(framework=TestFramework.PYTEST)

    _extract_test_results(signals, lines)
    _extract_stack_frames(signals, raw, lines)
    _extract_errors(signals, lines)
    _extract_call_chains(signals)

    return signals


# ── Internal: test results ──────────────────────────────────────────


def _extract_test_results(signals: TestLogSignals, lines: list[str]) -> None:
    """Extract FAILED and PASSED test lists."""
    for line in lines:
        stripped = line.strip()

        m = _RE_FAILED.match(stripped)
        if m:
            signals.failed_tests.append(m.group(1))
            continue

        m = _RE_PASSED.match(stripped)
        if m:
            signals.passed_tests.append(m.group(1))
            continue

    # Count total tests from the summary line
    for line in reversed(lines):
        m = re.search(r"(\d+)\s+passed", line)
        n = re.search(r"(\d+)\s+failed", line)
        if m or n:
            total = int(m.group(1)) if m else 0
            total += int(n.group(1)) if n else 0
            signals.num_tests_run = total
            break


# ── Internal: stack frames ──────────────────────────────────────────


def _extract_stack_frames(
    signals: TestLogSignals, raw: str, lines: list[str]
) -> None:
    """Extract stack frames, separating test failures from build errors.

    CRITICAL FIX over v4 extract_witness:
      v4 only matched 'File "..."' format, which finds ONLY pip build frames.
      pytest short format frames ('file.py:N: in func') were completely missed.
    """
    # Find FAILURES section boundaries
    # The pytest FAILURES header looks like:
    #   =================================== FAILURES ===================================
    # NOT starting with "FAILURES". So we check for "FAILURES" anywhere in the line.
    failure_start = None
    summary_start = None
    for i, line in enumerate(lines):
        if "FAILURES" in line and "===" in line:
            failure_start = i
        if failure_start is not None and "short test summary" in line:
            summary_start = i
            break
    if failure_start is None:
        failure_start = 0
    if summary_start is None:
        summary_start = len(lines)

    # Isolate the test execution region vs build/setup region
    # Build region: before the tests start (~ the "pytest -rA" command)
    test_start = None
    for i, line in enumerate(lines):
        if "pytest" in line and "-rA" in line and "test_" in line:
            test_start = i
            break
        # Also detect git checkout of test patch
        if "git checkout" in line and "test_" in line:
            test_start = i + 10  # a few lines after checkout
            break
    if test_start is None:
        test_start = 0

    # Parse frames in test failure region (between FAILURES and summary)
    for i in range(failure_start, summary_start):
        line = lines[i] if i < len(lines) else ""

        # Pytest short format: astropy/utils/iers/iers.py:271: in mjd_utc
        m = _RE_PYTEST_FRAME.search(line)
        if m:
            fpath = m.group(1) + ".py"  # regex consumes .py, restore it
            if not fpath.startswith("/"):
                fpath = _resolve_repo_path(fpath)
            signals.stack_frames.append(
                normalize_frame(fpath, int(m.group(2)), m.group(3))
            )
            continue

        # File "..." format in failure section (unusual for pytest, but handle it)
        m = _RE_FILE_FRAME.search(line)
        if m:
            fpath = m.group(1)
            if fpath.startswith("/testbed/"):
                fpath = fpath[len("/testbed/"):]
            signals.stack_frames.append(
                normalize_frame(fpath, int(m.group(2)), m.group(3) or "")
            )
            continue

    # Build frames: File "..." format in the setup phase (before test section)
    for i in range(0, test_start):
        line = lines[i] if i < len(lines) else ""
        m = _RE_FILE_FRAME.search(line)
        if m:
            fpath = m.group(1)
            signals.build_frames.append(
                normalize_frame(fpath, line=int(m.group(2)), function=m.group(3) or "")
            )


# ── Internal: error extraction ──────────────────────────────────────


def _extract_errors(signals: TestLogSignals, lines: list[str]) -> None:
    """Extract error types, messages, and assertion details."""
    error_type_counts: dict[str, int] = {}

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Error lines: E       TypeError: ...
        m = _RE_ERROR_LINE.search(stripped)
        if m:
            etype = m.group(1)
            msg = m.group(2) or ""
            error_type_counts[etype] = error_type_counts.get(etype, 0) + 1
            if not signals.first_error_message:
                signals.first_error_message = f"{etype}: {msg}"
            signals.error_messages.append(f"{etype}: {msg}")
            continue

        # Fallback: also match error message in summary lines
        m = _RE_ERROR_GENERIC.search(stripped)
        if m and "FAILURES" not in stripped and "Error" not in stripped:
            etype = m.group(1)
            msg = m.group(2) or ""
            error_type_counts[etype] = error_type_counts.get(etype, 0) + 1
            if not signals.first_error_message:
                signals.first_error_message = f"{etype}: {msg}"

        # Assertion detail lines: >       cirsnod = inod.transform_to(cframe1)
        if _RE_ASSERTION_LINE.search(stripped):
            signals.failure_assertions.append(stripped)

    signals.error_types = error_type_counts


# ── Internal: call chains ────────────────────────────────────────────


def _extract_call_chains(signals: TestLogSignals) -> None:
    """Group stack frames into per-failure call chains.

    Heuristic: frames are separated by blank lines or failure headers
    (________________________________ test_name ________________________________).
    """
    if not signals.stack_frames:
        return

    # A simpler heuristic: if there are N failed tests, we try to distribute
    # frames proportionally. This is imperfect but better than a flat list.
    # For now, store the full frame list — call chain segmentation is a
    # future improvement when we have more test_log samples.
    # signals.call_chains = [signals.stack_frames]  # single chain
    pass  # Reserved for Phase 2 improvement


# ── Helpers ──────────────────────────────────────────────────────────


def _is_system_path(path: str) -> bool:
    """Check if path is a system/site-packages path (not repo code)."""
    return any(
        prefix in path
        for prefix in [
            "/opt/",
            "/usr/",
            "/lib/",
            "site-packages/",
            "dist-packages/",
        ]
    )


def _resolve_repo_path(short_path: str) -> str:
    """Resolve a short pytest-style path to a reasonable repo-relative path.

    Pytest sometimes produces paths like:
      astropy/coordinates/baseframe.py:1202: in transform_to
    These are already repo-relative. Just return as-is.

    But some paths might be even shorter (just a filename). In that case,
    prefix with the module structure if possible.
    """
    if short_path.startswith("."):
        short_path = short_path.lstrip("./")
    return short_path
