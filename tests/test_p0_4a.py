"""Tests for P0-4a Patch Integrity Gate."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from condiag.integrity import (
    PatchIntegrityReport,
    check_patch_integrity,
    extract_changed_files,
    is_safe_path,
    is_valid_unified_diff,
)
from condiag.patch_artifacts import AgentSubmission, sha256_short


# A valid unified diff fragment for testing
VALID_DIFF = """diff --git a/foo.py b/foo.py
index 1234567..89abcde 100644
--- a/foo.py
+++ b/foo.py
@@ -1,1 +1,1 @@
-old
+new
diff --git a/bar.py b/bar.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/bar.py
@@ -0,0 +1,2 @@
+line1
+line2
"""


class TestPatchExtraction:
    def test_extract_files_from_diff(self):
        files = extract_changed_files(VALID_DIFF)
        assert "foo.py" in files
        assert "bar.py" in files

    def test_extract_files_empty(self):
        assert extract_changed_files("") == []


class TestPathSafety:
    def test_safe_path(self):
        assert is_safe_path("foo/bar.py") is True

    def test_unsafe_parent_traversal(self):
        assert is_safe_path("../etc/passwd") is False
        assert is_safe_path("foo/../bar") is False

    def test_unsafe_absolute(self):
        assert is_safe_path("/etc/passwd") is False

    def test_unsafe_dot_git(self):
        assert is_safe_path(".git/config") is False


class TestUnifiedDiffFormat:
    def test_valid(self):
        assert is_valid_unified_diff(VALID_DIFF) is True

    def test_invalid_no_header(self):
        assert is_valid_unified_diff("just some text") is False

    def test_empty(self):
        assert is_valid_unified_diff("") is False


class TestCheckPatchIntegrity:
    def test_valid_explicit_submission_consistent(self):
        sub = AgentSubmission(selected_patch=VALID_DIFF, selected_source="exit_extra_submission")
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch=VALID_DIFF,
            evaluation_patch=VALID_DIFF,
        )
        assert r.ok is True
        assert r.status == "valid"
        assert r.consistency == "consistent"
        assert "foo.py" in r.changed_files
        assert "bar.py" in r.changed_files

    def test_canonical_consistency_tolerates_trailing_whitespace(self):
        canonical = VALID_DIFF
        uncanonical = VALID_DIFF + "\n\n   \n"
        sub = AgentSubmission(selected_patch=uncanonical, selected_source="exit_extra_submission")
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch=canonical,
            evaluation_patch=canonical,
        )
        assert r.ok
        assert r.consistency == "consistent"

    def test_mismatch_does_not_block_valid_submission(self):
        """Workspace mismatch with explicit submission should not block.
        evaluation_patch must equal submitted patch, but workspace may have extras."""
        submitted = VALID_DIFF
        workspace = VALID_DIFF + "diff --git a/extra b/extra\n+extra\n"
        sub = AgentSubmission(selected_patch=submitted, selected_source="exit_extra_submission")
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch=workspace,
            evaluation_patch=submitted,
        )
        # Should pass (not block) but record the workspace mismatch
        assert r.ok is True
        assert r.consistency == "mismatch"

    def test_provenance_mismatch_blocks(self):
        """evaluation_patch must equal submitted_patch when explicit submission is used."""
        submitted = VALID_DIFF
        different = VALID_DIFF.replace("+new", "+DIFFERENT")
        sub = AgentSubmission(selected_patch=submitted, selected_source="exit_extra_submission")
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch=submitted,
            evaluation_patch=different,
        )
        assert r.ok is False
        assert r.status == "invalid_provenance"

    def test_fallback_allowed(self):
        sub = AgentSubmission(
            selected_patch=VALID_DIFF,
            selected_source="workspace_diff_fallback",
        )
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch="",
            evaluation_patch=VALID_DIFF,
        )
        assert r.ok
        assert r.fallback_used is True
        assert r.consistency == "fallback"

    def test_both_empty_blocks(self):
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=AgentSubmission(),
            workspace_patch="",
            evaluation_patch="",
        )
        assert r.ok is False
        assert r.status == "invalid_empty"

    def test_invalid_diff_format_blocks(self):
        bad = "this is not a diff"
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=AgentSubmission(),
            workspace_patch="",
            evaluation_patch=bad,
        )
        assert r.ok is False
        assert r.status == "invalid_diff_format"

    def test_git_apply_check(self, tmp_path):
        # Set up a real Git repo
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        (tmp_path / "foo.py").write_text("old\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

        # Apply a valid diff to a fresh dir
        workdir2 = tmp_path / "fresh"
        workdir2.mkdir()
        subprocess.run(["git", "clone", "-q", str(tmp_path), str(workdir2)], check=True)
        subprocess.run(["git", "reset", "-q", "--hard", "HEAD"], cwd=workdir2, check=True)

        sub = AgentSubmission(selected_patch=VALID_DIFF, selected_source="exit_extra_submission")
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch=VALID_DIFF,
            evaluation_patch=VALID_DIFF,
            agent_workdir=workdir2,
            run_apply_check=True,
        )
        assert r.ok
        assert r.apply_check_status == "passed"

    def test_quoted_filename_with_special_chars(self):
        """Files with spaces/quotes in names must be captured (Git quotes them)."""
        diff = (
            'diff --git "a/students test.py" "b/students test.py"\n'
            'index 1234567..89abcde 100644\n'
            '--- "a/students test.py"\n'
            '+++ "b/students test.py"\n'
            '@@ -1,1 +1,1 @@\n'
            '-old\n'
            '+new\n'
        )
        files = extract_changed_files(diff)
        assert "students test.py" in files

    def test_apply_check_status_not_run_by_default(self):
        """Without run_apply_check, status is 'not_run' even if workdir provided."""
        sub = AgentSubmission(selected_patch=VALID_DIFF, selected_source="exit_extra_submission")
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch=VALID_DIFF,
            evaluation_patch=VALID_DIFF,
            agent_workdir=None,  # no workdir
        )
        assert r.apply_check_status == "not_run"

    def test_git_apply_check_failure(self, tmp_path):
        # Set up a Git repo without the file we're trying to modify
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
        (tmp_path / "other.py").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

        # Diff tries to modify foo.py which doesn't exist
        sub = AgentSubmission(selected_patch=VALID_DIFF, selected_source="exit_extra_submission")
        r = check_patch_integrity(
            termination_reason="submitted",
            agent_submission=sub,
            workspace_patch=VALID_DIFF,
            evaluation_patch=VALID_DIFF,
            agent_workdir=tmp_path,
            run_apply_check=True,
        )
        assert r.ok is False
        assert r.status == "invalid_unapplyable"
        assert r.apply_check_status == "failed"

    def test_termination_must_be_submitted(self):
        r = check_patch_integrity(
            termination_reason="wall_timeout",
            evaluation_patch=VALID_DIFF,
        )
        assert r.ok is False
        assert r.status == "invalid_termination"

    def test_unsafe_path_blocks(self):
        bad_diff = """diff --git a/../etc/passwd b/../etc/passwd
index 1234567..89abcde 100644
--- a/../etc/passwd
+++ b/../etc/passwd
@@ -1,1 +1,1 @@
-old
+new
"""
        r = check_patch_integrity(
            termination_reason="submitted",
            evaluation_patch=bad_diff,
        )
        assert r.ok is False
        assert r.status == "invalid_unsafe_path"
