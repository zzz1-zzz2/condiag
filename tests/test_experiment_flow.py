"""Integration tests for experiment flow: serialization, fairness, snapshot."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

from condiag.experiment import ComparisonOutput, _sha
from condiag.workspace import WorkspaceSnapshot, UntrackedFile, check_workspace_fairness


class TestComparisonOutput:
    def test_to_dict_serializable(self):
        """comparison.json must be JSON-serializable without custom encoders."""
        out = ComparisonOutput(
            instance_id="test",
            verdict="tie",
        )
        d = out.to_dict()
        dumped = json.dumps(d, indent=2)
        assert '"instance_id": "test"' in dumped

    def test_to_dict_with_round1_result(self):
        """asdict_skip must exclude non-serializable fields like workspace_snapshot."""
        out = ComparisonOutput(instance_id="test")
        # Simulate what experiment.py does with round1 result
        out.round1 = {
            "termination_reason": "submitted",
            "n_calls": 35,
            "patch_text": "diff --git a/x.py b/x.py",
        }
        d = out.to_dict()
        json.dumps(d, indent=2)  # must not raise


class TestFairness:
    def test_identical_workspaces_pass(self):
        ws = WorkspaceSnapshot(tracked_diff="same diff", base_commit_sha="abc")
        r = check_workspace_fairness(ws, ws, ws)
        assert r["all_ok"]

    def test_diff_tracked_fails(self):
        ws1 = WorkspaceSnapshot(tracked_diff="change a", base_commit_sha="abc")
        ws2 = WorkspaceSnapshot(tracked_diff="change b", base_commit_sha="abc")
        r = check_workspace_fairness(ws1, ws2, ws1)
        assert not r["all_ok"]

    def test_diff_untracked_fails(self):
        ws1 = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile(path="x.py", size=10, sha256="aaa")],
            base_commit_sha="abc",
        )
        ws2 = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile(path="y.py", size=20, sha256="bbb")],
            base_commit_sha="abc",
        )
        r = check_workspace_fairness(ws1, ws2, ws1)
        assert not r["all_ok"]


class TestWorkspaceSnapshot:
    def test_only_untracked_no_tracked(self):
        """Snapshot with only untracked files should still produce a meaningful SHA."""
        ws = WorkspaceSnapshot(
            tracked_diff="",
            untracked_manifest=[UntrackedFile(path="reproduce.py", size=50, sha256="abc123")],
            base_commit_sha="abc",
        )
        assert ws.workspace_state_sha != ""

    def test_untracked_content_sha_changes_state(self):
        """Same path but different content SHA must produce different state SHA."""
        ws1 = WorkspaceSnapshot(
            tracked_diff="diff",
            untracked_manifest=[UntrackedFile(path="x.py", size=10, sha256="aaa")],
            base_commit_sha="abc",
        )
        ws2 = WorkspaceSnapshot(
            tracked_diff="diff",
            untracked_manifest=[UntrackedFile(path="x.py", size=10, sha256="bbb")],
            base_commit_sha="abc",
        )
        assert ws1.workspace_state_sha != ws2.workspace_state_sha
