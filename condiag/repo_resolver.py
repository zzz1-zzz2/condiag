"""Resolve and validate a clean repo at base_commit for ConDiag retrieval.

The retrieval executor must operate on repo@base_commit, never on the patched
attempt tree. This module:
  - Verifies the path is a git repo
  - Reads HEAD and dirty status
  - Cross-checks HEAD against the case's base_commit (from task.json)
  - Emits a repo_resolution.json describing what was used

Does NOT clone, does NOT checkout. If the repo is not at base_commit, it
returns an error result and the caller decides what to do (the CLI surfaces
a clear message; no silent fallback).
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


@dataclass
class RepoResolution:
    instance_id: str = ""
    repo_path: str = ""
    base_commit_expected: str = ""
    head_commit_actual: str = ""
    dirty: bool = True
    source: str = ""               # 'clean_checkout' | 'provided' | 'mismatch' | 'error'
    ok: bool = False
    error: Optional[str] = None
    git_remote: Optional[str] = None
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def resolve(repo_path: Path, instance_id: str, base_commit: str) -> RepoResolution:
    """Validate that repo_path is a git repo at base_commit.

    Returns a RepoResolution with ok=True only if HEAD matches base_commit
    and the tree is not dirty.
    """
    res = RepoResolution(
        instance_id=instance_id,
        repo_path=str(repo_path),
        base_commit_expected=base_commit,
    )

    if not Path(repo_path).is_dir():
        res.source = "error"
        res.error = f"repo_path does not exist or is not a directory: {repo_path}"
        return res

    # 1. Is it a git repo?
    try:
        head = _git(repo_path, "rev-parse", "HEAD")
        res.head_commit_actual = head
    except Exception as e:
        res.source = "error"
        res.error = f"not a git repo or git command failed: {e}"
        return res

    # 2. Dirty?
    try:
        porcelain = _git(repo_path, "status", "--porcelain")
        res.dirty = bool(porcelain.strip())
    except Exception as e:
        res.source = "error"
        res.error = f"git status failed: {e}"
        return res

    # 3. HEAD matches base_commit?
    if not base_commit:
        res.source = "error"
        res.error = "base_commit is empty in task.json; cannot validate repo"
        return res

    # Compare prefix (base_commit may be a shortened SHA)
    if head.startswith(base_commit) or base_commit.startswith(head):
        res.source = "clean_checkout"
        if res.dirty:
            res.source = "provided"
            res.error = "repo is at base_commit but tree is dirty (run git reset --hard + git clean -fdx)"
            return res
        res.ok = True
        return res

    # Mismatch
    res.source = "mismatch"
    res.error = (
        f"HEAD={head[:12]} does not match base_commit={base_commit[:12]}. "
        f"Run: git -C {repo_path} reset --hard {base_commit} && git -C {repo_path} clean -fdx"
    )
    return res


def write_resolution(out_dir: Path, res: RepoResolution) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "repo_resolution.json"
    p.write_text(json.dumps(res.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
