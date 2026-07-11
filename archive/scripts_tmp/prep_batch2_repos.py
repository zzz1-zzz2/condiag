"""Prepare repo_base worktrees for all 17 Batch2 instances.

For each Batch2 instance:
  1. Look up repo + base_commit from swe-bench_verified
  2. Map repo -> cache clone path
  3. `git worktree add --detach <workspace>/<iid>/repo_base <base_commit>`

Skips instances whose worktree already exists at the right commit.

Run:
    python3 -m scripts_tmp.prep_batch2_repos
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from experiments.manifest_builder import _load_swe_bench_verified


WORKSPACES_ROOT = Path("/home/swelite/condiag/workspaces")
REPO_CACHE_ROOT = Path("/mnt/d/condiag-artifacts/cache/repos")

# repo slug -> cache clone dir name
REPO_CACHE_MAP = {
    "astropy/astropy": "github.com__astropy__astropy",
    "django/django": "github.com__django__django",
    "scikit-learn/scikit-learn": "github.com__scikit-learn__scikit-learn",
    "sympy/sympy": "github.com__sympy__sympy",
}

BATCH2_INSTANCES = [
    "astropy__astropy-14995",
    "django__django-10880",
    "django__django-11099",
    "django__django-11179",
    "django__django-11603",
    "django__django-11815",
    "django__django-12125",
    "django__django-13028",
    "django__django-13158",
    "django__django-13513",
    "django__django-14349",
    "django__django-15104",
    "django__django-15973",
    "scikit-learn__scikit-learn-25232",
    "sympy__sympy-19954",
    "sympy__sympy-20428",
    "sympy__sympy-20590",
]


def _run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str, str]:
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def prep_one(iid: str, repo_slug: str, base_commit: str) -> str:
    """Returns a short status string for logging."""
    cache = REPO_CACHE_ROOT / REPO_CACHE_MAP[repo_slug]
    if not cache.is_dir():
        return f"MISSING_CACHE: {cache}"

    repo_base = WORKSPACES_ROOT / iid / "repo_base"
    git_marker = repo_base / ".git"

    # If worktree exists and is at the right commit, skip
    if git_marker.is_file() or git_marker.is_dir():
        rc, out, _ = _run(["git", "rev-parse", "HEAD"], cwd=repo_base)
        actual = out.strip()
        if rc == 0 and actual == base_commit:
            return f"OK_exists:{actual[:8]}"
        # exists but wrong commit — try checkout (only safe if it's a worktree we own)
        rc, _, err = _run(["git", "checkout", "-q", base_commit], cwd=repo_base)
        if rc == 0:
            return f"OK_checked_out:{base_commit[:8]}"
        return f"FAIL_checkout:{err[:80]}"

    # Fresh worktree
    repo_base.parent.mkdir(parents=True, exist_ok=True)
    # Clean half-written non-git dir if present
    if repo_base.is_dir() and not git_marker.exists():
        import shutil
        shutil.rmtree(repo_base)

    rc, _, err = _run(
        ["git", "worktree", "add", "--detach", str(repo_base), base_commit],
        cwd=cache,
    )
    if rc == 0:
        return f"OK_worktree:{base_commit[:8]}"
    return f"FAIL_worktree:{err[:120]}"


def main() -> int:
    verified = _load_swe_bench_verified()
    print(f"Prepping {len(BATCH2_INSTANCES)} Batch2 worktrees under {WORKSPACES_ROOT}")
    print()
    ok = 0
    fail = 0
    for iid in BATCH2_INSTANCES:
        meta = verified.get(iid)
        if not meta:
            print(f"  {iid:42s}  MISSING_IN_VERIFIED")
            fail += 1
            continue
        status = prep_one(iid, meta["repo"], meta["base_commit"])
        marker = "OK " if status.startswith("OK") else "XX "
        print(f"  [{marker}] {iid:42s}  {status}")
        if status.startswith("OK"):
            ok += 1
        else:
            fail += 1
    print()
    print(f"Summary: {ok} OK, {fail} fail")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
