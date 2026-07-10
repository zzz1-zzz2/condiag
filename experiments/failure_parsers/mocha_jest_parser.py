"""Parser for Mocha and Jest JavaScript test framework output."""
from __future__ import annotations

import json
import re

from condiag.schemas import FailureWitness
from .base import FailureParser, register_parser


@register_parser("mocha_jest_parser")
class MochaJestParser(FailureParser):
    """Parse Mocha (spec + JSON reporter) and Jest failure output."""

    framework = "mocha_jest"
    priority = 100

    _MOCHA_PASSING = re.compile(r"^\s*(\d+)\s+passing", re.M)
    _MOCHA_FAILING = re.compile(r"^\s*(\d+)\s+failing", re.M)
    _MOCHA_FAIL_NUM = re.compile(r"^\s*(\d+)\)\s*(.+)", re.M)
    _MOCHA_ASSERT = re.compile(r"AssertionError(?: \[[\w:]+\])?:\s*(.*)", re.M)
    _MOCHA_AT_LINE = re.compile(
        r"at\s+(?:\w+\s+)?\(?(?:file://)?([^:)]+):(\d+)(?::(\d+))?\)?", re.M
    )

    _JEST_FAIL_FILE = re.compile(r"^\s*FAIL\s+(\S+\.(?:js|ts|jsx|tsx))", re.M)
    _JEST_TEST_NAME = re.compile(r"^\s*●\s+(.+)", re.M)
    _JEST_EXPECTED = re.compile(r"^\s*Expected:\s*(.*)", re.M)
    _JEST_RECEIVED = re.compile(r"^\s*Received:\s*(.*)", re.M)
    _JEST_AT = re.compile(r"at\s+(.+?)\s+\(([^:)]+):(\d+):(\d+)\)", re.M)

    _JS_ERROR = re.compile(
        r"(ReferenceError|TypeError|SyntaxError|RangeError|ServerlessError):\s*(.*)", re.M
    )

    _JS_ERROR_TYPES = re.compile(
        r"(TypeError|AssertionError|SyntaxError|ReferenceError|RangeError|ServerlessError)"
    )
    _JS_STACK_TRACE = re.compile(r"at\s+\S+\.(?:js|ts|jsx|tsx):\d+")
    _MOCHA_JSON_FAILURES = re.compile(r'"failures"\s*:\s*([1-9]\d*)')

    @classmethod
    def can_parse(cls, log_text):
        if cls._MOCHA_FAILING.search(log_text) and cls._MOCHA_PASSING.search(log_text):
            return True
        if '"failures"' in log_text and '"stats"' in log_text:
            try:
                parsed = json.loads(log_text)
                if isinstance(parsed, dict) and "stats" in parsed:
                    return True
            except (json.JSONDecodeError, ValueError):
                pass
        # Embedded Mocha JSON: requires 3 lines of evidence
        # 1) "failures": <positive int>  2) stats key  3) JS error or stack trace
        if cls._MOCHA_JSON_FAILURES.search(log_text):
            has_stats_key = any(k in log_text for k in ['"tests"', '"passes"', '"duration"'])
            has_js_error = bool(cls._JS_ERROR_TYPES.search(log_text) or cls._JS_STACK_TRACE.search(log_text))
            if has_stats_key and has_js_error:
                return True
        if cls._JEST_FAIL_FILE.search(log_text):
            return True
        return False

    @classmethod
    def parse(cls, instance_id, log_text, raw_log_path=""):
        witness = cls._try_parse_json(instance_id, log_text, raw_log_path)
        if witness:
            return witness
        witness = cls._try_parse_jest(instance_id, log_text, raw_log_path)
        if witness:
            return witness
        return cls._parse_mocha_spec(instance_id, log_text, raw_log_path)

    @classmethod
    def _extract_json_block(cls, text):
        """Find and parse a JSON block containing Mocha 'stats' key from mixed text."""
        idx = text.find('"stats"')
        if idx < 0:
            return None
        start = text.rfind('{', 0, idx)
        if start < 0:
            return None
        depth = 0
        end = -1
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
                continue
            if text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end <= start:
            return None
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            return None

    @classmethod
    def _extract_embedded_messages(cls, text):
        """Extract error messages from embedded JSON err.message fields."""
        messages = []
        for m in re.finditer(r'"message"\s*:\s*"([^"]+)"', text):
            msg = m.group(1)
            if msg not in messages:
                messages.append(msg)
        return messages

    @classmethod
    def _filter_stack_frames(cls, frames):
        """Prioritize repo frames over node_modules/internal frames."""
        repo_frames = [f for f in frames if 'node_modules/' not in f and not f.startswith('node:')]
        external_frames = [f for f in frames if f not in repo_frames]
        return repo_frames[:10] + external_frames[:3]

    @classmethod
    def _try_parse_json(cls, instance_id, log_text, raw_log_path):
        if '"failures"' not in log_text or '"stats"' not in log_text:
            return None

        # Try full parse first, then fall back to embedded block extraction
        data = None
        try:
            data = json.loads(log_text)
        except (json.JSONDecodeError, ValueError):
            pass
        if data is None:
            data = cls._extract_json_block(log_text)

        if not isinstance(data, dict) or "stats" not in data:
            return None

        stats = data.get("stats", {})
        failures = data.get("failures", [])
        n_fail = stats.get("failures", len(failures))

        failed_tests = []
        error_msg = ""
        stack_frames = []

        # Extract from JSON failure objects
        for f in failures:
            title = f.get("fullTitle", f.get("title", ""))
            if title:
                failed_tests.append(title)
            err = f.get("err", {})
            stack = err.get("stack", "")
            if stack and not error_msg:
                # Stack may contain \n escapes from JSON serialization
                first_line = stack.split("\\n")[0].split("\n")[0].strip()[:500]
                if first_line:
                    error_msg = first_line

        # Extract stack frames from err.stack in JSON
        for f in failures:
            err = f.get("err", {})
            stack = err.get("stack", "")
            if stack:
                for sf in stack.split("\\n"):
                    sf = sf.strip()
                    m = re.search(r"at\s+(?:\w+\s+)?\(?(?:file://)?([^:)]+):(\d+)", sf)
                    if m:
                        frame = f'File "{m.group(1)}", line {m.group(2)}'
                        if frame not in stack_frames:
                            stack_frames.append(frame)
                break  # First error's stack is enough

        # Fallback: extract error messages from embedded JSON strings in text
        if not error_msg:
            messages = cls._extract_embedded_messages(log_text)
            if messages:
                error_msg = messages[0][:500]
                # Also extract frames from at-line markers in the text
                for m in cls._MOCHA_AT_LINE.finditer(log_text):
                    filepath = m.group(1)
                    line_no = m.group(2)
                    frame = f'File "{filepath}", line {line_no}'
                    if frame not in stack_frames:
                        stack_frames.append(frame)

        # Fallback: JS error pattern from raw text
        if not error_msg:
            js_err = cls._JS_ERROR.search(log_text)
            if js_err:
                error_msg = f"{js_err.group(1)}: {js_err.group(2)[:200]}"

        if not error_msg:
            error_msg = f"{n_fail} test(s) failed"

        expected, actual = cls._extract_expected_actual_from_text(log_text)
        stack_frames = cls._filter_stack_frames(stack_frames)

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type="test_failure",
            test_framework="mocha_jest",
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
    def _try_parse_jest(cls, instance_id, log_text, raw_log_path):
        fail_files = cls._JEST_FAIL_FILE.findall(log_text)
        if not fail_files:
            return None

        test_names = cls._JEST_TEST_NAME.findall(log_text)
        failed_tests = list(set(t.strip() for t in test_names))

        expected = None
        actual = None
        exp_m = cls._JEST_EXPECTED.findall(log_text)
        rec_m = cls._JEST_RECEIVED.findall(log_text)
        if exp_m:
            expected = exp_m[-1].strip()[:500]
        if rec_m:
            actual = rec_m[-1].strip()[:500]

        error_msg = ""
        at_lines = cls._JEST_AT.findall(log_text)
        if at_lines:
            error_msg = f"AssertionError in {fail_files[0]}"
        else:
            js_err = cls._JS_ERROR.search(log_text)
            if js_err:
                error_msg = f"{js_err.group(1)}: {js_err.group(2)[:200]}"

        if not error_msg:
            error_msg = f"{len(fail_files)} file(s) failed"

        stack_frames = []
        for func_name, filepath, line, col in at_lines:
            stack_frames.append(f'File "{filepath}", line {line}')

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type="assertion_error" if expected else "test_failure",
            test_framework="mocha_jest",
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
    def _parse_mocha_spec(cls, instance_id, log_text, raw_log_path):
        lines = log_text.splitlines()
        n_fail = 0

        fail_m = cls._MOCHA_FAILING.search(log_text)
        n_fail = int(fail_m.group(1)) if fail_m else 0

        failed_tests = []
        current_fail_title = ""
        for line in lines:
            m = cls._MOCHA_FAIL_NUM.match(line)
            if m:
                if current_fail_title and current_fail_title not in failed_tests:
                    failed_tests.append(current_fail_title)
                current_fail_title = m.group(2).strip()
        if current_fail_title and current_fail_title not in failed_tests:
            failed_tests.append(current_fail_title)

        error_msg = ""
        for m in cls._MOCHA_ASSERT.finditer(log_text):
            error_msg = m.group(1).strip()[:500]
            break

        if not error_msg:
            js_err = cls._JS_ERROR.search(log_text)
            if js_err:
                error_msg = f"{js_err.group(1)}: {js_err.group(2)[:200]}"

        expected, actual = cls._extract_expected_actual_from_mocha_diff(log_text)

        stack_frames = []
        for m in cls._MOCHA_AT_LINE.finditer(log_text):
            filepath = m.group(1)
            line_no = m.group(2)
            stack_frames.append(f'File "{filepath}", line {line_no}')

        stack_frames = cls._filter_stack_frames(stack_frames)

        ftype = cls._classify_js_failure(log_text, error_msg)

        if not error_msg and n_fail > 0:
            error_msg = f"{n_fail} test(s) failed"
            if failed_tests:
                error_msg = f"{failed_tests[0]}: {error_msg}"
        if not error_msg:
            error_msg = "test_failure"

        return FailureWitness(
            instance_id=instance_id,
            has_failure_witness=True,
            failure_observed=True,
            failure_stage="validation_failure",
            failure_type=ftype,
            test_framework="mocha_jest",
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
    def _extract_expected_actual_from_mocha_diff(cls, text):
        plus_lines = []
        minus_lines = []
        for line in text.splitlines():
            if line.startswith("+") and not line.startswith("++"):
                plus_lines.append(line[1:].strip())
            elif line.startswith("-") and not line.startswith("--"):
                minus_lines.append(line[1:].strip())

        expected = "\n".join(minus_lines[:5])[:500] if minus_lines else None
        actual = "\n".join(plus_lines[:5])[:500] if plus_lines else None
        return expected, actual

    @classmethod
    def _extract_expected_actual_from_text(cls, text):
        m = re.search(
            r"expected\s*([\s\S]*?)to\s+(?:deeply\s+)?equal\s*([\s\S]*?)(?:\n\s*at\s|\Z)",
            text, re.I
        )
        if m:
            return m.group(1).strip()[:500], m.group(2).strip()[:500]
        exp = re.search(r"(?:Expected|expected):\s*(.*)", text)
        got = re.search(r"(?:Received|received|Got|got):\s*(.*)", text)
        if exp and got:
            return exp.group(1).strip()[:500], got.group(1).strip()[:500]
        return None, None

    @classmethod
    def _classify_js_failure(cls, text, error_msg):
        combined = text + " " + error_msg
        if "AssertionError" in combined or "assert" in combined.lower():
            return "assertion_error"
        if "TypeError" in combined:
            return "type_error"
        if "ReferenceError" in combined:
            return "reference_error"
        if "Timeout" in combined:
            return "timeout"
        if "ELIFECYCLE" in combined:
            return "npm_lifecycle"
        return "test_failure"
