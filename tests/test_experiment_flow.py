"""Tests for P0-5/6 closure: serialization, capture, fairness pre-step gate."""
from __future__ import annotations

import json
from dataclasses import dataclass

from condiag.experiment import ComparisonOutput, asdict_skip
from condiag.workspace import (
    CaptureResult,
    WorkspaceSnapshot,
    UntrackedFile,
    check_workspace_fairness,
)


class TestComparisonSerialization:
    def test_branch_result_with_restore_result_serializable(self):
        """branch_result containing RestoreResult must survive json.dumps()."""
        out = ComparisonOutput(instance_id="test")

        # Simulate what experiment.py does after assigning asdict_skip(sf, ...)
        sf_dict = {
            "termination_reason": "submitted",
            "restore_result": {"ok": True, "workspace_sha": "abc123", "reason": ""},
            "workspace_sha_before_first_step": "abc123",
            "n_calls_total": 37,
        }
        out.sf = sf_dict

        cd_dict = {
            "termination_reason": "submitted",
            "restore_result": {"ok": True, "workspace_sha": "abc123", "reason": ""},
            "workspace_sha_before_first_step": "abc123",
            "n_calls_total": 42,
        }
        out.cd = cd_dict
        out.fairness_ok = True

        # Must not raise TypeError
        dumped = json.dumps(out.to_dict(), indent=2)
        assert '"fairness_ok": true' in dumped
        assert '"restore_result"' in dumped

    def test_asdict_skip_handles_nested_dataclass(self):
        """asdict_skip must recursively convert dataclass fields to dicts."""

        @dataclass
        class Inner:
            ok: bool = True
            sha: str = "abc"

        @dataclass
        class Outer:
            name: str = "test"
            inner: Inner | None = None

        result = asdict_skip(Outer(inner=Inner()), skip_keys=[])
        assert isinstance(result["inner"], dict)
        assert result["inner"]["ok"] is True
        # Verify JSON serializable
        json.dumps(result)


class TestCaptureResult:
    def test_capture_ok(self):
        cr = CaptureResult(ok=True, snapshot=WorkspaceSnapshot(), reason="")
        assert cr.ok
        assert cr.snapshot is not None

    def test_capture_failed(self):
        cr = CaptureResult(ok=False, reason="git rev-parse failed")
        assert not cr.ok
        assert cr.snapshot is None


class TestFairnessGate:
    def test_equal_workspaces_pass(self):
        ws = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        assert check_workspace_fairness(ws, ws, ws)["all_ok"]

    def test_mismatched_tracked_fails(self):
        r1 = WorkspaceSnapshot(tracked_diff="diff a", base_commit_sha="abc")
        sf = WorkspaceSnapshot(tracked_diff="diff b", base_commit_sha="abc")
        fairness = check_workspace_fairness(r1, sf, sf)
        assert not fairness["all_ok"]
        assert not fairness["r1_vs_sf_tracked_ok"]

    def test_mismatched_untracked_fails(self):
        r1 = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile("x.py", 10, "aaa")],
            base_commit_sha="abc",
        )
        sf = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile("x.py", 20, "bbb")],
            base_commit_sha="abc",
        )
        fairness = check_workspace_fairness(r1, sf, sf)
        assert not fairness["all_ok"]
        assert not fairness["r1_vs_sf_state_ok"]
