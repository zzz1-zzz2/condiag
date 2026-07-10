"""Artifact validator for baseline runs (D4-3 internal hook).

Per artifact_schema.md: each baseline run must produce a fixed set of files
under runs/pilot50/<agent>/<baseline>/<instance>/. This module:

  1. Checks required artifacts exist
  2. Checks no gold leakage in raw_trajectory.json / context_packet.md / etc
  3. Returns a status dict that baseline_runner writes into run_report.json

In dry-run mode, validator returns "skipped_dry_run" (handlers are stubs).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


# Per-baseline required artifacts (only checked when mode != dry-run)
REQUIRED_ARTIFACTS: dict[str, list[str]] = {
    "base_miniswe": [
        "attempt_1/raw_trajectory.json",
        "attempt_1/patch.diff",
        "attempt_1/runtime_signals.json",
        "attempt_1/final_patch_context.json",
        "attempt_1/attempt_report.json",
        "final/patch.diff",
        "final/final_report.json",
        "cost.json",
        "run_report.json",
        # NOTE: attempt_1/contextbench_metrics.json is produced by the
        # ContextBench eval stage, NOT by the handler. It is therefore NOT
        # in the handler-required list. A separate validator mode will
        # check ContextBench outputs after eval runs.
    ],
    "feedback_retry": [
        "attempt_1/raw_trajectory.json",
        "attempt_1/patch.diff",
        "attempt_1/runtime_signals.json",
        "intervention/intervention_report.json",
        "intervention/retry_trigger_result.json",
        # context_packet.md: required only when intervention_report.should_retry=True
        # attempt_2/*: required only in full retry mode (D4-5b), not packet_only
        "final/patch.diff",
        "final/final_report.json",
        "cost.json",
        "run_report.json",
    ],
    "broad_expansion": [
        "attempt_1/raw_trajectory.json",
        "attempt_1/patch.diff",
        "attempt_1/runtime_signals.json",
        "intervention/intervention_report.json",
        "intervention/retry_trigger_result.json",
        "intervention/broad_candidates.jsonl",
        "intervention/expansion_report.json",
        # context_packet.md: required only when should_retry=True (dynamic)
        # attempt_2/*: required only in full retry mode (D4-6b+), not packet_only
        "final/patch.diff",
        "final/final_report.json",
        "cost.json",
        "run_report.json",
    ],
    "condiag_packet_only": [
        "attempt_1/raw_trajectory.json",
        "attempt_1/patch.diff",
        "attempt_1/runtime_signals.json",
        "intervention/intervention_report.json",
        "intervention/retry_trigger_result.json",
        "intervention/recovery_report.json",
        "intervention/selected_evidence.json",
        "intervention/executed_actions.json",
        "intervention/context_packet.md",
        "final/patch.diff",
        "final/final_report.json",
        "cost.json",
        "run_report.json",
    ],
    "condiag_retry": [
        "attempt_1/raw_trajectory.json",
        "intervention/intervention_report.json",
        "intervention/context_packet.md",
        "attempt_2/raw_trajectory.json",
        "attempt_2/patch.diff",
        "final/patch.diff",
        "final/final_report.json",
        "cost.json",
        "run_report.json",
    ],
    "plain_rerun": [
        "attempt_1/raw_trajectory.json",
        "attempt_1/patch.diff",
        "attempt_1/runtime_signals.json",
        "intervention/intervention_report.json",
        "attempt_2/raw_trajectory.json",
        "attempt_2/attempt_report.json",
        # attempt_2/patch.diff and final/patch.diff are conditional:
        # required when retry_no_change=false, optional when true
        "final/final_report.json",
        "run_report.json",
    ],
}


# Files that, if their content mentions gold/eval terms, count as leakage
LEAKAGE_SCAN_FILES = [
    "attempt_1/raw_trajectory.json",
    "attempt_2/raw_trajectory.json",
    "intervention/context_packet.md",
    "intervention/intervention_report.json",
    "intervention/selected_evidence.json",
    "intervention/expansion_report.json",
    "intervention/recovery_report.json",
    "intervention/executed_actions.json",
]

LEAKAGE_KEYWORDS = [
    "gold_check",
    "contextbench_metrics",
    "fail_to_pass",
    "pass_to_pass",
    "official_eval",
    "gold_patch",
    "gold_context",
]


def validate_run(
    run_dir: Path,
    baseline: str,
    agent: str,
    mode: str = "dry-run",
) -> dict:
    """Validate a single baseline run directory.

    Returns:
        {
            "status": "ok" | "missing_artifacts" | "gold_leakage" | "skipped_dry_run",
            "missing": [...],
            "leakage_hits": [...],
            "checked_count": int,
        }
    """
    run_dir = Path(run_dir)

    if mode == "dry-run":
        return {
            "status": "skipped_dry_run",
            "missing": [],
            "leakage_hits": [],
            "checked_count": 0,
            "reason": "handlers are stubs in dry-run; artifact check deferred",
        }

    # 1. required artifacts (with intervention_report-aware extensions)
    required = list(REQUIRED_ARTIFACTS.get(baseline, []))

    # Dynamic extension: if intervention/intervention_report.json exists,
    # add conditional requirements based on should_retry / mode.
    ireport_path = run_dir / "intervention" / "intervention_report.json"
    if ireport_path.is_file():
        try:
            ireport = json.loads(ireport_path.read_text(encoding="utf-8"))
            should_retry = bool(ireport.get("should_retry"))
            ireport_mode = ireport.get("mode", "")
            # context_packet.md required iff should_retry=True
            if should_retry:
                if "intervention/context_packet.md" not in required:
                    required.append("intervention/context_packet.md")
            # attempt_2/* required iff retry mode (not packet_only)
            if ireport_mode and ireport_mode != "packet_only":
                for a in ("attempt_2/raw_trajectory.json", "attempt_2/patch.diff"):
                    if a not in required:
                        required.append(a)
        except Exception:
            pass  # malformed intervention_report: just skip dynamic extension

    missing = [r for r in required if not (run_dir / r).is_file()]

    # Conditional patch rule: if retry_no_change=true, patch.diff is optional
    attempt_report_path = run_dir / "attempt_2" / "attempt_report.json"
    if attempt_report_path.is_file():
        try:
            ar = json.loads(attempt_report_path.read_text(encoding="utf-8"))
            retry_no_change = bool(ar.get("retry_no_change", False))
        except Exception:
            retry_no_change = False
        if retry_no_change:
            for patch_rel in ("attempt_2/patch.diff", "final/patch.diff"):
                if patch_rel in missing:
                    missing.remove(patch_rel)
            # Also remove empty-string-if-patch-missing entries from missing
            # so that an intentionally absent diff doesn't fail validation
            for patch_rel in ("attempt_2/patch.diff", "final/patch.diff"):
                p = run_dir / patch_rel
                if p.is_file() and p.stat().st_size == 0:
                    # empty patch is acceptable when retry_no_change
                    pass

    # 2. gold leakage scan
    leakage_hits: list[dict] = []
    for rel in LEAKAGE_SCAN_FILES:
        f = run_dir / rel
        if not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for kw in LEAKAGE_KEYWORDS:
            # word-boundary-ish check to avoid matching substrings of legit fields
            pattern = r"(?<![A-Za-z_])" + re.escape(kw) + r"(?![A-Za-z_])"
            if re.search(pattern, text):
                leakage_hits.append({"file": rel, "keyword": kw})

    if leakage_hits:
        status = "gold_leakage"
    elif missing:
        status = "missing_artifacts"
    else:
        status = "ok"

    return {
        "status": status,
        "missing": missing,
        "leakage_hits": leakage_hits,
        "checked_count": len(required),
    }


def assert_no_leakage_in_text(text: str, source_label: str = "<text>") -> None:
    """Standalone leak guard for arbitrary text inputs (used by tests)."""
    for kw in LEAKAGE_KEYWORDS:
        pattern = r"(?<![A-Za-z_])" + re.escape(kw) + r"(?![A-Za-z_])"
        if re.search(pattern, text):
            raise ValueError(
                f"leakage guard: forbidden keyword '{kw}' found in {source_label}"
            )
