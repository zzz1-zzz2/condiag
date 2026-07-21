"""Patch Integrity Gate - block invalid patches before Harness.

Verifies the evaluation_patch before it is sent to the official SWE-bench Harness.
Also extracts basic patch metadata (changed files, size) for the audit trail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_RE_DIFF_HEADER = re.compile(r"^diff --git a/.* b/", re.MULTILINE)
_RE_HUNK = re.compile(r"^@@\s*-?\d", re.MULTILINE)
_RE_NEW_FILE = re.compile(r"^new file mode", re.MULTILINE)
_RE_PARENT_TRAVERSAL = re.compile(r"\.\./|\.\.\\")
_RE_ABSOLUTE = re.compile(r"^/|[A-Z]:\\")
_RE_DOT_GIT = re.compile(r"(^|/)(\.git)(/|$)")


@dataclass
class PatchIntegrityReport:
    ok: bool
    status: str
    reason: str
    changed_files: list
    patch_size: int
    fallback_used: bool
    consistency: str
    submitted_sha: str
    workspace_sha: str
    evaluation_sha: str

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status": self.status,
            "reason": self.reason,
            "changed_files": self.changed_files,
            "patch_size": self.patch_size,
            "fallback_used": self.fallback_used,
            "consistency": self.consistency,
            "submitted_sha": self.submitted_sha,
            "workspace_sha": self.workspace_sha,
            "evaluation_sha": self.evaluation_sha,
        }


def extract_changed_files(patch_text):
    if not patch_text:
        return []
    files = []
    for match in re.finditer(r"^diff --git a/(\S+) b/(\S+)", patch_text, re.MULTILINE):
        path = match.group(2)
        if path and path != "/dev/null":
            files.append(path)
    return files


def is_safe_path(file_path):
    if not file_path:
        return False
    if _RE_PARENT_TRAVERSAL.search(file_path):
        return False
    if _RE_ABSOLUTE.match(file_path):
        return False
    if _RE_DOT_GIT.search(file_path):
        return False
    return True


def is_valid_unified_diff(patch_text):
    if not patch_text or not patch_text.strip():
        return False
    has_diff_header = bool(_RE_DIFF_HEADER.search(patch_text))
    has_hunk_or_new = bool(_RE_HUNK.search(patch_text) or _RE_NEW_FILE.search(patch_text))
    return has_diff_header and has_hunk_or_new


def check_patch_integrity(
    termination_reason,
    agent_submission=None,
    workspace_patch="",
    evaluation_patch="",
    agent_workdir=None,
):
    """Validate a patch before Harness submission.

    Decision rules (in order):
      1. termination_reason must be submitted
      2. evaluation_patch must be non-empty
      3. evaluation_patch must be a valid unified diff
      4. All changed file paths must be safe
      5. submitted vs workspace consistency (when explicit submission is used)
      6. If agent_workdir provided, git apply --check must succeed
    """
    from condiag.patch_artifacts import sha256_short, canonicalize_patch

    changed_files = extract_changed_files(evaluation_patch)
    patch_size = len(evaluation_patch) if evaluation_patch else 0
    submitted = getattr(agent_submission, "selected_patch", "") if agent_submission else ""
    fallback_used = bool(
        agent_submission and getattr(agent_submission, "selected_source", "") == "workspace_diff_fallback"
    )
    submitted_sha = sha256_short(canonicalize_patch(submitted)) if submitted else ""
    workspace_sha = sha256_short(canonicalize_patch(workspace_patch)) if workspace_patch else ""
    evaluation_sha = sha256_short(canonicalize_patch(evaluation_patch)) if evaluation_patch else ""

    # 1. Termination reason
    if termination_reason != "submitted":
        return PatchIntegrityReport(
            ok=False, status="invalid_termination",
            reason="termination_reason={!r}".format(termination_reason),
            changed_files=changed_files, patch_size=patch_size, fallback_used=fallback_used,
            consistency="n/a", submitted_sha=submitted_sha, workspace_sha=workspace_sha,
            evaluation_sha=evaluation_sha,
        )

    # 2. Empty patch
    if not evaluation_patch or not evaluation_patch.strip():
        return PatchIntegrityReport(
            ok=False, status="invalid_empty", reason="evaluation_patch is empty",
            changed_files=[], patch_size=0, fallback_used=fallback_used,
            consistency="empty", submitted_sha=submitted_sha, workspace_sha=workspace_sha,
            evaluation_sha=evaluation_sha,
        )

    # 3. Not a unified diff
    if not is_valid_unified_diff(evaluation_patch):
        return PatchIntegrityReport(
            ok=False, status="invalid_diff_format",
            reason="Not a valid unified diff (no diff --git or hunk)",
            changed_files=changed_files, patch_size=patch_size, fallback_used=fallback_used,
            consistency="n/a", submitted_sha=submitted_sha, workspace_sha=workspace_sha,
            evaluation_sha=evaluation_sha,
        )

    # 4. Unsafe paths
    unsafe = [p for p in changed_files if not is_safe_path(p)]
    if unsafe:
        return PatchIntegrityReport(
            ok=False, status="invalid_unsafe_path",
            reason="Unsafe paths: {}".format(unsafe),
            changed_files=changed_files, patch_size=patch_size, fallback_used=fallback_used,
            consistency="n/a", submitted_sha=submitted_sha, workspace_sha=workspace_sha,
            evaluation_sha=evaluation_sha,
        )

    # 5. Consistency: explicit submission vs workspace
    if not fallback_used and submitted and workspace_patch:
        a = canonicalize_patch(submitted)
        w = canonicalize_patch(workspace_patch)
        if a != w:
            return PatchIntegrityReport(
                ok=False, status="invalid_mismatch",
                reason="submitted patch != workspace patch",
                changed_files=changed_files, patch_size=patch_size, fallback_used=fallback_used,
                consistency="mismatch", submitted_sha=submitted_sha, workspace_sha=workspace_sha,
                evaluation_sha=evaluation_sha,
            )
        consistency = "consistent"
    elif fallback_used:
        consistency = "fallback"
    elif not submitted and not workspace_patch:
        consistency = "empty"
    else:
        consistency = "n/a"

    # 6. git apply --check (if workdir available)
    if agent_workdir is not None:
        try:
            import subprocess
            (agent_workdir / "test.patch").write_text(evaluation_patch)
            r = subprocess.run(
                ["git", "apply", "--check", str(agent_workdir / "test.patch")],
                capture_output=True, text=True, cwd=agent_workdir, timeout=10,
            )
            (agent_workdir / "test.patch").unlink()
            if r.returncode != 0:
                return PatchIntegrityReport(
                    ok=False, status="invalid_unapplyable",
                    reason="git apply --check failed: {}".format(r.stderr[:200]),
                    changed_files=changed_files, patch_size=patch_size, fallback_used=fallback_used,
                    consistency=consistency, submitted_sha=submitted_sha,
                    workspace_sha=workspace_sha, evaluation_sha=evaluation_sha,
                )
        except Exception as e:
            return PatchIntegrityReport(
                ok=False, status="invalid_unapplyable",
                reason="git apply --check exception: {}".format(e),
                changed_files=changed_files, patch_size=patch_size, fallback_used=fallback_used,
                consistency=consistency, submitted_sha=submitted_sha,
                workspace_sha=workspace_sha, evaluation_sha=evaluation_sha,
            )

    return PatchIntegrityReport(
        ok=True, status="valid", reason="All checks passed",
        changed_files=changed_files, patch_size=patch_size, fallback_used=fallback_used,
        consistency=consistency, submitted_sha=submitted_sha,
        workspace_sha=workspace_sha, evaluation_sha=evaluation_sha,
    )
