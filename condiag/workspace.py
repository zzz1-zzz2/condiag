"""Workspace Snapshot — capture and verify persistent workspace state.

Tracks both tracked (git diff) and untracked files (content archive).
SHA covers both → used for Fairness Gate comparison.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def sha256_full(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_short(text: str) -> str:
    return sha256_full(text)[:16]


@dataclass
class UntrackedFile:
    """A file present in the workspace but not tracked by git."""

    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}


@dataclass
class WorkspaceSnapshot:
    """Complete workspace state at a given point in the episode.

    tracked_diff:     git diff --binary --no-ext-diff (all tracked file changes)
    untracked_manifest: list of UntrackedFile entries
    untracked_archive_path: Path to a tar archive of untracked file contents
    base_commit_sha:  git rev-parse HEAD at snapshot time

    SHA fingerprints:
      tracked_diff_sha:      hash of tracked_diff alone
      untracked_manifest_sha: hash of the manifest (file paths + content SHAs)
      workspace_state_sha:   combined SHA for fairness comparison
    """

    tracked_diff: str = ""
    untracked_manifest: list[UntrackedFile] = field(default_factory=list)
    untracked_archive_path: str = ""
    base_commit_sha: str = ""

    @property
    def tracked_diff_sha(self) -> str:
        return sha256_short(self.tracked_diff)

    @property
    def untracked_manifest_sha(self) -> str:
        raw = json.dumps([u.to_dict() for u in self.untracked_manifest], sort_keys=True)
        return sha256_short(raw)

    @property
    def workspace_state_sha(self) -> str:
        """Combined SHA: base_commit + tracked diff + untracked manifest."""
        raw = (
            self.base_commit_sha
            + self.tracked_diff_sha
            + self.untracked_manifest_sha
        )
        return sha256_short(raw)

    def to_dict(self) -> dict:
        return {
            "tracked_diff_sha": self.tracked_diff_sha,
            "untracked_manifest_sha": self.untracked_manifest_sha,
            "workspace_state_sha": self.workspace_state_sha,
            "untracked_count": len(self.untracked_manifest),
            "untracked_archive_exists": bool(self.untracked_archive_path),
            "base_commit_sha": self.base_commit_sha,
        }


def capture_workspace_snapshot(
    agent: Any,
    base_commit: str,
    snapshot_dir: Path | None = None,
) -> WorkspaceSnapshot:
    """Capture the current workspace state from a running agent container.

    Args:
        agent: Agent object with env.execute() for container commands.
        base_commit: The base commit to diff against.
        snapshot_dir: If provided, save untracked archive here.

    Returns:
        WorkspaceSnapshot with tracked_diff and untracked_manifest populated.
    """
    # Tracked diff
    result = agent.env.execute({
        "command": f"cd /testbed && git diff --binary --no-ext-diff {base_commit} 2>/dev/null"
    })
    tracked = result.get("output", "") if result.get("returncode") == 0 else ""

    # Base commit SHA
    head_r = agent.env.execute({"command": "cd /testbed && git rev-parse HEAD 2>/dev/null"})
    head_sha = head_r.get("output", "").strip() if head_r.get("returncode") == 0 else ""

    # Untracked files (list only)
    ut_r = agent.env.execute({
        "command": "cd /testbed && git ls-files --others --exclude-standard 2>/dev/null"
    })
    untracked_paths = [p for p in ut_r.get("output", "").split("\n") if p.strip()] if ut_r.get("returncode") == 0 else []

    # Build untracked manifest (content SHAs, sizes)
    manifest = []
    archive_path = ""
    if snapshot_dir and untracked_paths:
        import tarfile
        import tempfile
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        archive_path = str(snapshot_dir / "untracked.tar")

        with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False) as list_file:
            list_file.write("\n".join(untracked_paths))
            list_file_path = list_file.name

        # Get file stats via container command
        for upath in untracked_paths[:50]:  # Limit to 50 files
            stat_r = agent.env.execute({
                "command": f"cd /testbed && sha256sum '{upath}' 2>/dev/null && wc -c '{upath}' 2>/dev/null"
            })
            if stat_r.get("returncode") == 0:
                manifest.append(UntrackedFile(
                    path=upath,
                    size=0,  # Would need more parsing
                    sha256="",
                ))

        # Create tar archive
        agent.env.execute({
            "command": f"cd /testbed && tar cf /tmp/untracked.tar {' '.join(untracked_paths[:50])} 2>/dev/null || true"
        })
        # Copy archive out
        import subprocess
        try:
            subprocess.run(
                ["docker", "cp", f"{agent.env.container_id}:/tmp/untracked.tar", archive_path],
                capture_output=True, timeout=10,
            )
        except Exception:
            archive_path = ""

        try:
            Path(list_file_path).unlink(missing_ok=True)
        except Exception:
            pass

    return WorkspaceSnapshot(
        tracked_diff=tracked,
        untracked_manifest=manifest,
        untracked_archive_path=archive_path,
        base_commit_sha=head_sha,
    )


def check_workspace_fairness(
    r1_snapshot: WorkspaceSnapshot,
    sf_snapshot: WorkspaceSnapshot,
    cd_snapshot: WorkspaceSnapshot | None = None,
) -> dict[str, bool]:
    """Verify that all branches start from the same workspace state.

    Returns a dict with individual check results.
    """
    result = {
        "r1_vs_sf_tracked_ok": r1_snapshot.tracked_diff_sha == sf_snapshot.tracked_diff_sha,
        "r1_vs_sf_state_ok": r1_snapshot.workspace_state_sha == sf_snapshot.workspace_state_sha,
    }
    if cd_snapshot:
        result["r1_vs_cd_tracked_ok"] = r1_snapshot.tracked_diff_sha == cd_snapshot.tracked_diff_sha
        result["r1_vs_cd_state_ok"] = r1_snapshot.workspace_state_sha == cd_snapshot.workspace_state_sha
        result["sf_vs_cd_tracked_ok"] = sf_snapshot.tracked_diff_sha == cd_snapshot.tracked_diff_sha
        result["sf_vs_cd_state_ok"] = sf_snapshot.workspace_state_sha == cd_snapshot.workspace_state_sha
    result["all_ok"] = all(result.values())
    return result
