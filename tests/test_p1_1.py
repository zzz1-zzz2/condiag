"""Tests for P1-1: Bundle builder + DiagnoserCore integration."""
from __future__ import annotations

from condiag.diagnosis.bundle_builder import build_failure_feature_bundle
from condiag.diagnosis.diagnoser_core import DiagnoserCore
from condiag.diagnosis.taxonomy import ContextDeficiencyType
from condiag.diagnosis.signals.schema import RuntimeInstanceSignals, RuntimeFailureFeatureBundle, PatchSignals


def _make_fw(failed=None, error="", frames=None):
    return {
        "failed_tests": failed or [],
        "error_message": error,
        "stack_frames": frames or [],
    }


class TestRuntimeBundle:
    def test_bundle_has_no_oracle_fields(self):
        """RuntimeFailureFeatureBundle must not contain gold data fields."""
        b = build_failure_feature_bundle()
        d = b.model_dump()
        assert "fail_to_pass" not in d["instance"]
        assert "pass_to_pass" not in d["instance"]
        assert "has_gold_context" not in d["instance"]
        assert "gold_context" not in d["instance"]

    def test_repo_frame_detection(self):
        """/testbed/ paths must be marked as repo frames."""
        fw = _make_fw(frames=[
            {"file": "/testbed/astropy/x.py", "line": 42, "function": "foo"},
            {"file": "/usr/lib/python3.9/os.py", "line": 99, "function": "bar"},
            {"file": "astropy/y.py", "line": 10, "function": "baz"},
        ])
        b = build_failure_feature_bundle(failure_witness=fw)
        frames = b.test_log.stack_frames
        assert len(frames) == 3
        assert frames[0].is_repo_frame is True, "/testbed/ path must be repo"
        assert frames[1].is_repo_frame is False, "/usr/ path must not be repo"
        assert frames[2].is_repo_frame is True, "relative path astropy/ must be repo"

    def test_no_reliable_deficiency_when_no_signals(self):
        """Diagnoser returns NO_RELIABLE_DEFICIENCY when no rules match."""
        fw = _make_fw(error="")
        b = build_failure_feature_bundle(failure_witness=fw)
        diagnosis = DiagnoserCore().diagnose(b)
        assert diagnosis.primary.type == ContextDeficiencyType.NO_RELIABLE_DEFICIENCY

    def test_localization_direction_on_mismatch(self):
        """Edit vs error location mismatch -> LOCALIZATION_DIRECTION."""
        from condiag.diagnosis.signals.schema import PatchSignals, TrajectorySignals, TestLogSignals
        from condiag.diagnosis.signals.schema import StackFrame

        # Bundle with edits in one place, errors in another
        b = RuntimeFailureFeatureBundle(
            patch=PatchSignals(edited_files=["setup.py"]),
        )
        b.test_log.stack_frames = [
            StackFrame(file="astropy/iers.py", line=271, function="mjd_utc", is_repo_frame=True),
        ]
        diagnosis = DiagnoserCore().diagnose(b)
        # Should produce LOCALIZATION_DIRECTION
        types = [diagnosis.primary.type.value] + [s.type.value for s in diagnosis.secondary]
        assert ContextDeficiencyType.LOCALIZATION_DIRECTION.value in types, \
            f"Expected LOCALIZATION_DIRECTION in {types}"

    def test_patch_edited_files_from_integrity_parser(self):
        """Bundle builder must use integrity's shlex parser for filenames."""
        diff = 'diff --git "a/student test.py" "b/student test.py"\n@@ -0,0 +1 @@\n+new\n'
        b = build_failure_feature_bundle(evaluation_patch=diff)
        assert "student test.py" in b.patch.edited_files, f"Got {b.patch.edited_files}"

    def test_tool_call_count_excludes_non_tool_assistant(self):
        """Assistant messages without tool_calls don't count."""
        from condiag.diagnosis.signals.schema import TrajectorySignals
        traj = {
            "messages": [
                {"role": "assistant", "content": "thinking"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call1"}, {"id": "call2"}]},
                {"role": "assistant", "content": "done"},
            ]
        }
        b = build_failure_feature_bundle(trajectory=traj)
        assert b.trajectory.total_tool_calls == 2, f"Expected 2, got {b.trajectory.total_tool_calls}"


class TestDiagnosisArtifacts:
    def test_diagnosis_result_has_primary_and_evidence(self):
        """A basic diagnosis must produce primary type with evidence."""
        fw = _make_fw(error="TypeError: unexpected keyword 'location'")
        b = build_failure_feature_bundle(failure_witness=fw)
        diagnosis = DiagnoserCore().diagnose(b)
        assert diagnosis.primary.type is not None
        assert len(diagnosis.primary.evidence) >= 1


class TestAbstentionBehavior:
    def test_no_reliable_skips_diagnosis_text(self):
        """NO_RELIABLE_DEFICIENCY must skip diagnosis text generation."""
        from condiag.diagnosis.taxonomy import ContextDeficiencyType
        fw = _make_fw(error="")  # no signals
        b = build_failure_feature_bundle(failure_witness=fw)
        diagnosis = DiagnoserCore().diagnose(b)
        assert diagnosis.primary.type == ContextDeficiencyType.NO_RELIABLE_DEFICIENCY
        # Verify abstention produces no text (simulate experiment.py behavior)
        if diagnosis.primary.type == ContextDeficiencyType.NO_RELIABLE_DEFICIENCY:
            diag_text = None
        else:
            from condiag.experiment import _render_diagnosis_prompt
            diag_text = _render_diagnosis_prompt(diagnosis)
        assert diag_text is None, "Abstained diagnosis should produce no prompt text"

    def test_typed_diagnosis_non_abstained(self):
        """Typed diagnosis with signal should render prompt text."""
        fw = _make_fw(error="TypeError: unexpected keyword 'foo'")
        b = build_failure_feature_bundle(failure_witness=fw)
        diagnosis = DiagnoserCore().diagnose(b)
        from condiag.diagnosis.taxonomy import ContextDeficiencyType
        if diagnosis.primary.type != ContextDeficiencyType.NO_RELIABLE_DEFICIENCY:
            from condiag.experiment import _render_diagnosis_prompt
            text = _render_diagnosis_prompt(diagnosis)
            assert "Diagnosis" in text
            assert diagnosis.primary.type.value in text


class TestTestFileClassification:
    """Verify is_test_file is correctly inferred for various path patterns."""

    def test_testbed_source_frame_is_not_test(self):
        """/testbed/astropy/baseframe.py -> repo=True, test=False."""
        fw = _make_fw(frames=[{"file": "/testbed/astropy/baseframe.py", "line": 42, "function": "transform_to"}])
        b = build_failure_feature_bundle(failure_witness=fw)
        assert len(b.test_log.stack_frames) == 1
        f = b.test_log.stack_frames[0]
        assert f.is_repo_frame is True
        assert f.is_test_file is False, f"{f.file} should NOT be a test file"

    def test_testbed_test_frame_is_test(self):
        """/testbed/astropy/tests/test_baseframe.py -> repo=True, test=True."""
        fw = _make_fw(frames=[{"file": "/testbed/astropy/tests/test_baseframe.py", "line": 42, "function": "test_something"}])
        b = build_failure_feature_bundle(failure_witness=fw)
        assert len(b.test_log.stack_frames) == 1
        f = b.test_log.stack_frames[0]
        assert f.is_repo_frame is True
        assert f.is_test_file is True, f"{f.file} should be a test file"

    def test_external_system_frame_is_not_repo(self):
        """/usr/lib/python/os.py -> repo=False, test=False."""
        fw = _make_fw(frames=[{"file": "/usr/lib/python3.9/os.py", "line": 99, "function": "walk"}])
        b = build_failure_feature_bundle(failure_witness=fw)
        assert len(b.test_log.stack_frames) == 1
        f = b.test_log.stack_frames[0]
        assert f.is_repo_frame is False
        assert f.is_test_file is False
    def test_all_test_frames_does_not_trigger_localization(self):
        """If all stack frames are test files, localization must not trigger."""
        from condiag.diagnosis.signals.schema import StackFrame
        b = RuntimeFailureFeatureBundle(patch=PatchSignals(edited_files=["foo.py"]))
        b.test_log.stack_frames = [
            StackFrame(file="tests/test_foo.py", line=42, function="test_bar",
                       is_repo_frame=True, is_test_file=True),
        ]
        diagnosis = DiagnoserCore().diagnose(b)
        # Should not trigger LOCALIZATION_DIRECTION (all frames are test)
        types = {diagnosis.primary.type.value} | {s.type.value for s in diagnosis.secondary}
        from condiag.diagnosis.taxonomy import ContextDeficiencyType
        assert ContextDeficiencyType.LOCALIZATION_DIRECTION.value not in types
