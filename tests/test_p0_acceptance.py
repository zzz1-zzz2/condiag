"""P0 acceptance integration tests: archive failure pre-block, preflight fairness gate."""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def local_git_repo(tmp_path):
    """Create a temp git repo with a tracked file and a base commit."""
    workdir = tmp_path / "repo"
    workdir.mkdir()

    def _exec(cmd: str) -> dict:
        try:
            result = subprocess.run(
                ["bash", "-c", cmd],
                capture_output=True, text=True, timeout=10,
                cwd=workdir,
            )
            return {"returncode": result.returncode, "output": result.stdout}
        except Exception as e:
            return {"returncode": -1, "output": str(e)}

    def exec_cmd(command: dict) -> dict:
        cmd = command.get("command", "")
        if "/testbed" in cmd:
            cmd = cmd.replace("/testbed", str(workdir))
        return _exec(cmd)

    _exec("git init -q")
    _exec("git config user.email 'test@test.com'")
    _exec("git config user.name 'Test'")
    (workdir / "original.py").write_text("def hello():\n    pass\n")
    _exec("git add .")
    _exec("git commit -q -m 'initial'")
    base = _exec("git rev-parse HEAD")["output"].strip()

    agent = MagicMock()
    agent.env = MagicMock()
    agent.env.container_id = "local"
    agent.env.execute = exec_cmd
    return agent, workdir, base


class TestArchiveFailurePreBlock:
    def test_archive_failure_with_untracked_blocks_r1(self, local_git_repo):
        """Archive failure with untracked files present must return None (block episode)."""
        from condiag.round1_runner import _capture_snapshot

        agent_a, workdir, base = local_git_repo
        (workdir / "reproduce.py").write_text("x\n")

        snapshot_dir = workdir / "snapshot_blocked"
        snapshot_dir.mkdir()
        os.chmod(snapshot_dir, 0o555)

        try:
            snapshot = _capture_snapshot(agent_a, base, snapshot_dir)
            assert snapshot is None, \
                f"Expected None due to archive failure, got snapshot: {snapshot}"
        finally:
            os.chmod(snapshot_dir, 0o755)


class TestPreflightFairnessGate:
    def test_sha_mismatch_blocks_before_step(self, local_git_repo, monkeypatch):
        """Pre-step fairness gate: SHA mismatch must block BEFORE agent.step()."""
        from condiag.workspace import WorkspaceSnapshot, CaptureResult
        from condiag import branch_runner
        from condiag.branch_runner import run_branch, RestoreResult

        # Mock docker cp
        original_subprocess_run = branch_runner.subprocess.run

        def fake_docker_run(cmd, **kwargs):
            if cmd[0] == "docker" and cmd[1] == "cp":
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")
            return original_subprocess_run(cmd, **kwargs)

        def fake_restore_workspace(agent, snapshot, base_commit, **kwargs):
            return RestoreResult(ok=True, workspace_sha=snapshot.workspace_state_sha)

        def fake_capture_workspace_fingerprint(agent, base_commit):
            """Return a different workspace state than the snapshot expects."""
            ws = WorkspaceSnapshot(
                tracked_diff="diff --git a/x.py b/x.py\n-corrupted\n+state\n",
                base_commit_sha=base_commit,
            )
            return CaptureResult(ok=True, snapshot=ws)

        monkeypatch.setattr(branch_runner.subprocess, "run", fake_docker_run)
        monkeypatch.setattr(branch_runner, "restore_workspace", fake_restore_workspace)
        import condiag.workspace as _ws
        monkeypatch.setattr(_ws, "capture_workspace_fingerprint", fake_capture_workspace_fingerprint)

        agent_a, workdir, base = local_git_repo

        snapshot_a = WorkspaceSnapshot(tracked_diff="", base_commit_sha=base)

        step_calls = []

        def step(*args, **kwargs):
            step_calls.append(1)
            from minisweagent.exceptions import Submitted
            raise Submitted({"role": "exit", "content": "", "extra": {"exit_status": "Submitted", "submission": "x"}})

        agent_a.step = step

        from condiag import branch_builder
        original_bb = branch_builder.build_branch_messages
        branch_builder.build_branch_messages = lambda *a, **kw: []
        try:
            result = run_branch(
                agent_factory=lambda: agent_a,
                checkpoint_messages=[],
                base_commit=base,
                task="test",
                r1_n_calls=0, r1_cost=0.0,
                failure_witness=None,
                diagnosis=None,
                mode="sf",
                workspace_snapshot=snapshot_a,
            )
        finally:
            branch_builder.build_branch_messages = original_bb

        assert len(step_calls) == 0, \
            f"Expected 0 step() calls on fairness failure, got {len(step_calls)}"
        assert "preflight_fairness_failed" in result.termination_reason, \
            f"Wrong termination reason: {result.termination_reason}"


class TestFingerprintCapture:
    def test_capture_correctly_detects_tracked_and_untracked(self, local_git_repo):
        """capture_workspace_fingerprint must compute full SHA including untracked."""
        from condiag.workspace import capture_workspace_fingerprint, WorkspaceSnapshot

        agent_a, workdir, base = local_git_repo

        # Modify tracked
        (workdir / "original.py").write_text("modified\n")
        # Add untracked
        (workdir / "reproduce.py").write_text("print(1)\n")

        cr = capture_workspace_fingerprint(agent_a, base)
        assert cr.ok
        ws = cr.snapshot
        # SHA is consistent and not equal to a clean-state SHA
        assert ws.workspace_state_sha
        assert ws.tracked_diff_sha
        assert ws.untracked_manifest_sha

    def test_special_char_filenames_captured_correctly(self, local_git_repo):
        """Files with special characters in names must be captured without errors."""
        from condiag.workspace import capture_workspace_fingerprint

        agent_a, workdir, base = local_git_repo

        weird_name = "student's test.py"
        (workdir / weird_name).write_text("print('weird')\n")

        cr = capture_workspace_fingerprint(agent_a, base)
        assert cr.ok, f"Fingerprint failed: {cr.reason}"
        paths = {u.path for u in cr.snapshot.untracked_manifest}
        assert weird_name in paths, f"Special char file not captured: {paths}"

    def test_pipeline_failure_returns_empty(self, local_git_repo):
        """archive_untracked_files must return "" when git ls-files fails (pipefail)."""
        from condiag.workspace import archive_untracked_files

        agent_a, workdir, base = local_git_repo
        # Delete .git so git ls-files will fail
        subprocess.run(["bash", "-c", "rm -rf .git"], cwd=workdir, check=True)

        snapshot_dir = workdir / "snapshot_pf"
        result = archive_untracked_files(agent_a, snapshot_dir)
        assert result == "", f"Expected empty string on pipeline failure, got {result!r}"



class TestGitApplyIndexNewFile:
    """Regression guard: `git apply --index` keeps tracked-diff SHA stable."""

    def _git(self, workdir, cmd, check=True):
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=10, cwd=workdir,
        )
        if check and r.returncode != 0:
            raise RuntimeError(f"git {' '.join(cmd)}: {r.stderr[:200]}")
        return r

    def _diff_sha(self, workdir, base_commit) -> str:
        r = self._git(workdir, [
            "git", "diff", "--binary", "--no-ext-diff", base_commit,
        ])
        return hashlib.sha256(r.stdout.encode("utf-8")).hexdigest()[:16]

    def _apply_and_verify(self, repo, base_commit, apply_args, patch_path):
        """Reset, apply diff, return SHA. patch_path must be outside repo to survive `git clean -fd`."""
        self._git(repo, ["git", "reset", "--hard", base_commit])
        self._git(repo, ["git", "clean", "-fd", "-q"])
        self._git(repo, ["git", "apply", "--whitespace=nowarn", *apply_args, patch_path])
        return self._diff_sha(repo, base_commit)

    def test_apply_with_index_new_file(self, tmp_path):
        """New file: git apply --index must reproduce the same SHA;
        bare git apply produces a DIFFERENT SHA because the new file
        ends up untracked and invisible to `git diff base` comparison
        against the commit tree in certain environments (Docker)."""
        repo = tmp_path / "r1"
        repo.mkdir()
        self._git(repo, ["git", "init", "-q"])
        self._git(repo, ["git", "config", "user.email", "t@t"])
        self._git(repo, ["git", "config", "user.name", "t"])
        (repo / "f.py").write_text("base\n")
        self._git(repo, ["git", "add", "."])
        self._git(repo, ["git", "commit", "-q", "-m", "init"])
        base = self._git(repo, ["git", "rev-parse", "HEAD"]).stdout.strip()

        (repo / "f.py").write_text("modified\n")
        (repo / "new.py").write_text("import os\n")

        # Make the untracked file appear in git diff without staging its content
        self._git(repo, ["git", "add", "-N", "new.py"])

        diff_before = self._git(repo, [
            "git", "diff", "--binary", "--no-ext-diff", base,
        ]).stdout

        # Pre-conditions: diff must actually contain the new file
        assert "new.py" in diff_before, "diff must contain new.py (git add -N required)"
        assert "new file mode" in diff_before, "diff must mark new.py as new file"

        sha_orig = hashlib.sha256(diff_before.encode("utf-8")).hexdigest()[:16]
        patch_path = str(tmp_path / "restore.patch")
        Path(patch_path).write_text(diff_before)

        # Bare git apply: new file ends up untracked → SHA differs
        sha_without = self._apply_and_verify(repo, base, [], patch_path)
        assert sha_without != sha_orig, (
            "bare git apply must produce different SHA "
            "(new file not in tracked diff)"
        )

        # git apply --index: new file enters index → SHA matches
        sha_with = self._apply_and_verify(repo, base, ["--index"], patch_path)
        assert sha_with == sha_orig, "git apply --index must reproduce original SHA"

    def test_apply_with_index_existing_file(self, tmp_path):
        """Existing file only: SHA matches either way."""
        repo = tmp_path / "r2"
        repo.mkdir()
        self._git(repo, ["git", "init", "-q"])
        self._git(repo, ["git", "config", "user.email", "t@t"])
        self._git(repo, ["git", "config", "user.name", "t"])
        (repo / "f.py").write_text("original\n")
        self._git(repo, ["git", "add", "."])
        self._git(repo, ["git", "commit", "-q", "-m", "init"])
        base = self._git(repo, ["git", "rev-parse", "HEAD"]).stdout.strip()

        (repo / "f.py").write_text("modified\n")
        sha_orig = self._diff_sha(repo, base)
        diff_before = self._git(repo, [
            "git", "diff", "--binary", "--no-ext-diff", base,
        ]).stdout
        patch_path = str(tmp_path / "ex_restore.patch")
        Path(patch_path).write_text(diff_before)

        sha_without = self._apply_and_verify(repo, base, [], patch_path)
        assert sha_orig == sha_without, "bare apply: SHA mismatch"
        sha_with = self._apply_and_verify(repo, base, ["--index"], patch_path)
        assert sha_orig == sha_with, "--index: SHA mismatch"
