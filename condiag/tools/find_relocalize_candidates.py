"""RELOCALIZE candidate miner.

Purpose: scan case_bundles/*/runtime_signals.json + patch.diff + local_test_outputs.md
to surface cases that look like RELOCALIZE pathology — i.e. the agent's patch failed
local validation with a concrete error (exception name / warning code / stack trace
top file), but the agent never searched for that error token and never edited the
error-origin file.

This miner is OFFLINE-ONLY analysis. It does NOT feed runtime triggers and does
NOT touch gold/oracle metrics for selection; oracle_support_score is reported
purely for human inspection alongside the runtime score.

Inputs (per case bundle):
    runtime_signals.json
    patch.diff
    local_test_outputs.md
    contextbench_metrics.json   (optional, for oracle_support_score)

Outputs:
    relocalize_candidates.csv              (one row per case)
    top10_relocalize_candidates.md         (ranked, with reason)

Scoring rubric (see project_condiag_four_flows RELOCALIZE design notes):
    +2  local validation has failure log / stack trace / warning / error code
    +2  visible error tokens (exception name, warning name, error code, or
        key error message) extractable from local_test_outputs.md
    +2  search_commands did NOT grep for those error tokens
    +1  edited_files does NOT include stack-trace top file or error-origin candidate
    +1  patch file count small (<=3) yet local validation still failed
    +1  search queries lean on issue-surface keywords rather than failure-origin tokens
    +1  final PATCH_CONTEXT_FILES does NOT include error-origin candidate
    -2  patch_shape_anomaly strong (over-edit / over-explore → RESTRAIN, not RELOCALIZE)
    -1  visible regression failures many (>=3) → RECONCILE, not RELOCALIZE

Threshold:
    runtime_relocalize_score >= 5  → strong candidate
    3 <= score <= 4                → weak candidate
    score < 3                      → unlikely RELOCALIZE

Usage:
    python -m condiag.tools.find_relocalize_candidates \
        --root /mnt/d/condiag-artifacts/condiag/v0 \
        --out  /mnt/d/condiag-artifacts/condiag/v0
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# Error token extraction patterns
# ---------------------------------------------------------------------------

# Django / system check codes: E001, W0123, etc.
DJANGO_CHECK_CODE = re.compile(r"\b([EW])(\d{3,4})\b")
# Python exceptions / warnings / errors
PY_EXC = re.compile(r"\b([A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning))\b")
# pytest FAILED line: FAILED path/to/test.py::test_name
PYTEST_FAILED = re.compile(r"FAILED\s+(\S+?)::(\S+)")
# Stack-trace file:line
STACK_FILE = re.compile(r'File "([^"]+)", line (\d+)')
# AssertionError / assert messages often appear bare — keep this distinct from PY_EXC
ASSERT_MSG = re.compile(r"AssertionError\b")

# Tokens too generic to be useful as "error-origin" anchors
GENERIC_TOKENS = {
    "Error",
    "Exception",
    "Warning",
    "DeprecationWarning",
    "UserWarning",
    "RuntimeWarning",
    "FutureWarning",
    "SyntaxWarning",
    "PendingDeprecationWarning",
    "ResourceWarning",
}

# Bare builtin exception types — too generic to point at an origin file. We
# still record them (for the "visible error token" axis) but they don't count
# as a strong RELOCALIZE anchor on their own.
BUILTIN_EXCEPTIONS = {
    "TypeError",
    "ValueError",
    "KeyError",
    "AttributeError",
    "IndexError",
    "NameError",
    "ImportError",
    "ModuleNotFoundError",
    "FileNotFoundError",
    "RuntimeError",
    "StopIteration",
    "NotImplementedError",
    "ZeroDivisionError",
    "OverflowError",
    "AssertionError",
    "UnboundLocalError",
}


@dataclass
class ErrorTokens:
    """Extracted error signal from local_test_outputs.md."""

    exception_names: list[str] = field(default_factory=list)
    warning_codes: list[str] = field(default_factory=list)
    django_check_codes: list[str] = field(default_factory=list)
    failed_tests: list[str] = field(default_factory=list)
    stack_files: list[str] = field(default_factory=list)  # ALL files from File "..." lines
    origin_source_files: list[str] = field(default_factory=list)  # stack_files minus tests/_tests/

    def specific_tokens(self) -> list[str]:
        """Tokens specific enough to plausibly point at an error origin:
        custom exception names, warning codes, django check codes. Excludes
        builtin exception types like TypeError and test-framework warnings
        (PytestConfigWarning etc.) which are about the test harness, not the
        project code under test."""
        out: list[str] = []
        out += [
            t for t in self.exception_names
            if t not in GENERIC_TOKENS
            and t not in BUILTIN_EXCEPTIONS
            and not _is_framework_internal(t)
        ]
        out += [t for t in self.warning_codes if not _is_framework_internal(t)]
        out += list(self.django_check_codes)
        return out

    def all_tokens(self) -> list[str]:
        """Flatten into a single list for search-coverage checks. Includes
        builtin exceptions only when nothing more specific is available."""
        out: list[str] = []
        out += self.specific_tokens()
        out += [t for t in self.exception_names if t not in GENERIC_TOKENS]
        return out

    def has_any_signal(self) -> bool:
        return bool(
            self.exception_names
            or self.warning_codes
            or self.django_check_codes
            or self.failed_tests
            or self.stack_files
        )


def extract_error_tokens(test_md: str) -> ErrorTokens:
    """Pull error / exception / warning / failed-test / stack-file signals."""
    et = ErrorTokens()

    for m in PY_EXC.finditer(test_md):
        name = m.group(1)
        if name not in GENERIC_TOKENS and name not in et.exception_names:
            et.exception_names.append(name)

    # Django check codes: 'E001', 'W0123' — store with prefix
    for m in DJANGO_CHECK_CODE.finditer(test_md):
        code = m.group(1) + m.group(2)
        if code not in et.django_check_codes:
            et.django_check_codes.append(code)

    # Python warnings captured by PY_EXC already (Warning suffix); also keep raw
    # warnings.warn category names if present
    for m in re.finditer(r"warnings\.warn\(([A-Za-z_][A-Za-z0-9_]*)", test_md):
        name = m.group(1)
        if (
            name.endswith("Warning")
            and name not in GENERIC_TOKENS
            and name not in et.warning_codes
        ):
            et.warning_codes.append(name)

    for m in PYTEST_FAILED.finditer(test_md):
        node = f"{m.group(1)}::{m.group(2)}"
        if node not in et.failed_tests:
            et.failed_tests.append(node)

    # Stack-trace files: keep basename only for cross-comparison with edited_files
    for m in STACK_FILE.finditer(test_md):
        full = m.group(1)
        base = full.rsplit("/", 1)[-1]
        if base not in et.stack_files:
            et.stack_files.append(base)
        # exclude test files AND generic origin files from "origin source files"
        # — a test file is the symptom site; entry-point / module-init files
        # are on the call stack but are not bug origins in the RELOCALIZE sense
        if (
            not _is_test_file(base)
            and not _is_generic_origin(base)
            and base not in et.origin_source_files
        ):
            et.origin_source_files.append(base)

    return et


def _is_test_file(name: str) -> bool:
    """Heuristic: 'test_*' or '*_test.*' or files under tests/ are symptoms."""
    low = name.lower()
    return (
        low.startswith("test_")
        or low.startswith("conftest")
        or "_test." in low
        or low.startswith("test")
        and "." in low
    )


# Generic / harness-level "origin" files that never point at a real bug origin.
# These show up in stack traces when the entry point or module init is on the
# call path but says nothing about where the bug actually lives.
_GENERIC_ORIGIN_FILES = {
    "__init__.py",
    "__main__.py",
    "conftest.py",
    "runtests.py",
    "setup.py",
    "sitecustomize.py",
    "usercustomize.py",
}


def _is_generic_origin(name: str) -> bool:
    """Filter out entry-point / module-init / harness files. They are on the
    call stack but are not error origins in the RELOCALIZE sense."""
    low = name.lower().strip()
    if low in _GENERIC_ORIGIN_FILES:
        return True
    # '<frozen ...>' / '<string>' CPython interpreter frames
    if low.startswith("<") and low.endswith(">"):
        return True
    return False


_FRAMEWORK_PREFIXES = ("Pytest", "Unraisable", "PytestUnraisable")


def _is_framework_internal(name: str) -> bool:
    """Test-harness-internal or framework-deprecation warnings
    (PytestConfigWarning, RemovedInDjango41Warning, RemovedInSphinx30Warning,
    ...) — these tell us about test runner / framework version drift, not
    that the project code is broken in a way ConDiag should localize."""
    if any(name.startswith(p) for p in _FRAMEWORK_PREFIXES):
        return True
    # Django / Sphinx / SQLAlchemy style deprecation warnings
    if name.startswith("RemovedIn") and name.endswith("Warning"):
        return True
    return False


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _normalize_file_list(val: object) -> list[str]:
    """Accept either list[str] or list[dict(file=..., lines=...)] and return
    repo-relative basenames, dropping template placeholders like
    '<absolute_file_path>'."""
    out: list[str] = []
    if not isinstance(val, list):
        return out
    for item in val:
        if isinstance(item, str):
            path = item
        elif isinstance(item, dict):
            path = item.get("file") or ""
        else:
            continue
        if not path or path.startswith("<"):
            continue
        base = path.lstrip("/").rsplit("/", 1)[-1]
        if base and base not in out:
            out.append(base)
    return out


def _searched_tokens(search_commands: Iterable[str], tokens: Iterable[str]) -> list[str]:
    """Return tokens that appear in any search command (case-insensitive)."""
    tokens = list(tokens)
    if not tokens:
        return []
    haystack = " ".join(search_commands).lower()
    return [t for t in tokens if t.lower() in haystack]


def _issue_surface_keywords(search_commands: Iterable[str]) -> bool:
    """Heuristic: agent searched by issue-surface keywords (e.g. 'Infinity',
    'is_prime', function names from the issue) rather than failure tokens.
    Detects grep/rg calls that reference identifiers but lack failure words."""
    cmds = list(search_commands)
    if not cmds:
        return False
    has_grep = any(("grep" in c.lower() or "rg " in c.lower() or "ag " in c.lower()) for c in cmds)
    if not has_grep:
        return False
    # absence of failure-keyword filters suggests issue-surface orientation
    failure_kw = ("error", "exception", "warning", "traceback", "failed", "fail")
    return not any(any(k in c.lower() for k in failure_kw) for c in cmds)


def _patch_changed_files(patch_diff: str) -> list[str]:
    """Return the list of changed files in a unified diff."""
    out: list[str] = []
    for line in patch_diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                # 'diff --git a/foo b/foo' — take b/foo
                b = parts[-1]
                if b.startswith("b/"):
                    b = b[2:]
                out.append(b)
    return out


def _regression_count(runtime: dict) -> int:
    """Best-effort count of visible regression failures."""
    n = runtime.get("possible_regression_failures_count")
    if isinstance(n, int):
        return n
    pr = runtime.get("possible_regression_failures") or []
    if isinstance(pr, list):
        return len(pr)
    return 0


def _patch_shape_anomaly_strong(runtime: dict) -> bool:
    """Proxy for RESTRAIN-shape: many changed_files + repeated pattern detected."""
    cf = runtime.get("changed_files_count") or 0
    rep = bool(runtime.get("repeated_edit_pattern_detected"))
    return cf >= 8 and rep


# ---------------------------------------------------------------------------
# Main scoring
# ---------------------------------------------------------------------------

@dataclass
class CaseScore:
    instance: str
    agent: str
    trigger_type: str
    edited_files_count: int
    test_runs_count: int
    visible_error_tokens: list[str]
    searched_error_tokens: list[str]
    error_token_search_missing: bool
    edited_files: list[str]
    stack_files_in_patch: list[str]
    runtime_score: int
    score_components: dict[str, int]
    oracle_support_score_optional: float | str
    candidate_reason: str
    candidate_strength: str  # 'strong' / 'weak' / 'none'


def score_case(
    instance: str,
    runtime: dict,
    patch_diff: str,
    test_md: str,
    oracle_metrics: dict | None = None,
) -> CaseScore:
    """Apply the RELOCALIZE scoring rubric to one case."""
    components: dict[str, int] = {}

    agent = runtime.get("agent") or ""
    trigger_type = runtime.get("trigger_type") or ""

    # Extract error tokens
    et = extract_error_tokens(test_md)
    specific_tokens = et.specific_tokens()
    all_visible_tokens = et.all_tokens()
    origin_files = et.origin_source_files  # source files only, tests excluded

    # Test-failure count — used to distinguish real RELOCALIZE signal
    # (test FAILED with concrete origin file) from framework-noise output
    # (deprecation warnings printed during a passing test run).
    test_failures_count = int(runtime.get("test_failures_count") or 0)

    # --- +2: local validation has failure log / stack trace / warning / error code
    # Strong signal: real stack trace into a *user-code* source file AND at
    # least one FAILED test (i.e. validation genuinely failed).
    # Weak signal (+1): just pytest FAILED / generic exception name (symptom only).
    # Zero: only framework deprecation warnings on an otherwise passing run.
    has_origin_stack = bool(origin_files)
    has_any_failure = et.has_any_signal()
    if has_origin_stack and test_failures_count > 0:
        components["has_failure_signal"] = 2
    elif has_any_failure:
        components["has_failure_signal"] = 1
    else:
        components["has_failure_signal"] = 0

    # --- +2: visible error tokens extractable — must be SPECIFIC (custom exception /
    # warning code / django check code) to deserve any points. Generic builtin
    # exception names (TypeError, ValueError, ...) and framework-internal
    # deprecation warnings no longer score, because experience shows they
    # produce constant false positives on Django / Sphinx style codebases.
    if specific_tokens:
        components["has_visible_error_tokens"] = 2
    else:
        components["has_visible_error_tokens"] = 0

    # --- +2: search_commands did NOT grep for those error tokens (only specific ones matter)
    search_cmds = runtime.get("search_commands") or []
    searched = _searched_tokens(search_cmds, specific_tokens)
    missing = bool(specific_tokens) and not searched
    components["error_token_search_missing"] = 2 if missing else 0

    # --- +1: edited_files does NOT include stack-trace origin file
    edited_files = _normalize_file_list(runtime.get("edited_files"))
    if not edited_files:
        edited_files = _patch_changed_files(patch_diff)
    edited_bases = [e.rsplit("/", 1)[-1] for e in edited_files]
    origin_in_patch = [s for s in origin_files if s in edited_bases]
    components["origin_file_not_edited"] = 1 if (origin_files and not origin_in_patch) else 0

    # --- +1: patch file count small (<=3) but local validation still failed
    cf_count = runtime.get("changed_files_count")
    if cf_count is None:
        cf_count = len(_patch_changed_files(patch_diff))
    test_runs = runtime.get("test_runs_count") or 0
    components["small_patch_still_failed"] = (
        1 if (cf_count <= 3 and test_failures_count > 0) else 0
    )

    # --- +1: search queries lean on issue-surface keywords rather than failure tokens
    components["issue_surface_oriented"] = (
        1 if _issue_surface_keywords(search_cmds) and specific_tokens else 0
    )

    # --- +1: final PATCH_CONTEXT_FILES does NOT include origin file
    final_pc_bases = _normalize_file_list(runtime.get("final_patch_context_files"))
    origin_in_final = any(s in final_pc_bases for s in origin_files)
    components["origin_not_in_final_patch"] = (
        1 if (origin_files and not origin_in_final) else 0
    )

    # --- -2: patch_shape_anomaly strong (RESTRAIN-like)
    components["patch_shape_anomaly_strong"] = -2 if _patch_shape_anomaly_strong(runtime) else 0

    # --- -1: visible regression failures many (RECONCILE-like)
    reg_n = _regression_count(runtime)
    components["visible_regression_many"] = -1 if reg_n >= 3 else 0

    # --- -2: REHYDRATE-shape (agent viewed right files but dropped them)
    viewed_but_dropped = int(runtime.get("viewed_but_not_final_files_count") or 0)
    components["rehydrate_shape_viewed_but_dropped"] = -2 if viewed_but_dropped >= 2 else 0

    runtime_score = sum(components.values())

    # Oracle support (OFFLINE ONLY — contextbench metrics, never used for trigger)
    oracle_support: float | str = ""
    if oracle_metrics:
        try:
            file_cov = float(oracle_metrics.get("file_cov", 0) or 0)
            line_cov = float(oracle_metrics.get("line_cov", 0) or 0)
            oracle_support = round((1 - file_cov) * 0.6 + (1 - line_cov) * 0.4, 3)
        except Exception:
            oracle_support = ""

    # Reason text
    bits = []
    if components.get("has_failure_signal") == 2:
        bits.append(f"stack-trace origin in source file ({', '.join(origin_files[:3])})")
    elif components.get("has_failure_signal") == 1:
        bits.append("only generic failure signal (no source-level stack origin)")
    if components.get("error_token_search_missing"):
        bits.append(
            f"agent never searched for visible error tokens "
            f"({len(specific_tokens)} specific tokens)"
        )
    if components.get("origin_file_not_edited"):
        bits.append("stack-trace origin file not edited")
    if components.get("origin_not_in_final_patch"):
        bits.append("origin file absent from final patch context")
    if components.get("rehydrate_shape_viewed_but_dropped"):
        bits.append(f"{viewed_but_dropped} files viewed-but-dropped → REHYDRATE-shape")
    if components.get("patch_shape_anomaly_strong"):
        bits.append("over-edit shape → RESTRAIN-like")
    if components.get("visible_regression_many"):
        bits.append(f"{reg_n} regression candidates → RECONCILE-like")
    if not bits:
        bits.append("no RELOCALIZE signal")
    reason = "; ".join(bits)

    if runtime_score >= 5:
        strength = "strong"
    elif runtime_score >= 3:
        strength = "weak"
    else:
        strength = "none"

    return CaseScore(
        instance=instance,
        agent=agent,
        trigger_type=trigger_type,
        edited_files_count=int(cf_count or 0),
        test_runs_count=int(test_runs or 0),
        visible_error_tokens=specific_tokens,
        searched_error_tokens=searched,
        error_token_search_missing=missing,
        edited_files=edited_files,
        stack_files_in_patch=origin_in_patch,
        runtime_score=runtime_score,
        score_components=components,
        oracle_support_score_optional=oracle_support,
        candidate_reason=reason,
        candidate_strength=strength,
    )


# ---------------------------------------------------------------------------
# IO driver
# ---------------------------------------------------------------------------

def iter_cases(root: Path) -> Iterable[Path]:
    bundles = root / "case_bundles"
    if not bundles.is_dir():
        return
    for p in sorted(bundles.iterdir()):
        if p.is_dir() and (p / "runtime_signals.json").is_file():
            yield p


def run(root: Path, out: Path) -> dict:
    root = Path(root).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    rows: list[CaseScore] = []
    for case_dir in iter_cases(root):
        instance = case_dir.name
        try:
            runtime = json.loads((case_dir / "runtime_signals.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[skip] {instance}: runtime_signals.json unreadable ({e})")
            continue
        patch_diff = ""
        patch_path = case_dir / "patch.diff"
        if patch_path.is_file():
            patch_diff = patch_path.read_text(encoding="utf-8", errors="ignore")
        test_md = ""
        test_md_path = case_dir / "local_test_outputs.md"
        if test_md_path.is_file():
            test_md = test_md_path.read_text(encoding="utf-8", errors="ignore")
        oracle_metrics = None
        om_path = case_dir / "contextbench_metrics.json"
        if om_path.is_file():
            try:
                oracle_metrics = json.loads(om_path.read_text(encoding="utf-8"))
            except Exception:
                oracle_metrics = None

        rows.append(score_case(instance, runtime, patch_diff, test_md, oracle_metrics))

    # CSV
    csv_path = out / "relocalize_candidates.csv"
    fieldnames = [
        "instance",
        "agent",
        "trigger_type",
        "edited_files_count",
        "test_runs_count",
        "visible_error_tokens",
        "searched_error_tokens",
        "error_token_search_missing",
        "edited_files",
        "top_runtime_score",
        "oracle_support_score_optional",
        "candidate_reason",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({
                "instance": r.instance,
                "agent": r.agent,
                "trigger_type": r.trigger_type,
                "edited_files_count": r.edited_files_count,
                "test_runs_count": r.test_runs_count,
                "visible_error_tokens": "; ".join(r.visible_error_tokens),
                "searched_error_tokens": "; ".join(r.searched_error_tokens),
                "error_token_search_missing": r.error_token_search_missing,
                "edited_files": "; ".join(r.edited_files),
                "top_runtime_score": r.runtime_score,
                "oracle_support_score_optional": r.oracle_support_score_optional,
                "candidate_reason": r.candidate_reason,
            })

    # Markdown top-N summary
    md_path = out / "top10_relocalize_candidates.md"
    sorted_rows = sorted(rows, key=lambda r: r.runtime_score, reverse=True)
    top = sorted_rows[:10]
    lines: list[str] = []
    lines.append("# RELOCALIZE candidate ranking (offline analysis)")
    lines.append("")
    lines.append(
        "Generated by `condiag.tools.find_relocalize_candidates`. "
        "Oracle support score is OFFLINE ONLY and must not be used as a "
        "runtime trigger input."
    )
    lines.append("")
    lines.append(
        "Threshold: `runtime_score >= 5` strong · `3–4` weak · `<3` unlikely."
    )
    lines.append("")
    lines.append("| Rank | Instance | Score | Strength | Visible error tokens | Reason |")
    lines.append("|-----:|----------|------:|----------|----------------------|--------|")
    for i, r in enumerate(top, 1):
        tok = ", ".join(r.visible_error_tokens[:6]) or "—"
        reason_short = r.candidate_reason.split(";")[0]
        lines.append(
            f"| {i} | `{r.instance}` | {r.runtime_score} | {r.candidate_strength} | {tok} | {reason_short} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # JSON report (machine-readable, includes full components)
    json_path = out / "relocalize_candidates_report.json"
    report = {
        "schema_version": "condiag.relocalize_candidates.v0",
        "cases_total": len(rows),
        "strong_candidates": sum(1 for r in rows if r.candidate_strength == "strong"),
        "weak_candidates": sum(1 for r in rows if r.candidate_strength == "weak"),
        "rows": [
            {
                "instance": r.instance,
                "agent": r.agent,
                "trigger_type": r.trigger_type,
                "runtime_score": r.runtime_score,
                "candidate_strength": r.candidate_strength,
                "score_components": r.score_components,
                "visible_error_tokens": r.visible_error_tokens,
                "searched_error_tokens": r.searched_error_tokens,
                "error_token_search_missing": r.error_token_search_missing,
                "edited_files": r.edited_files,
                "edited_files_count": r.edited_files_count,
                "test_runs_count": r.test_runs_count,
                "oracle_support_score_optional": r.oracle_support_score_optional,
                "candidate_reason": r.candidate_reason,
            }
            for r in sorted_rows
        ],
        "csv_path": str(csv_path),
        "md_path": str(md_path),
    }
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    return report


def main() -> None:
    ap = argparse.ArgumentParser(description="RELOCALIZE candidate miner")
    ap.add_argument("--root", required=True, help="ConDiag v0 root (contains case_bundles/)")
    ap.add_argument("--out", required=True, help="Output directory")
    args = ap.parse_args()
    report = run(Path(args.root), Path(args.out))
    print(
        f"[relocalize] {report['cases_total']} cases · "
        f"{report['strong_candidates']} strong · "
        f"{report['weak_candidates']} weak"
    )
    print(f"[relocalize] csv : {report['csv_path']}")
    print(f"[relocalize] md  : {report['md_path']}")


if __name__ == "__main__":
    main()
