"""Tests for patch_artifacts and workspace modules."""
from condiag.patch_artifacts import (
    AgentSubmission,
    PatchArtifacts,
    collect_agent_submission,
    canonicalize_patch,
)
from condiag.workspace import (
    UntrackedFile,
    WorkspaceSnapshot,
    check_workspace_fairness,
)


class TestPatchArtifacts:
    def test_collect_agent_submission_exception(self):
        sub = collect_agent_submission(exception_payload="--- a/foo.py\n+++ b/foo.py\n")
        assert sub.selected_source == "exception_payload"
        assert sub.sha != ""

    def test_collect_agent_submission_empty(self):
        sub = collect_agent_submission()
        assert sub.selected_source == "none"
        assert sub.selected_patch == ""

    def test_collect_agent_submission_fallback(self):
        sub = collect_agent_submission(patch_file_text="--- a/bar.py\n+++ b/bar.py\n")
        assert sub.selected_source == "patch_file_content"

    def test_collect_agent_submission_mismatch(self):
        sub = collect_agent_submission(
            exception_payload="diff --git a/a.py b/a.py",
            patch_file_text="diff --git a/b.py b/b.py",
        )
        assert sub.consistency_status == "mismatch"

    def test_collect_agent_submission_from_messages(self):
        """Extract submission from agent's exit message via extra.submission."""
        from condiag.patch_artifacts import extract_submission_from_messages
        messages = [
            {"role": "assistant", "content": "done"},
            {"role": "exit", "content": "",
             "extra": {"exit_status": "Submitted", "submission": "--- a/x.py\n+++ b/x.py\n"}},
        ]
        patch, source = extract_submission_from_messages(messages)
        assert source == "exit_extra_submission"
        assert "x.py" in patch
        # Should be selected as primary source
        sub = collect_agent_submission(agent_messages=messages)
        assert sub.selected_source == "exit_extra_submission"

    def test_canonicalize_patch_removes_trailing_spaces(self):
        raw = "diff --git a/x.py b/x.py\n+print('hello')\n\n  \n"
        result = canonicalize_patch(raw)
        assert result == raw.strip() + "\n"

    def test_canonicalize_patch_empty(self):
        assert canonicalize_patch("") == ""
        assert canonicalize_patch("   ") == ""

    def test_consistency_check_consistent(self):
        from condiag.patch_artifacts import patch_consistency_check
        a = "diff --git a/x.py\n+x\n"
        b = "diff --git a/x.py\n+x\n\n\n"
        assert patch_consistency_check(a, b) == "consistent"

    def test_consistency_check_mismatch(self):
        from condiag.patch_artifacts import patch_consistency_check
        a = "diff --git a/x.py\n+x\n"
        b = "diff --git a/x.py\n+y\n"
        assert patch_consistency_check(a, b) == "mismatch"

    def test_consistency_check_empty(self):
        from condiag.patch_artifacts import patch_consistency_check
        assert patch_consistency_check("", "") == "empty"
        # One side non-empty = mismatch (not empty)
        assert patch_consistency_check("x", "") == "mismatch"

    def test_patch_artifacts_defaults(self):
        pa = PatchArtifacts()
        assert pa.evaluation_sha == ""
        assert pa.workspace_sha == ""


class TestWorkspace:
    def test_untracked_file(self):
        uf = UntrackedFile(path="test.py", size=100, sha256="abc")
        d = uf.to_dict()
        assert d["path"] == "test.py"

    def test_workspace_snapshot_sha(self):
        ws = WorkspaceSnapshot(
            tracked_diff="diff --git a/x.py b/x.py",
            untracked_manifest=[UntrackedFile("y.py", 50, "abc")],
            base_commit_sha="abc123",
        )
        assert ws.tracked_diff_sha != ""
        assert ws.workspace_state_sha != ""
        assert ws.untracked_manifest_sha != ""

    def test_workspace_snapshot_sha_changes_with_diff(self):
        ws1 = WorkspaceSnapshot(tracked_diff="change a", base_commit_sha="abc")
        ws2 = WorkspaceSnapshot(tracked_diff="change b", base_commit_sha="abc")
        assert ws1.workspace_state_sha != ws2.workspace_state_sha

    def test_workspace_snapshot_sha_changes_with_untracked(self):
        ws1 = WorkspaceSnapshot(
            tracked_diff="diff",
            untracked_manifest=[UntrackedFile("x.py", 10, "aaa")],
            base_commit_sha="abc",
        )
        ws2 = WorkspaceSnapshot(
            tracked_diff="diff",
            untracked_manifest=[UntrackedFile("y.py", 20, "bbb")],
            base_commit_sha="abc",
        )
        assert ws1.workspace_state_sha != ws2.workspace_state_sha

    def test_fairness_both_equal(self):
        ws = WorkspaceSnapshot(tracked_diff="same diff", base_commit_sha="abc")
        fairness = check_workspace_fairness(ws, ws, ws)
        assert fairness["all_ok"]

    def test_fairness_mismatch(self):
        ws1 = WorkspaceSnapshot(tracked_diff="diff a", base_commit_sha="abc")
        ws2 = WorkspaceSnapshot(tracked_diff="diff b", base_commit_sha="abc")
        fairness = check_workspace_fairness(ws1, ws2, ws1)
        assert not fairness["all_ok"]
