"""Patch extractor — extract structured signals from a git diff."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from condiag.diagnosis.signals.schema import PatchSignals


# ── Patterns ────────────────────────────────────────────────────────

_RE_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
_RE_ADDED_LINE = re.compile(r"^\+[^+]")
_RE_DELETED_LINE = re.compile(r"^-[^-]")

# Config file paths (by path component)
_CONFIG_DIRS = frozenset({".circleci", ".github", ".gitlab", "ci", "build"})
_CONFIG_FILES = frozenset({
    "pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "makefile",
    "dockerfile", "requirements.txt", "requirements-dev.txt",
    "environment.yml", "conda.recipe", ".gitignore",
})


def extract_patch_signals(patch_text: str) -> PatchSignals:
    """Extract structured signals from a git diff patch text.

    Returns:
        PatchSignals with all fields populated.
    """
    signals = PatchSignals()
    if not patch_text or not patch_text.strip():
        return signals

    lines = patch_text.split("\n")
    signals.patch_size_chars = len(patch_text)
    signals.diff_total_lines = len(lines)

    # Extract edited files (uses shlex-based parser for quoted filenames)
    from condiag.integrity import extract_changed_files as _extract_files
    signals.edited_files = _extract_files(patch_text)

    current_file = ""
    for line in lines:
        m = re.match(r"^diff --git a/(\S+) b/(\S+)", line)
        if m:
            current_file = m.group(2)
            in_diff = True
            continue
        if current_file and not line.startswith("diff --git "):
            if _RE_ADDED_LINE.match(line):
                signals.added_lines += 1
                signals.changed_lines += 1
            elif _RE_DELETED_LINE.match(line):
                signals.deleted_lines += 1
                signals.changed_lines += 1
            if _RE_HUNK_HEADER.match(line):
                signals.hunk_count += 1

    # Classify edited files
    from pathlib import PurePosixPath
    source_files: list[str] = []
    test_files: list[str] = []
    config_files: list[str] = []
    for f in signals.edited_files:
        pp = PurePosixPath(f)
        name = pp.name.lower()
        parts = [p.lower() for p in pp.parts]
        is_test = (
            any(p in {"test", "tests", "testing"} for p in parts[:-1])
            or name.startswith("test_")
            or name.endswith("_test.py")
        )
        is_config = (
            name in _CONFIG_FILES
            or any(p in _CONFIG_DIRS for p in parts)
        )
        if is_test:
            test_files.append(f)
        elif is_config:
            config_files.append(f)
        else:
            source_files.append(f)

    signals.introduced_config_change = bool(config_files)
    signals.edited_files = list(signals.edited_files)  # preserve order

    return signals
