"""ConDiag Patch Scope Analyzer — RESTRAIN / Scope Guard component.

Reads runtime-visible patch shape (no gold/oracle data) and produces:
  - changed_files / changed_lines_total / changed_lines_added / removed
  - hunks parsed from patch.diff (per-file, with concrete before/after)
  - repeated_edit_patterns (concrete before->after pairs, not abstract shapes)
  - scope_anomaly_score + scope_anomaly_level (reuses scope_guard.scoring)

Inputs (all runtime-visible):
  - case_bundle/patch.diff  (agent's submitted patch)
  - case_bundle/runtime_signals.json

Output:
  - patch_scope_report.json (under <out>/<instance>/)
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

from . import scope_guard as sg
from .schemas import RuntimeSignals


@dataclass
class Hunk:
    file: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    added_lines: List[str] = field(default_factory=list)
    removed_lines: List[str] = field(default_factory=list)
    added_count: int = 0
    removed_count: int = 0


@dataclass
class ConcretePattern:
    before: str
    after: str
    file_count: int
    sample_files: List[str]


@dataclass
class PatchScopeReport:
    instance_id: str
    changed_files_count: int
    changed_lines_total: int
    changed_lines_added: int
    changed_lines_removed: int
    hunks: List[dict]
    repeated_edit_patterns: List[dict]   # concrete before/after pairs
    abstract_pattern_shapes: List[dict]  # pass-through from runtime_signals
    scope_anomaly_score: int
    scope_anomaly_level: str  # "none" | "warning" | "strong"
    scope_signals: dict
    submitted_without_tests: bool
    test_runs_count: int

    def to_dict(self) -> dict:
        return asdict(self)


# ===== patch.diff parser =====

_DIFF_FILE_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_DIFF_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_patch_diff(patch_text: str) -> List[Hunk]:
    """Parse a unified diff into a list of Hunks.

    Each Hunk captures its source file (b/ side, which is the new path),
    line ranges, and the added/removed lines (without the leading +/- marker).
    """
    hunks: List[Hunk] = []
    current_file: str | None = None
    current_hunk: Hunk | None = None

    for raw in patch_text.splitlines():
        if raw.startswith("diff --git "):
            # Previous file's last hunk must be flushed before switching files
            if current_hunk is not None:
                hunks.append(current_hunk)
                current_hunk = None
            m = _DIFF_FILE_HEADER.match(raw)
            if m:
                current_file = m.group(2)
            continue
        if raw.startswith("@@"):
            m = _DIFF_HUNK_HEADER.match(raw)
            if m and current_file:
                if current_hunk:
                    hunks.append(current_hunk)
                old_start = int(m.group(1))
                old_count = int(m.group(2)) if m.group(2) else 1
                new_start = int(m.group(3))
                new_count = int(m.group(4)) if m.group(4) else 1
                current_hunk = Hunk(
                    file=current_file,
                    old_start=old_start, old_count=old_count,
                    new_start=new_start, new_count=new_count,
                )
            continue
        if current_hunk is None:
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("+"):
            current_hunk.added_lines.append(raw[1:])
            current_hunk.added_count += 1
        elif raw.startswith("-"):
            current_hunk.removed_lines.append(raw[1:])
            current_hunk.removed_count += 1
    if current_hunk:
        hunks.append(current_hunk)
    return hunks


# ===== concrete pattern extraction =====

import difflib

def _normalize_code_line(s: str) -> str:
    return s.strip()


def _diff_segments(before: str, after: str) -> tuple[str, str] | None:
    """Return (removed_segment, added_segment) if before->after is a single
    contiguous replace. Returns None for multi-region diffs or trivial changes.
    """
    if not before or not after or before == after:
        return None
    sm = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    ops = sm.get_opcodes()
    # We want exactly: equal | replace | equal (where equal on either side may be empty)
    if len(ops) > 3:
        return None
    replace_ops = [op for op in ops if op[0] == "replace"]
    insert_ops = [op for op in ops if op[0] == "insert"]
    delete_ops = [op for op in ops if op[0] == "delete"]
    if len(replace_ops) == 1 and not insert_ops and not delete_ops:
        op = replace_ops[0]
        return (before[op[1]:op[2]], after[op[3]:op[4]])
    if len(insert_ops) == 1 and not replace_ops and not delete_ops:
        op = insert_ops[0]
        return ("", after[op[3]:op[4]])
    if len(delete_ops) == 1 and not replace_ops and not insert_ops:
        op = delete_ops[0]
        return (before[op[1]:op[2]], "")
    return None


def _tokenize_identifier_chain(s: str) -> List[str]:
    """Find dotted identifier chains like 'collections.abc.Iterable' or 'A.B.C'."""
    import re
    return re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\b", s)


def extract_concrete_patterns(hunks: List[Hunk]) -> List[ConcretePattern]:
    """Cluster added/removed line pairs by their concrete before->after shape.

    Two strategies:
      (a) Line-level: when a removed line and added line in the same hunk
          differ by exactly one contiguous segment, record that segment pair.
      (b) Chain-level: when dotted identifier chains like A.B.C in removed
          lines correspond to A.B.X.C in added lines, record the chain shift.

    Returns patterns observed in >=2 files, sorted by file_count desc.
    """
    pair_to_files: dict[str, dict] = {}

    by_file: dict[str, List[Hunk]] = {}
    for h in hunks:
        by_file.setdefault(h.file, []).append(h)

    def _record(before: str, after: str, file: str, seen: set) -> None:
        if not before and not after:
            return
        key = f"{before!r} || {after!r}"
        if key in seen:
            return
        seen.add(key)
        pair_to_files.setdefault(key, {"before": before, "after": after, "files": set()})
        pair_to_files[key]["files"].add(file)

    for file, file_hunks in by_file.items():
        seen_pairs_this_file: set[str] = set()
        for h in file_hunks:
            removed = [_normalize_code_line(x) for x in h.removed_lines if x.strip()]
            added = [_normalize_code_line(x) for x in h.added_lines if x.strip()]

            # Strategy (a): line-level contiguous-replace diff
            n = max(len(removed), len(added))
            for i in range(n):
                r = removed[i] if i < len(removed) else ""
                a = added[i] if i < len(added) else ""
                seg = _diff_segments(r, a)
                if seg:
                    _record(seg[0], seg[1], file, seen_pairs_this_file)

            # Strategy (b): dotted-chain shifts across the whole hunk
            removed_chains = set()
            added_chains = set()
            for r in removed:
                removed_chains.update(_tokenize_identifier_chain(r))
            for a in added:
                added_chains.update(_tokenize_identifier_chain(a))
            # Match A.B.C (removed) -> A.B.X.C (added) by checking if added
            # has a chain whose tail equals the removed chain after stripping
            # one inner segment.
            for rchain in removed_chains:
                rparts = rchain.split(".")
                for achain in added_chains:
                    aparts = achain.split(".")
                    if len(aparts) != len(rparts) + 1:
                        continue
                    # find if aparts == rparts with one extra element inserted
                    for k in range(len(aparts)):
                        candidate = aparts[:k] + aparts[k+1:]
                        if candidate == rparts:
                            _record(rchain, achain, file, seen_pairs_this_file)
                            break

    patterns: List[ConcretePattern] = []
    for key, info in pair_to_files.items():
        if len(info["files"]) < 2:
            continue
        patterns.append(ConcretePattern(
            before=info["before"],
            after=info["after"],
            file_count=len(info["files"]),
            sample_files=sorted(list(info["files"]))[:5],
        ))
    patterns.sort(key=lambda p: p.file_count, reverse=True)
    return patterns


# ===== main entry =====

def analyze(
    instance_id: str,
    patch_text: str,
    rs: RuntimeSignals,
) -> PatchScopeReport:
    """Build the PatchScopeReport from runtime-visible signals + patch.diff."""
    hunks = parse_patch_diff(patch_text)
    concrete = extract_concrete_patterns(hunks)

    # Reuse Scope Guard scoring (5 binary signals)
    sg_result = sg.score_scope(rs)
    level = "none"
    if sg_result.triggered_strong:
        level = "strong"
    elif sg_result.triggered_warning:
        level = "warning"

    return PatchScopeReport(
        instance_id=instance_id,
        changed_files_count=rs.changed_files_count,
        changed_lines_total=rs.changed_lines_total,
        changed_lines_added=getattr(rs, "changed_lines_added", 0) or 0,
        changed_lines_removed=getattr(rs, "changed_lines_removed", 0) or 0,
        hunks=[asdict(h) for h in hunks],
        repeated_edit_patterns=[asdict(p) for p in concrete],
        abstract_pattern_shapes=list(rs.repeated_edit_patterns or []),
        scope_anomaly_score=sg_result.score,
        scope_anomaly_level=level,
        scope_signals={
            "signal_values": sg_result.signal_values,
            "signals": sg_result.signals,
            "threshold_warning": sg_result.threshold_warning,
            "threshold_strong": sg_result.threshold_strong,
        },
        submitted_without_tests=bool(rs.submitted_without_tests),
        test_runs_count=rs.test_runs_count if hasattr(rs, "test_runs_count") else 0,
    )


def write_report(out_dir: Path, report: PatchScopeReport) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "patch_scope_report.json"
    p.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
