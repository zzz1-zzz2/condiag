"""Shared helpers for trajectory parsers.

These helpers are deliberately factored out so that mini-SWE, Agentless, and
future parsers can share the same patch / command / test-output parsing
without duplicating logic.

Strict policy (see base.py):
- Helpers only extract runtime-visible facts.
- No gold/oracle/official-eval fields ever leak through here.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------

_TESTBED_PREFIX = "/testbed/"


def normalize_repo_path(path: str) -> str:
    """Best-effort normalize an absolute container path to repo-relative.

    Drops /testbed/ prefix and /home/<repo>/... prefix when the suffix looks
    like a source path. For paths that don't match a known prefix, the leading
    '/' is stripped so suffix resolution can still match at evaluation time.
    """
    p = (path or "").strip().strip("'\"")
    if not p:
        return ""
    if p.startswith(_TESTBED_PREFIX):
        p = p[len(_TESTBED_PREFIX):]
    elif p.startswith("/home/"):
        rest = p[len("/home/"):]
        parts = rest.split("/", 1)
        if len(parts) == 2:
            # Drop the first segment (container home dir) — keep tail.
            p = parts[1]
        else:
            return ""
    elif p.startswith("/"):
        p = p.lstrip("/")
    if p.startswith("./"):
        p = p[2:]
    return p


# ---------------------------------------------------------------------------
# Patch (unified diff) parsing
# ---------------------------------------------------------------------------

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_unified_diff(diff_text: str) -> dict:
    """Extract structured facts from a unified diff.

    Returns dict with:
        edited_files   — repo-relative paths (from `diff --git a/X b/X`)
        edited_spans   — {path: [start_line, ...]} first removed line of each hunk
        hunks_total    — count of @@ hunks across all files
        added          — total `+` content lines (non-header)
        removed        — total `-` content lines (non-header)
    """
    edited_files: list[str] = []
    edited_spans: dict[str, list[int]] = {}
    hunks_total = 0
    added = 0
    removed = 0

    current_file: str | None = None

    for raw in (diff_text or "").splitlines():
        if raw.startswith("diff --git "):
            parts = raw.split()
            if len(parts) >= 4:
                b = parts[-1]
                if b.startswith("b/"):
                    b = b[2:]
                current_file = b
                if current_file and current_file not in edited_files:
                    edited_files.append(current_file)
            continue
        m = _HUNK_HEADER.match(raw)
        if m:
            hunks_total += 1
            # old start (group 1) is the original line where the hunk starts
            old_start = int(m.group(1))
            if current_file:
                edited_spans.setdefault(current_file, []).append(old_start)
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            added += 1
        elif raw.startswith("-"):
            removed += 1

    return {
        "edited_files": edited_files,
        "edited_spans": edited_spans,
        "hunks_total": hunks_total,
        "added": added,
        "removed": removed,
    }


# ---------------------------------------------------------------------------
# Bash command classification
# ---------------------------------------------------------------------------

_TEST_CMD_HINTS = (
    "pytest",
    "python -m pytest",
    "python -m unittest",
    "nosetests",
    "tox ",
    "runtests",
)

_SEARCH_CMD_HINTS = (
    "grep",
    "rg ",
    "ag ",
    "find ",
)


def is_test_command(cmd: str) -> bool:
    c = (cmd or "").strip()
    if not c:
        return False
    return any(h in c for h in _TEST_CMD_HINTS)


def is_search_command(cmd: str) -> bool:
    c = (cmd or "").strip()
    if not c:
        return False
    return any(h in c for h in _SEARCH_CMD_HINTS)


def looks_like_complete_task(cmd: str) -> bool:
    return "COMPLETE_TASK" in (cmd or "")


# ---------------------------------------------------------------------------
# Test output parsing
# ---------------------------------------------------------------------------

_PYTEST_FAILED = re.compile(r"FAILED\s+(\S+?)::(\S+)")
_PYTEST_ERROR_LINE = re.compile(r"^E\s+\w")
_PYTEST_SHORT_TEST_SUMMARY = re.compile(r"^=+ short test summary info =+$")


def extract_failed_tests_from_output(output: str) -> list[str]:
    """Pull `FAILED path::test` nodes from pytest output."""
    if not output:
        return []
    out: list[str] = []
    for m in _PYTEST_FAILED.finditer(output):
        node = f"{m.group(1)}::{m.group(2)}"
        if node not in out:
            out.append(node)
    return out


# ---------------------------------------------------------------------------
# XML-ish tag block extraction (EXPLORE_CONTEXT / PATCH_CONTEXT)
# ---------------------------------------------------------------------------


def extract_tag_blocks(text: str, tag: str) -> list[str]:
    """Return inner payload list for <tag>...</tag> blocks. Case-sensitive."""
    if not text:
        return []
    pattern = rf"<{re.escape(tag)}>\s*([\s\S]*?)\s*</{re.escape(tag)}>"
    return [m.group(1) for m in re.finditer(pattern, text)]


def parse_file_lines_pairs(block: str) -> dict[str, list[list[int]]]:
    """Parse blocks of:

        File: <path>
        Lines: <start>-<end>

    Returns {repo_relative_path: [[start, end], ...]}.
    """
    result: dict[str, list[list[int]]] = {}
    current_file = ""
    for raw in (block or "").splitlines():
        line = (raw or "").strip()
        if not line:
            continue
        if line.startswith("File:"):
            f = line[len("File:"):].strip()
            current_file = normalize_repo_path(f)
            continue
        if line.startswith("Lines:") and current_file:
            m = re.match(r"(\d+)\s*-\s*(\d+)", line[len("Lines:"):].strip())
            if not m:
                continue
            a, b = int(m.group(1)), int(m.group(2))
            if a > b:
                a, b = b, a
            result.setdefault(current_file, []).append([a, b])
    return result


# ---------------------------------------------------------------------------
# Bash code block extraction
# ---------------------------------------------------------------------------

_BASH_BLOCK = re.compile(r"```bash\s*\r?\n([\s\S]*?)\r?\n```")


def extract_bash_blocks(content: str) -> list[str]:
    """Return the inner command text of every ```bash``` block in `content`."""
    if not content:
        return []
    return [m.group(1).strip() for m in _BASH_BLOCK.finditer(content)]


# ---------------------------------------------------------------------------
# Misc utilities
# ---------------------------------------------------------------------------


def safe_head(text: str, n: int = 1500) -> str:
    if not text:
        return ""
    return text[:n]
