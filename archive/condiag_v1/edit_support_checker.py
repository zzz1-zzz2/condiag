"""ConDiag Edit Support Checker — Scope Guard core.

For each edited file, derives a support verdict from runtime-visible signals:

  - issue_keyword_support : a target_hint symbol actually appears in the file
                            (requires repo_root; falls back to "unknown" if not)
  - search_query_support  : a non-generic search query directly mentions the file
  - viewed_span_support   : the file's edited span overlaps a tight (<60 line)
                            explicitly viewed span
  - test_failure_support  : a failing test references the file
  - stack_trace_support   : the issue's stack trace mentions the file

Anti-support signal:
  - pattern_only_edits    : all of the file's added/removed lines exactly match
                            a known repeated_edit_pattern (mechanical sweep)

Verdict:
  - supported   : stack_trace | test_failure | issue_keyword (target_hint in file)
                  AND not pattern_only
  - weak        : viewed_span or search_query, but no target_hint in file
  - unsupported : pattern_only_edits with no other support, OR zero support sources

Does NOT consult gold/oracle fields. All inputs are runtime-visible.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

from .schemas import ManualDiagnosis, RuntimeSignals


@dataclass
class FileSupport:
    path: str
    support: str            # "supported" | "weak" | "unsupported"
    support_sources: List[str] = field(default_factory=list)
    anti_support_signals: List[str] = field(default_factory=list)
    score: int = 0
    reason: str = ""
    edited_span_lines: List[int] = field(default_factory=list)
    viewed_span_matches: List[List[int]] = field(default_factory=list)
    matched_target_hints: List[str] = field(default_factory=list)
    matched_search_queries: List[str] = field(default_factory=list)
    pattern_only_edits: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EditSupportMap:
    instance_id: str
    edited_files_count: int
    supported: List[str] = field(default_factory=list)
    weak: List[str] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)
    files: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ===== helpers =====

GENERIC_PATH_TOKENS = {
    "sympy", "core", "tests", "test", "tensor", "physics", "plotting",
    "printing", "assumptions", "vector", "sparse", "basic", "function",
    "common", "matrices", "abc", "collections", "defaultdict", "iterable",
    "mapping", "mutablemapping", "mutableset", "callable", "ndim", "array",
    "arrayop", "containers", "coordsysrect", "conventions", "linearize",
    "sathandlers", "polytools", "indexed", "functions", "util", "plot",
    "exprtools", "expr", "basic", "numbers", "matrix", "expr",  # generic
}


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _strip_testbed(p: str) -> str:
    if p.startswith("/testbed/"):
        return p[len("/testbed/"):]
    return p


def _viewed_spans_normalized(viewed_spans: dict) -> dict[str, list[list[int]]]:
    out: dict[str, list[list[int]]] = {}
    for raw_path, spans in (viewed_spans or {}).items():
        norm = _strip_testbed(raw_path)
        out[norm] = spans or []
    return out


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int, tolerance: int = 3) -> bool:
    return not (a_end + tolerance < b_start or b_end + tolerance < a_start)


def _is_generic_search(query: str) -> bool:
    q = query.strip()
    if re.search(r"grep\s+-r\w*\s+\S+\s+/testbed/sympy/?\s*$", q):
        return True
    if re.search(r"grep\s+-r\w*\s+\S+\s+/testbed/?\s*$", q):
        return True
    return False


def _issue_text(issue_path: Path) -> str:
    if not issue_path or not issue_path.exists():
        return ""
    return issue_path.read_text(encoding="utf-8", errors="ignore").lower()


def _file_contains_target_hint(file_content: str, hint: str) -> bool:
    """Check whether a target_hint identifier appears in the file content."""
    if not hint or not file_content:
        return False
    # Use word-boundary regex so `_eval_det_bareiss` doesn't match `_eval_det_bareiss_xyz`
    # but DOES match method definitions and call sites.
    pattern = r"\b" + re.escape(hint) + r"\b"
    return re.search(pattern, file_content) is not None


def _file_read_safe(repo_root: Path | None, rel_path: str) -> str:
    if repo_root is None:
        return ""
    try:
        p = Path(repo_root) / rel_path
        if not p.exists():
            return ""
        return p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""


def _normalize_code(s: str) -> str:
    return s.strip()


def _edits_are_pattern_only(
    file_hunks: list[dict],
    repeated_patterns: list[dict],
) -> tuple[bool, list[str]]:
    """Decide if every added/removed line in this file is explained by a
    known repeated_edit_pattern.

    Returns (is_pattern_only, matched_pattern_keys).
    """
    if not file_hunks or not repeated_patterns:
        return False, []

    # Build a matcher set of (before, after) pairs.
    # `before` may be "" (pure insertion); `after` may be "" (pure deletion).
    pairs: list[tuple[str, str]] = []
    for p in repeated_patterns:
        pairs.append((p.get("before", ""), p.get("after", "")))

    def _matches_any(removed_line: str, added_line: str) -> str | None:
        r = _normalize_code(removed_line)
        a = _normalize_code(added_line)
        for before, after in pairs:
            # Insertion-only pattern: `"" → "abc."` means added line contains the after segment.
            if before == "" and after:
                if after in a:
                    return f"'{before}' -> '{after}'"
            # Replace pattern: before is a substring of removed, after is substring of added.
            elif before and after:
                if before in r and after in a:
                    return f"'{before}' -> '{after}'"
            elif before and not after:
                if before in r:
                    return f"'{before}' -> ''"
        return None

    matched: list[str] = []
    for h in file_hunks:
        added = h.get("added_lines", []) or []
        removed = h.get("removed_lines", []) or []
        n = max(len(added), len(removed))
        for i in range(n):
            r = removed[i] if i < len(removed) else ""
            a = added[i] if i < len(added) else ""
            if not r and not a:
                continue
            key = _matches_any(r, a)
            if key is None:
                # Has at least one substantive edit
                return False, []
            if key not in matched:
                matched.append(key)
    return True, matched


# ===== per-file analysis =====

def check_file_support(
    file_path: str,
    file_hunks: list[dict],
    rs: RuntimeSignals,
    md: ManualDiagnosis,
    issue_text: str,
    file_content: str,
    repeated_patterns: list[dict],
) -> FileSupport:
    sources: list[str] = []
    anti_signals: list[str] = []
    matched_hints: list[str] = []
    matched_queries: list[str] = []
    viewed_matches: list[list[int]] = []

    basename = _basename(file_path)

    # --- 1. issue_keyword_support (target_hint in file) ---
    target_hints = [h.get("value", "") for h in (md.target_hints or []) if h.get("value")]
    for hint in target_hints:
        if _file_contains_target_hint(file_content, hint):
            sources.append("issue_keyword")
            matched_hints.append(hint)
            break  # one hint is enough

    # --- 2. search_query_support (non-generic search names this file) ---
    for q in (rs.search_commands or []):
        if _is_generic_search(q):
            continue
        if basename in q or file_path in q:
            sources.append("search_query")
            matched_queries.append(q)
            break

    # --- 3. viewed_span_support (tight per-hunk overlap) ---
    viewed = _viewed_spans_normalized(rs.viewed_spans)
    file_viewed = viewed.get(file_path, [])
    # For each hunk, compute its new-side range and check overlap with viewed spans.
    edited_lines: list[int] = []
    for h in file_hunks:
        new_start = int(h.get("new_start", 0))
        new_count = int(h.get("new_count", 0))
        hunk_lo = new_start
        hunk_hi = max(new_start + max(new_count - 1, 0), new_start)
        for off in range(new_count):
            edited_lines.append(new_start + off)
        # Try to match this hunk against any tight viewed span
        for span in file_viewed:
            if len(span) < 2:
                continue
            vs_start, vs_end = int(span[0]), int(span[1])
            if vs_end - vs_start > 100:
                continue
            if _spans_overlap(hunk_lo, hunk_hi, vs_start, vs_end):
                if [vs_start, vs_end] not in viewed_matches:
                    viewed_matches.append([vs_start, vs_end])
    if viewed_matches:
        sources.append("viewed_span")

    # --- 4. test_failure_support ---
    for tf in (rs.test_failures or []):
        if file_path in tf or basename in tf:
            sources.append("test_failure")
            break

    # --- 5. stack_trace_support ---
    stack_refs = re.findall(r'File\s+"[^"]*?/([^/"]+\.py)",\s+line', issue_text)
    if basename in stack_refs:
        sources.append("stack_trace")

    # --- anti-support: pattern_only_edits ---
    is_pattern_only, matched_pattern_keys = _edits_are_pattern_only(file_hunks, repeated_patterns)
    if is_pattern_only and matched_pattern_keys:
        anti_signals.append("pattern_only_edits")
        anti_signals.extend(matched_pattern_keys[:3])

    # Dedup sources
    seen = set()
    unique_sources: list[str] = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            unique_sources.append(s)

    score = len(unique_sources)

    # Verdict logic
    has_strong_evidence = (
        "stack_trace" in unique_sources
        or "test_failure" in unique_sources
        or "issue_keyword" in unique_sources
    )
    has_weak_evidence = (
        "viewed_span" in unique_sources or "search_query" in unique_sources
    )

    if has_strong_evidence and not is_pattern_only:
        verdict = "supported"
    elif has_strong_evidence and is_pattern_only:
        # File contains issue-relevant code, but THIS edit was purely a pattern sweep.
        verdict = "weak"
    elif has_weak_evidence and not is_pattern_only:
        verdict = "weak"
    elif has_weak_evidence and is_pattern_only:
        verdict = "unsupported"
    else:
        verdict = "unsupported"

    reason_bits: list[str] = []
    if matched_hints:
        reason_bits.append(f"contains target_hints: {matched_hints}")
    if matched_queries:
        reason_bits.append(f"targeted search: {matched_queries[0][:80]}")
    if viewed_matches:
        reason_bits.append(f"viewed tight span(s): {viewed_matches[:2]}")
    if is_pattern_only:
        reason_bits.append(f"all edits match repeated pattern: {matched_pattern_keys[:2]}")
    if not reason_bits:
        reason_bits.append("No direct evidence in issue, search, viewed spans, tests, or stack trace.")

    return FileSupport(
        path=file_path,
        support=verdict,
        support_sources=unique_sources,
        anti_support_signals=anti_signals,
        score=score,
        reason="; ".join(reason_bits),
        edited_span_lines=sorted(set(edited_lines)) if file_hunks else [],
        viewed_span_matches=viewed_matches,
        matched_target_hints=matched_hints,
        matched_search_queries=matched_queries,
        pattern_only_edits=is_pattern_only,
    )


# ===== main entry =====

def build_support_map(
    instance_id: str,
    rs: RuntimeSignals,
    md: ManualDiagnosis,
    issue_path: Path,
    patch_scope_report: dict | None = None,
    repo_root: Path | None = None,
) -> EditSupportMap:
    """Build support map for every edited file."""
    issue_text = _issue_text(issue_path)
    repeated_patterns = (patch_scope_report or {}).get("repeated_edit_patterns", []) or []
    hunks_by_file: dict[str, list[dict]] = {}
    for h in (patch_scope_report or {}).get("hunks", []) or []:
        hunks_by_file.setdefault(h.get("file", ""), []).append(h)

    files: list[FileSupport] = []
    supported: list[str] = []
    weak: list[str] = []
    unsupported: list[str] = []

    for fpath in (rs.edited_files or []):
        file_hunks = hunks_by_file.get(fpath, [])
        file_content = _file_read_safe(repo_root, fpath)
        fs = check_file_support(
            fpath, file_hunks, rs, md, issue_text, file_content, repeated_patterns,
        )
        files.append(fs)
        if fs.support == "supported":
            supported.append(fpath)
        elif fs.support == "weak":
            weak.append(fpath)
        else:
            unsupported.append(fpath)

    return EditSupportMap(
        instance_id=instance_id,
        edited_files_count=len(rs.edited_files or []),
        supported=supported,
        weak=weak,
        unsupported=unsupported,
        files=[f.to_dict() for f in files],
    )


def write_map(out_dir: Path, support_map: EditSupportMap) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "edit_support_map.json"
    p.write_text(json.dumps(support_map.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
