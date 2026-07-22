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
    check_tracked_code_fairness,
    check_full_workspace_equivalence,
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


class TestTrackedCodeFairness:
    def test_tracked_matches(self):
        r1 = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        sf = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        g = check_tracked_code_fairness(r1, sf)
        assert g["r1_vs_sf_tracked_ok"] is True
        assert g["all_ok"] is True

    def test_tracked_mismatch(self):
        r1 = WorkspaceSnapshot(tracked_diff="diff a", base_commit_sha="abc")
        sf = WorkspaceSnapshot(tracked_diff="diff b", base_commit_sha="abc")
        g = check_tracked_code_fairness(r1, sf)
        assert g["r1_vs_sf_tracked_ok"] is False
        assert g["all_ok"] is False

    def test_tracked_with_cd(self):
        r1 = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        sf = r1
        cd = r1
        g = check_tracked_code_fairness(r1, sf, cd)
        assert g["all_ok"] is True
        assert g["r1_vs_cd_tracked_ok"] is True

    def test_tracked_cd_mismatch(self):
        r1 = WorkspaceSnapshot(tracked_diff="diff a", base_commit_sha="abc")
        sf = WorkspaceSnapshot(tracked_diff="diff a", base_commit_sha="abc")
        cd = WorkspaceSnapshot(tracked_diff="diff b", base_commit_sha="abc")
        g = check_tracked_code_fairness(r1, sf, cd)
        assert g["r1_vs_sf_tracked_ok"] is True
        assert g["r1_vs_cd_tracked_ok"] is False
        assert g["all_ok"] is False

    def test_tracked_none_snapshot_fails(self):
        g = check_tracked_code_fairness(None, WorkspaceSnapshot())
        assert g["all_ok"] is False
        assert g["r1_vs_sf_tracked_ok"] is False

    def test_tracked_ignores_untracked_differences(self):
        r1 = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile("x.py", 10, "aaa")],
            base_commit_sha="abc",
        )
        sf = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile("y.py", 20, "bbb")],
            base_commit_sha="abc",
        )
        # Tracked gate MUST pass even though untracked differs
        g = check_tracked_code_fairness(r1, sf)
        assert g["all_ok"] is True, "untracked differences must not affect tracked gate"


class TestFullWorkspaceEquivalence:
    def test_full_match(self):
        r1 = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        sf = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        g = check_full_workspace_equivalence(r1, sf)
        assert g["all_ok"] is True

    def test_full_untracked_mismatch(self):
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
        # Full equivalence MUST catch untracked differences
        g = check_full_workspace_equivalence(r1, sf)
        assert g["all_ok"] is False
        assert g["r1_vs_sf_state_ok"] is False
        assert g["r1_vs_sf_tracked_ok"] is True

    def test_full_none_fails(self):
        g = check_full_workspace_equivalence(None, WorkspaceSnapshot())
        assert g["all_ok"] is False

    def test_full_cd(self):
        r1 = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        sf = r1
        cd = r1
        g = check_full_workspace_equivalence(r1, sf, cd)
        assert g["all_ok"] is True


class TestLegacyFairnessAlias:
    def test_legacy_matches(self):
        ws = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        f = check_workspace_fairness(ws, ws, ws)
        assert f["all_ok"]

    def test_legacy_untracked_mismatch_still_fails(self):
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
        # Legacy alias unions both gates — mismatch in EITHER fails
        f = check_workspace_fairness(r1, sf, sf)
        assert not f["all_ok"]
        assert f["r1_vs_sf_tracked_ok"] is True
        assert f["r1_vs_sf_state_ok"] is False


class TestRestoreResultAudit:
    def test_restore_result_to_dict(self):
        from condiag.branch_runner import RestoreResult
        r = RestoreResult(
            ok=True,
            workspace_sha="abc123",
            reason="test",
            untracked_manifest_count=3,
            untracked_manifest_sha="def456",
            untracked_archive_expected=True,
            untracked_archive_present=True,
            untracked_archive_extracted=True,
            untracked_restore_status="ok",
            base_commit="abc",
        )
        d = r.to_dict()
        assert d["ok"] is True
        assert d["untracked_restore_status"] == "ok"
        assert d["untracked_manifest_count"] == 3

    def test_restore_result_default_to_dict(self):
        from condiag.branch_runner import RestoreResult
        r = RestoreResult()
        d = r.to_dict()
        assert d["ok"] is False
        assert d["untracked_restore_status"] == "skipped"


class TestDumpFairnessDebug:
    def test_dump_writes_all_files(self, tmp_path):
        from condiag.branch_runner import dump_fairness_debug
        from condiag.workspace import WorkspaceSnapshot

        expected = WorkspaceSnapshot(
            tracked_diff="diff --git a/foo.py b/foo.py\n+new\n",
            untracked_manifest=[
                __import__('condiag.workspace', fromlist=['UntrackedFile']).UntrackedFile(
                    "config.json", 42, "aaa"
                )
            ],
            base_commit_sha="deadbeef",
        )
        actual = WorkspaceSnapshot(
            tracked_diff="diff --git a/bar.py b/bar.py\n+other\n",
            base_commit_sha="cafebabe",
        )

        debug_dir = tmp_path / "fairness_debug"
        dump_fairness_debug(str(debug_dir), expected, actual, "deadbeef", label="sf")

        assert (debug_dir / "expected_r1_tracked.diff").exists()
        assert (debug_dir / "restored_preflight_tracked.diff").exists()
        assert (debug_dir / "expected_sha.txt").exists()
        assert (debug_dir / "actual_sha.txt").exists()
        assert (debug_dir / "expected_head.txt").exists()
        assert (debug_dir / "actual_head.txt").exists()
        assert (debug_dir / "base_commit.txt").exists()
        assert (debug_dir / "expected_files.json").exists()
        assert (debug_dir / "actual_files.json").exists()
        assert (debug_dir / "untracked_expected.json").exists()
        assert (debug_dir / "untracked_actual.json").exists()
        assert (debug_dir / "manifest.txt").exists()

        # Verify content
        assert (debug_dir / "expected_sha.txt").read_text().strip() == expected.tracked_diff_sha
        assert (debug_dir / "actual_sha.txt").read_text().strip() == actual.tracked_diff_sha
        assert (debug_dir / "base_commit.txt").read_text().strip() == "deadbeef"
        import json
        ef = json.loads((debug_dir / "expected_files.json").read_text())
        assert "foo.py" in ef["files"]

    def test_dump_no_debug_dir_does_nothing(self, tmp_path):
        from condiag.branch_runner import dump_fairness_debug
        from condiag.workspace import WorkspaceSnapshot
        # Should not raise
        dump_fairness_debug("", WorkspaceSnapshot(), WorkspaceSnapshot(), "abc")

    def test_dump_missing_snapshots(self, tmp_path):
        from condiag.branch_runner import dump_fairness_debug
        # Both snapshots None — should still create files
        debug_dir = tmp_path / "empty"
        dump_fairness_debug(str(debug_dir), None, None, "abc")
        assert (debug_dir / "manifest.txt").exists()
