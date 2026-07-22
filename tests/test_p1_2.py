"""Tests for P1-2: new extractors and bundle fusion."""
from __future__ import annotations

from condiag.diagnosis.bundle_builder import build_failure_feature_bundle, _merge_test_log_signals
from condiag.diagnosis.signals.frame_normalizer import (
    is_repo_path, is_test_path, normalize_frame,
)
from condiag.diagnosis.signals.patch_extractor import extract_patch_signals
from condiag.diagnosis.signals.schema import StackFrame, TestLogSignals
from condiag.diagnosis.signals.trajectory_extractor import extract_trajectory_signals


class TestFrameNormalizer:
    def test_testbed_source_is_not_test(self):
        assert is_test_path("astropy/baseframe.py") is False
        assert is_test_path("django/db/models/fields.py") is False

    def test_test_directory_is_test(self):
        assert is_test_path("astropy/tests/test_baseframe.py") is True
        assert is_test_path("django/tests/test_foo.py") is True
        assert is_test_path("testing/test_foo.py") is True

    def test_test_file_name_is_test(self):
        assert is_test_path("test_foo.py") is True
        assert is_test_path("foo_test.py") is True
        assert is_test_path("tests/__init__.py") is True

    def test_repo_path_detection(self):
        assert is_repo_path("/testbed/astropy/baseframe.py") is True
        assert is_repo_path("astropy/baseframe.py") is True
        assert is_repo_path("/usr/lib/python3.9/os.py") is False
        assert is_repo_path("/opt/foo.py") is False
        assert is_repo_path("site-packages/foo.py") is False
        assert is_repo_path("") is False

    def test_normalize_frame_strips_testbed_prefix(self):
        frame = normalize_frame("/testbed/astropy/baseframe.py", 42, "transform_to")
        assert frame.file == "astropy/baseframe.py"
        assert frame.is_repo_frame is True
        assert frame.is_test_file is False

    def test_normalize_frame_keeps_relative_path(self):
        frame = normalize_frame("astropy/baseframe.py", 42, "transform_to")
        assert frame.file == "astropy/baseframe.py"
        assert frame.is_repo_frame is True


class TestPatchExtractor:
    def test_extracts_changed_files(self):
        diff = "diff --git a/foo.py b/foo.py\n@@ -0,0 +1 @@\n+new\n"
        s = extract_patch_signals(diff)
        assert "foo.py" in s.edited_files

    def test_extracts_quoted_filename(self):
        diff = 'diff --git "a/student test.py" "b/student test.py"\n@@ -0,0 +1 @@\n+new\n'
        s = extract_patch_signals(diff)
        assert "student test.py" in s.edited_files

    def test_counts_added_deleted_lines(self):
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "@@ -1,1 +1,1 @@\n"
            "-old\n"
            "+new\n"
        )
        s = extract_patch_signals(diff)
        assert s.added_lines == 1
        assert s.deleted_lines == 1
        assert s.changed_lines == 2
        assert s.hunk_count == 1

    def test_detects_config_change(self):
        diff = "diff --git a/pyproject.toml b/pyproject.toml\n@@ -0,0 +1 @@\n+x\n"
        s = extract_patch_signals(diff)
        assert s.introduced_config_change is True

    def test_detects_cicd_config_dir(self):
        diff = "diff --git a/.github/workflows/test.yml b/.github/workflows/test.yml\n@@ -0,0 +1 @@\n+x\n"
        s = extract_patch_signals(diff)
        assert s.introduced_config_change is True

    def test_classifies_test_file(self):
        diff = "diff --git a/tests/test_foo.py b/tests/test_foo.py\n@@ -0,0 +1 @@\n+x\n"
        s = extract_patch_signals(diff)
        assert "tests/test_foo.py" in s.edited_files


class TestTrajectoryExtractor:
    def test_counts_tool_calls(self):
        traj = {
            "messages": [
                {"role": "assistant", "tool_calls": [{"id": "1"}, {"id": "2"}]},
                {"role": "assistant", "tool_calls": [{"id": "3"}]},
            ]
        }
        s = extract_trajectory_signals(traj)
        assert s.total_tool_calls == 3
        assert s.assistant_turn_count == 2

    def test_counts_actions(self):
        traj = {
            "messages": [
                {"role": "assistant", "extra": {"actions": [{"id": "act1", "type": "bash"}, {"id": "act2", "type": "bash"}]}},
            ]
        }
        s = extract_trajectory_signals(traj)
        assert s.total_tool_calls == 2

    def test_extracts_viewed_files(self):
        traj = {
            "messages": [
                {"role": "tool", "content": "File: /testbed/astropy/foo.py\nFile: /testbed/astropy/bar.py\n"},
            ]
        }
        s = extract_trajectory_signals(traj)
        assert "astropy/foo.py" in s.viewed_files
        assert "astropy/bar.py" in s.viewed_files

    def test_counts_format_errors(self):
        traj = {
            "messages": [
                {"role": "user", "content": "No tool calls found in the response."},
                {"role": "assistant", "content": "ok"},
                {"role": "user", "content": "Error parsing tool call"},
            ]
        }
        s = extract_trajectory_signals(traj)
        assert s.format_error_count == 2

    def test_empty_trajectory(self):
        s = extract_trajectory_signals({})
        assert s.total_tool_calls == 0
        assert s.viewed_files == []


class TestTestLogFusion:
    def test_parsed_only(self):
        parsed = TestLogSignals(
            failed_tests=["test_x"],
            error_messages=["TypeError: x"],
        )
        target = TestLogSignals()
        _merge_test_log_signals(target, parsed, None)
        assert target.failed_tests == ["test_x"]
        assert target.error_messages == ["TypeError: x"]

    def test_fw_fills_gap_when_no_parsed(self):
        fw = {
            "failed_tests": ["test_y"],
            "error_message": "AttributeError: y",
            "stack_frames": [{"file": "/testbed/astropy/x.py", "line": 1, "function": "f"}],
        }
        target = TestLogSignals()
        _merge_test_log_signals(target, None, fw)
        assert target.failed_tests == ["test_y"]
        assert any("AttributeError" in m for m in target.error_messages)
        assert len(target.stack_frames) == 1

    def test_parsed_with_fw_dedupes_frames(self):
        parsed = TestLogSignals(
            stack_frames=[
                StackFrame(file="/testbed/a.py", line=1, function="f", is_repo_frame=True)
            ]
        )
        fw = {
            "stack_frames": [
                {"file": "/testbed/a.py", "line": 1, "function": "f"},  # duplicate
                {"file": "/testbed/b.py", "line": 2, "function": "g"},  # new
            ]
        }
        target = TestLogSignals()
        _merge_test_log_signals(target, parsed, fw)
        assert len(target.stack_frames) == 2

    def test_count_error_types(self):
        fw = {"error_message": "TypeError: blah"}
        target = TestLogSignals()
        _merge_test_log_signals(target, None, fw)
        assert target.error_types.get("TypeError") == 1


class TestToolEventDedup:
    """Unified ToolEvent dedup across tool_calls and extra.actions."""

    def test_actions_only_type_counts(self):
        traj = {
            "messages": [
                {"role": "assistant", "extra": {"actions": [{"id": "a1", "type": "bash"}, {"id": "a2", "type": "pytest"}]}},
            ]
        }
        s = extract_trajectory_signals(traj)
        assert s.total_tool_calls == 2
        assert s.tool_type_counts.get("bash") == 1
        assert s.tool_type_counts.get("pytest") == 1

    def test_mixed_id_no_id_tool_calls(self):
        traj = {
            "messages": [
                {"role": "assistant", "tool_calls": [
                    {"id": "c1", "function": {"name": "bash", "arguments": "ls"}},
                    {"function": {"name": "bash", "arguments": "pwd"}},
                ]},
            ]
        }
        s = extract_trajectory_signals(traj)
        assert s.total_tool_calls == 2
        assert s.tool_type_counts.get("bash") == 2

    def test_tc_and_action_same_event_dedup(self):
        traj = {
            "messages": [
                {"role": "assistant",
                 "tool_calls": [{"id": "dup_1", "function": {"name": "bash", "arguments": "ls"}}],
                 "extra": {"actions": [{"tool_call_id": "dup_1", "type": "bash", "command": "ls"}]}},
            ]
        }
        s = extract_trajectory_signals(traj)
        assert s.total_tool_calls == 1
        assert s.tool_type_counts.get("bash") == 1


class TestBranchBuilderRepair:
    """Branch builder repairs all incomplete turns, not just the last."""

    def test_two_incomplete_assistants(self):
        from condiag.branch_builder import build_branch_messages
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "a1"}]},
            {"role": "assistant", "tool_calls": [{"id": "b1"}]},
            {"role": "tool", "tool_call_id": "b1", "content": "ok"},
        ]
        result = build_branch_messages(msgs, None)
        tool_ids = [m.get("tool_call_id") for m in result if m.get("role") == "tool"]
        assert "a1" in tool_ids
        assert "b1" in tool_ids

    def test_synthetic_response_position(self):
        from condiag.branch_builder import build_branch_messages
        msgs = [
            {"role": "assistant", "tool_calls": [{"id": "x1"}]},
            {"role": "assistant", "tool_calls": [{"id": "y1"}]},
            {"role": "tool", "tool_call_id": "y1", "content": "ok"},
        ]
        result = build_branch_messages(msgs, None)
        x1_idx = next(i for i, m in enumerate(result) if m.get("tool_call_id") == "x1")
        y1_idx = next(i for i, m in enumerate(result) if m.get("tool_call_id") == "y1")
        assert x1_idx < y1_idx


class TestPytestNoDuplicate:
    def test_first_error_no_duplicate(self):
        from condiag.diagnosis.signals.pytest_extractor import extract_test_log
        import tempfile, os
        log = "_________________ test_foo _________________\nE   TypeError: x\n"
        tf = tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False)
        tf.write(log)
        tf.close()
        try:
            s = extract_test_log(tf.name)
        finally:
            os.unlink(tf.name)
        matches = [m for m in s.error_messages if "TypeError: x" in m]
        assert len(matches) == 1
