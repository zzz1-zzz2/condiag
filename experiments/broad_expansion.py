"""Broad Expansion engine (D4-6 + D4-6.1) — generic lexical context expansion baseline.

This module is **structurally separated** from ConDiag core. It:

  - Does NOT import the ConDiag retrieval / evidence-selector / manual-retrieval
    modules (see FORBIDDEN_IMPORTS below for the exhaustive list)
  - Does NOT produce typed-evidence or recovery-flow-classification output
  - Uses only Python stdlib + runtime-visible inputs + (D4-6.1) subprocess ripgrep

Inputs (all runtime-visible, no oracle):
  - attempt_1/runtime_signals.json
  - attempt_1/patch.diff
  - attempt_1/local_test_outputs.md
  - optional instance metadata (issue statement) for keyword extraction
  - optional repo_base Path for real ripgrep execution (D4-6.1)

Outputs:
  - broad_candidates.jsonl  : one candidate per line
  - expansion_report.json   : summary (counts, budget, sources_run, rg_executed)

Candidate sources (generic names, NOT ConDiag ops):
  1. EDITED_FILE_WINDOW           — ±N lines around each edited hunk
  2. VIEWED_SPAN_CARRYOVER        — top viewed spans from attempt_1
  3. RG_ISSUE_KEYWORD_SEARCH      — rg query from issue keywords
  4. RG_FAILURE_KEYWORD_SEARCH    — rg query from error_tokens / stack_trace
  5. RG_FAILED_TEST_NAME_SEARCH   — rg query from test_failures

D4-6.1 (real rg): when `repo_base` is provided to expand_context(), each RG_*
query is executed via `subprocess.run(["rg", ...])` against the repo at its
checked-out commit. Hits become concrete-span candidates (path + line range
around each match). When repo_base is None, RG_* queries are recorded but
not executed (the v0 behavior) — this is the "broad_no_repo" packet_mode.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Optional


# ============================================================================
# Budget (must align with ConDiag packet for fair comparison)
# ============================================================================

DEFAULT_BUDGET = {
    "max_files": 8,
    "max_spans": 12,
    "max_lines": 400,
    "max_lines_per_file": 120,
    "max_results_per_query": 5,
    "max_queries": 12,
    "edited_window_lines": 40,       # ±40 lines around each edited hunk
    "viewed_span_top_k": 5,          # top-K viewed spans carried over
}


# ============================================================================
# Source names (generic, NOT ConDiag retrieval op names)
# ============================================================================

SOURCE_EDITED_FILE_WINDOW = "EDITED_FILE_WINDOW"
SOURCE_VIEWED_SPAN_CARRYOVER = "VIEWED_SPAN_CARRYOVER"
SOURCE_RG_ISSUE_KEYWORD = "RG_ISSUE_KEYWORD_SEARCH"
SOURCE_RG_FAILURE_KEYWORD = "RG_FAILURE_KEYWORD_SEARCH"
SOURCE_RG_FAILED_TEST_NAME = "RG_FAILED_TEST_NAME_SEARCH"

ALL_SOURCES = [
    SOURCE_EDITED_FILE_WINDOW,
    SOURCE_VIEWED_SPAN_CARRYOVER,
    SOURCE_RG_ISSUE_KEYWORD,
    SOURCE_RG_FAILURE_KEYWORD,
    SOURCE_RG_FAILED_TEST_NAME,
]


# ============================================================================
# Forbidden tokens (self-audit guard)
# ============================================================================

# These tokens MUST NOT appear in this file's source. The acceptance test
# asserts this. If you find yourself reaching for them, you are probably
# about to leak ConDiag capability into the baseline.
FORBIDDEN_IMPORTS = [
    "from condiag.retrieval_executor",
    "from condiag.evidence_selector",
    "from condiag.manual_retrieval",
    "import condiag.retrieval_executor",
    "import condiag.evidence_selector",
    "import condiag.manual_retrieval",
]

FORBIDDEN_TOKENS = [
    "selected_evidence",
    "5R",
    "RECONCILE", "RESTRAIN", "REHYDRATE", "RETRIEVE", "RELOCALIZE",
    "recovery_intent",
    "context_evidence_type",
    "runtime_gap_diagnosis",
]


# ============================================================================
# Public API
# ============================================================================

def expand_context(
    attempt_1_dir: Path,
    instance_id: str,
    budget: Optional[dict] = None,
    instance_metadata: Optional[dict] = None,
    repo_base: Optional[Path] = None,
) -> dict:
    """Collect generic expansion candidates from attempt_1 artifacts.

    Returns a dict with:
        candidates        : list[dict]   one per candidate (see schema below)
        expansion_report  : dict         summary (sources_run, counts, budget)

    Candidate schema (broad_candidates.jsonl one per line):
        {
            "id": "B1",
            "source": "EDITED_FILE_WINDOW",
            "path": "django/foo.py",
            "start_line": 100,
            "end_line": 160,
            "query": null | "some query string",
            "reason": "human-readable one-liner"
        }

    D4-6.1: if `repo_base` is a directory containing a checked-out git
    repo at base_commit, each RG_* query is executed via `rg` subprocess
    and hits become concrete-span candidates. Otherwise RG_* queries are
    recorded but not executed (broad_no_repo mode).
    """
    budget = {**DEFAULT_BUDGET, **(budget or {})}
    attempt_1_dir = Path(attempt_1_dir)

    rs_path = attempt_1_dir / "runtime_signals.json"
    if not rs_path.is_file():
        return _empty_result(instance_id, budget, reason="missing runtime_signals.json")

    rs = json.loads(rs_path.read_text(encoding="utf-8"))
    instance_metadata = instance_metadata or {}

    # D4-6.1: real ripgrep execution only when repo_base is a usable repo.
    # Accept both regular clone (.git dir) and worktree (.git file pointing elsewhere).
    rb = Path(repo_base) if repo_base else None
    git_marker = rb / ".git" if rb else None
    rg_available = bool(
        rb and rb.is_dir()
        and git_marker is not None
        and (git_marker.is_dir() or git_marker.is_file())
    )

    candidates: list[dict] = []
    sources_run: list[str] = []
    counters = {s: 0 for s in ALL_SOURCES}
    rg_queries_total = 0
    rg_hits_total = 0

    # 1. EDITED_FILE_WINDOW — concrete spans
    edited_candidates = _collect_edited_file_windows(rs, budget)
    if edited_candidates:
        sources_run.append(SOURCE_EDITED_FILE_WINDOW)
        counters[SOURCE_EDITED_FILE_WINDOW] = len(edited_candidates)
        candidates.extend(edited_candidates)

    # 2. VIEWED_SPAN_CARRYOVER — concrete spans
    viewed_candidates = _collect_viewed_span_carryover(rs, budget)
    if viewed_candidates:
        sources_run.append(SOURCE_VIEWED_SPAN_CARRYOVER)
        counters[SOURCE_VIEWED_SPAN_CARRYOVER] = len(viewed_candidates)
        candidates.extend(viewed_candidates)

    # 3-5. RG_* — execute via subprocess rg when repo_base available
    issue_queries = _extract_issue_keyword_queries(instance_metadata, rs, budget)
    issue_hits = _run_rg_source(
        SOURCE_RG_ISSUE_KEYWORD, issue_queries, repo_base, budget,
        reason_prefix="rg hit for issue keyword",
        candidates_out=candidates,
    ) if rg_available else []
    if issue_queries:
        sources_run.append(SOURCE_RG_ISSUE_KEYWORD)
        counters[SOURCE_RG_ISSUE_KEYWORD] = (
            len(issue_hits) if rg_available else len(issue_queries)
        )
        rg_queries_total += len(issue_queries)
        rg_hits_total += len(issue_hits) if rg_available else 0
        if not rg_available:
            for q in issue_queries:
                candidates.append(_make_query_candidate(
                    SOURCE_RG_ISSUE_KEYWORD, q,
                    reason=f"rg query from issue keywords: {q!r}",
                ))

    failure_queries = _extract_failure_keyword_queries(rs, budget)
    failure_hits = _run_rg_source(
        SOURCE_RG_FAILURE_KEYWORD, failure_queries, repo_base, budget,
        reason_prefix="rg hit for failure keyword",
        candidates_out=candidates,
    ) if rg_available else []
    if failure_queries:
        sources_run.append(SOURCE_RG_FAILURE_KEYWORD)
        counters[SOURCE_RG_FAILURE_KEYWORD] = (
            len(failure_hits) if rg_available else len(failure_queries)
        )
        rg_queries_total += len(failure_queries)
        rg_hits_total += len(failure_hits) if rg_available else 0
        if not rg_available:
            for q in failure_queries:
                candidates.append(_make_query_candidate(
                    SOURCE_RG_FAILURE_KEYWORD, q,
                    reason=f"rg query from error token / stack trace: {q!r}",
                ))

    test_name_queries = _extract_failed_test_name_queries(rs, budget)
    test_name_hits = _run_rg_source(
        SOURCE_RG_FAILED_TEST_NAME, test_name_queries, repo_base, budget,
        reason_prefix="rg hit for failed test name",
        candidates_out=candidates,
    ) if rg_available else []
    if test_name_queries:
        sources_run.append(SOURCE_RG_FAILED_TEST_NAME)
        counters[SOURCE_RG_FAILED_TEST_NAME] = (
            len(test_name_hits) if rg_available else len(test_name_queries)
        )
        rg_queries_total += len(test_name_queries)
        rg_hits_total += len(test_name_hits) if rg_available else 0
        if not rg_available:
            for q in test_name_queries:
                candidates.append(_make_query_candidate(
                    SOURCE_RG_FAILED_TEST_NAME, q,
                    reason=f"rg query for failed test name: {q!r}",
                ))

    # Budget enforcement: trim to max_spans / max_files
    candidates = _enforce_budget(candidates, budget)

    # Assign sequential IDs after budget trim
    for i, c in enumerate(candidates, 1):
        c["id"] = f"B{i}"

    report = {
        "schema_version": "condiag.expansion_report.v0",
        "instance_id": instance_id,
        "baseline": "broad_expansion",
        "mode": "packet_only",
        "budget": budget,
        "sources_run": sources_run,
        "candidates_count": len(candidates),
        "by_source": {s: counters[s] for s in ALL_SOURCES if counters[s] > 0},
        "rg_executed": bool(rg_available and rg_queries_total > 0),
        "rg_available": rg_available,
        "rg_queries_total": rg_queries_total,
        "rg_hits_total": rg_hits_total,
        "rg_note": (
            "D4-6.1: RG_*_SEARCH queries executed via subprocess ripgrep "
            f"against repo_base@commit (rg_available={rg_available})."
        ) if rg_available else (
            "RG_*_SEARCH queries recorded but NOT executed "
            "(broad_no_repo: no repo_base available)."
        ),
        "has_concrete_spans": any(
            c.get("start_line") is not None and c.get("path")
            for c in candidates
        ),
    }

    return {
        "candidates": candidates,
        "expansion_report": report,
    }


def write_candidates_jsonl(candidates: list[dict], path: Path) -> None:
    """Write candidates to broad_candidates.jsonl (one JSON per line)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for c in candidates:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


# ============================================================================
# Source collectors
# ============================================================================

def _collect_edited_file_windows(rs: dict, budget: dict) -> list[dict]:
    """EDITED_FILE_WINDOW: ±N lines around each edited hunk.

    Uses runtime_signals.edited_spans_per_file (dict file->[line_numbers]).
    Returns concrete span candidates with path + line range.
    """
    window = int(budget.get("edited_window_lines", 40))
    max_per_file = int(budget.get("max_results_per_query", 5))

    edited = rs.get("edited_spans_per_file") or {}
    if not isinstance(edited, dict):
        return []

    out: list[dict] = []
    for file_path, lines in edited.items():
        if not isinstance(lines, list) or not lines:
            continue
        # Cluster edited lines: merge any within window distance
        sorted_lines = sorted({int(l) for l in lines if isinstance(l, (int, float))})
        if not sorted_lines:
            continue
        clusters = _cluster_lines(sorted_lines, window)
        # Take first max_per_file clusters
        for cl in clusters[:max_per_file]:
            start = max(1, cl[0] - window)
            end = cl[-1] + window
            out.append({
                "source": SOURCE_EDITED_FILE_WINDOW,
                "path": file_path,
                "start_line": start,
                "end_line": end,
                "query": None,
                "reason": f"window ±{window} around edited line(s) {cl}",
            })
    return out


def _collect_viewed_span_carryover(rs: dict, budget: dict) -> list[dict]:
    """VIEWED_SPAN_CARRYOVER: top-K viewed spans.

    Uses runtime_signals.viewed_spans (dict file->[[start,end],...]).
    Picks top-K files by total viewed lines, takes the widest span per file.
    """
    top_k = int(budget.get("viewed_span_top_k", 5))

    viewed = rs.get("viewed_spans") or {}
    if not isinstance(viewed, dict):
        return []

    # rank files by total line coverage
    file_scores = []
    for file_path, spans in viewed.items():
        if not isinstance(spans, list) or not spans:
            continue
        total = sum(
            (int(s[1]) - int(s[0])) for s in spans
            if isinstance(s, (list, tuple)) and len(s) == 2
        )
        file_scores.append((file_path, total, spans))
    file_scores.sort(key=lambda x: -x[1])

    out: list[dict] = []
    for file_path, total, spans in file_scores[:top_k]:
        # pick the widest span in this file
        valid_spans = [
            (int(s[0]), int(s[1])) for s in spans
            if isinstance(s, (list, tuple)) and len(s) == 2
        ]
        if not valid_spans:
            continue
        widest = max(valid_spans, key=lambda x: x[1] - x[0])
        out.append({
            "source": SOURCE_VIEWED_SPAN_CARRYOVER,
            "path": file_path,
            "start_line": widest[0],
            "end_line": widest[1],
            "query": None,
            "reason": f"widest viewed span in {file_path} (file total viewed ~{total} lines)",
        })
    return out


def _extract_issue_keyword_queries(
    instance_metadata: dict, rs: dict, budget: dict,
) -> list[str]:
    """RG_ISSUE_KEYWORD_SEARCH: keywords from issue statement.

    v0: extract camelCase / snake_case identifiers from instance_metadata.get('issue').
    """
    max_queries = int(budget.get("max_queries", 12))
    issue = (instance_metadata.get("issue") or "").strip()
    if not issue:
        return []
    # extract identifiers (snake_case or CamelCase, len >= 4)
    raw = re.findall(r"\b[A-Za-z_][A-Za-z0-9_]{3,}\b", issue)
    # filter stopwords
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "have", "should",
        "when", "while", "into", "them", "then", "what", "where", "which",
        "Django", "issue", "patch", "test", "tests",
    }
    seen = set()
    queries = []
    for tok in raw:
        t = tok.strip("_")
        if t in stop or t.lower() in stop:
            continue
        if len(t) < 4:
            continue
        if t in seen:
            continue
        seen.add(t)
        queries.append(t)
        if len(queries) >= max_queries:
            break
    return queries


def _extract_failure_keyword_queries(rs: dict, budget: dict) -> list[str]:
    """RG_FAILURE_KEYWORD_SEARCH: keywords from error_tokens / stack_trace."""
    max_queries = int(budget.get("max_queries", 12))
    queries: list[str] = []

    # error_tokens first (already curated)
    for tok in (rs.get("error_tokens") or []):
        if isinstance(tok, str) and len(tok) >= 3:
            queries.append(tok.strip())
            if len(queries) >= max_queries:
                return queries

    # stack_trace (extract identifiers)
    st = rs.get("stack_trace") or ""
    if isinstance(st, list):
        st = "\n".join(str(s) for s in st)
    if isinstance(st, str) and st:
        # capture Error/Exception class names + quoted identifiers
        for m in re.findall(r"\b([A-Z][a-zA-Z]+(?:Error|Exception|Warning))\b", st):
            if m not in queries:
                queries.append(m)
                if len(queries) >= max_queries:
                    return queries
        # capture errno-like codes E012 / models.X015
        for m in re.findall(r"\b([a-z_]+\.[A-Z][0-9]{3,})\b", st):
            if m not in queries:
                queries.append(m)
                if len(queries) >= max_queries:
                    return queries

    return queries


def _extract_failed_test_name_queries(rs: dict, budget: dict) -> list[str]:
    """RG_FAILED_TEST_NAME_SEARCH: test names from test_failures."""
    max_queries = int(budget.get("max_queries", 12))
    out: list[str] = []
    for entry in (rs.get("test_failures") or []):
        if isinstance(entry, str):
            name = entry.strip()
        elif isinstance(entry, dict):
            name = (entry.get("test") or entry.get("name") or "").strip()
        else:
            name = ""
        if not name or len(name) < 3:
            continue
        if name not in out:
            out.append(name)
            if len(out) >= max_queries:
                break
    return out


# ============================================================================
# Helpers
# ============================================================================

def _cluster_lines(lines: list[int], window: int) -> list[list[int]]:
    """Group lines so consecutive ones within `window` distance cluster together."""
    if not lines:
        return []
    clusters = [[lines[0]]]
    for ln in lines[1:]:
        if ln - clusters[-1][-1] <= window:
            clusters[-1].append(ln)
        else:
            clusters.append([ln])
    return clusters


def _make_query_candidate(source: str, query: str, reason: str) -> dict:
    return {
        "source": source,
        "path": None,
        "start_line": None,
        "end_line": None,
        "query": query,
        "reason": reason,
    }


# ============================================================================
# Real ripgrep execution (D4-6.1)
# ============================================================================

def _run_rg_source(
    source: str,
    queries: list[str],
    repo_base: Optional[Path],
    budget: dict,
    reason_prefix: str,
    candidates_out: list[dict],
) -> list[dict]:
    """Execute `rg` for each query and append hits to candidates_out.

    Returns the list of hit candidates created (also appended in-place).
    Each hit becomes a concrete-span candidate (path + line range around
    the match). Uses `rg --json` for machine-readable output.

    Budget: max_results_per_query caps hits per query; the surrounding
    span is ±edited_window_lines/2 around each match line.
    """
    if not queries or not repo_base:
        return []
    repo_base = Path(repo_base)
    if not repo_base.is_dir():
        return []

    max_per_query = int(budget.get("max_results_per_query", 5))
    window = max(1, int(budget.get("edited_window_lines", 40)) // 2)

    created: list[dict] = []
    for q in queries:
        hits = _execute_rg(q, repo_base, max_hits=max_per_query)
        for hit in hits:
            cand = {
                "source": source,
                "path": hit["path"],
                "start_line": max(1, hit["line"] - window),
                "end_line": hit["line"] + window,
                "query": q,
                "reason": f"{reason_prefix} {q!r} @ {hit['path']}:{hit['line']}",
            }
            created.append(cand)
            candidates_out.append(cand)
    return created


def _execute_rg(query: str, repo_base: Path, max_hits: int = 5) -> list[dict]:
    """Run `rg --json <query> <repo_base>`, parse matches.

    Returns [{"path": relpath, "line": int}, ...] (deduped by path+line).
    Falls back to plain `rg --line-number` if `--json` is unavailable.

    Excludes test files (*.py under tests/) to avoid broad baseline
    pulling only test scaffolding. Excludes .git.
    """
    if not query or len(query) < 2:
        return []
    try:
        proc = subprocess.run(
            [
                "rg", "--json",
                "--max-count", str(max_hits),
                "-g", "!.git",
                "-g", "!**/tests/**",
                "-g", "!**/test_*.py",
                "-g", "!**/*_test.py",
                "--", query, str(repo_base),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except FileNotFoundError:
        return []
    except subprocess.TimeoutExpired:
        return []

    hits: list[dict] = []
    seen: set = set()
    if proc.returncode not in (0, 1):  # rg returns 1 on no match, 0 on match
        return []
    for line in (proc.stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "match":
            continue
        data = obj.get("data") or {}
        path = data.get("path", {}).get("text") or data.get("path", "")
        line_no = data.get("line_number")
        if not path or not isinstance(line_no, int):
            continue
        # repo_base-prefix-stripped relpath
        try:
            rel = str(Path(path).relative_to(repo_base)).replace("\\", "/")
        except ValueError:
            rel = path
        key = (rel, line_no)
        if key in seen:
            continue
        seen.add(key)
        hits.append({"path": rel, "line": line_no})
        if len(hits) >= max_hits:
            break
    return hits


def _enforce_budget(candidates: list[dict], budget: dict) -> list[dict]:
    """Trim candidates to satisfy max_spans / max_files / max_lines."""
    max_spans = int(budget.get("max_spans", 12))
    max_files = int(budget.get("max_files", 8))
    max_lines = int(budget.get("max_lines", 400))
    max_per_file = int(budget.get("max_lines_per_file", 120))

    # Source priority: concrete spans first, then query-only candidates.
    # Within concrete, prefer EDITED_FILE_WINDOW then VIEWED_SPAN_CARRYOVER.
    priority = {
        SOURCE_EDITED_FILE_WINDOW: 0,
        SOURCE_VIEWED_SPAN_CARRYOVER: 1,
        SOURCE_RG_FAILURE_KEYWORD: 2,
        SOURCE_RG_FAILED_TEST_NAME: 3,
        SOURCE_RG_ISSUE_KEYWORD: 4,
    }
    candidates.sort(key=lambda c: priority.get(c.get("source", ""), 99))

    # Cap per-file line span
    trimmed = []
    for c in candidates:
        if c.get("start_line") is not None and c.get("end_line") is not None:
            span = int(c["end_line"]) - int(c["start_line"])
            if span > max_per_file:
                c["end_line"] = int(c["start_line"]) + max_per_file
                c["reason"] = (c.get("reason") or "") + f" (capped at {max_per_file} lines)"
        trimmed.append(c)
    candidates = trimmed

    # Track totals; drop entries that overflow budget
    kept: list[dict] = []
    seen_files: set[str] = set()
    total_lines = 0
    concrete_kept = 0
    for c in candidates:
        is_concrete = c.get("start_line") is not None and c.get("path")
        if is_concrete:
            if concrete_kept >= max_spans:
                continue
            if c["path"] not in seen_files:
                if len(seen_files) >= max_files:
                    continue
                seen_files.add(c["path"])
            lines = int(c["end_line"]) - int(c["start_line"])
            if total_lines + lines > max_lines:
                continue
            total_lines += lines
            concrete_kept += 1
        kept.append(c)

    return kept


def _empty_result(instance_id: str, budget: dict, reason: str) -> dict:
    return {
        "candidates": [],
        "expansion_report": {
            "schema_version": "condiag.expansion_report.v0",
            "instance_id": instance_id,
            "baseline": "broad_expansion",
            "mode": "packet_only",
            "budget": budget,
            "sources_run": [],
            "candidates_count": 0,
            "by_source": {},
            "rg_executed": False,
            "rg_note": reason,
            "has_concrete_spans": False,
        },
    }


# ============================================================================
# Context packet builder
# ============================================================================

def build_broad_packet(
    instance_id: str,
    trigger_result,
    candidates: list[dict],
    expansion_report: dict,
    patch_summary: dict,
    test_feedback: dict,
) -> str:
    """Build the Broad Expansion context packet.

    Note: this is a GENERIC packet — no recovery-flow-classification terms.
    The user spec template:
        - Previous Attempt Summary
        - Runtime Feedback
        - Expanded Context (grouped by source)
        - Retry Instruction (with explicit "do not assume sufficiency")
    """
    trigger_lines = (
        "\n".join(f"- {r}" for r in trigger_result.trigger_reason)
        or "- (no specific reason recorded)"
    )

    # group candidates by source for display
    by_source: dict[str, list[dict]] = {}
    for c in candidates:
        by_source.setdefault(c.get("source", "?"), []).append(c)

    expanded_sections = []
    for source in ALL_SOURCES:
        if source not in by_source:
            continue
        items = by_source[source]
        if source in (SOURCE_EDITED_FILE_WINDOW, SOURCE_VIEWED_SPAN_CARRYOVER):
            bullets = []
            for c in items:
                bullets.append(
                    f"- `{c.get('path')}` lines {c.get('start_line')}-{c.get('end_line')} "
                    f"— {c.get('reason', '')}"
                )
            section = f"### {source} ({len(items)} spans)\n" + "\n".join(bullets)
        else:
            # query-only source
            qlines = [f"- `{c.get('query')}`" for c in items]
            section = (
                f"### {source} ({len(items)} queries, NOT executed in v0)\n"
                + "\n".join(qlines)
            )
        expanded_sections.append(section)

    expanded_block = (
        "\n\n".join(expanded_sections)
        if expanded_sections
        else "- (no expansion candidates collected)"
    )

    patch_section = (
        f"- edited files: {patch_summary.get('files_count', 0)} "
        f"({', '.join(patch_summary.get('files', [])[:5]) or 'none'})"
        if patch_summary.get("has_patch")
        else "- no patch produced"
    )

    test_section = (
        f"```\n{test_feedback['excerpt']}\n```"
        if test_feedback.get("has_output")
        else "- (no local test output recorded)"
    )

    return f"""# Broad Expansion Context Packet

instance: `{instance_id}`
baseline: broad_expansion (packet_only)
trigger: {trigger_result.trigger_type} (confidence: {trigger_result.confidence})

## Previous Attempt Summary

The previous attempt produced a patch but may benefit from additional
repository context. The trigger reasons recorded by retry_trigger are:

{trigger_lines}

## Runtime Feedback

The following is the local test output seen by the agent during attempt_1.

{test_section}

## Expanded Context

The following spans / queries were collected through **generic lexical and
neighborhood expansion**. They are NOT filtered by any type-classification
or recovery-intent logic.

{expanded_block}

## Previous Patch Summary

{patch_section}
- added lines: {patch_summary.get('added_lines', 0)}
- removed lines: {patch_summary.get('removed_lines', 0)}

## Retry Instruction

Revise the patch using the runtime feedback and expanded context above.

Constraints:
- These spans were collected by generic lexical expansion; do NOT assume
  they are sufficient or all relevant.
- If a span is unhelpful, ignore it; if a critical region is missing, say so.
- Budget was capped at {expansion_report['budget']['max_files']} files /
  {expansion_report['budget']['max_spans']} spans /
  {expansion_report['budget']['max_lines']} lines for fairness with other baselines.

(This packet contains no typed evidence, no recovery-intent labels, and no
recovery-flow classification. It is a strict generic-expansion baseline.)
"""
