"""ConDiag Patch Prune Suggester — derives the prune list from EditSupportMap.

Inputs:
  - edit_support_map.json (output of edit_support_checker)
  - patch_scope_report.json (for hunk-level context)

Output:
  - PatchPruneReport with:
      - prune_candidates: files to drop entirely (unsupported + pattern-only)
      - review_candidates: files to revisit (weak)
      - keep_candidates:   files to keep (supported)
      - rationale per file

Does NOT auto-apply pruning. ConDiag never edits the patch directly; the
suggestions go into the context packet for the retrying agent to act on.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List


@dataclass
class PruneSuggestion:
    path: str
    action: str               # "drop" | "review" | "keep"
    current_verdict: str      # source verdict from EditSupportMap
    reason: str
    suggestion: str
    anti_support_signals: List[str] = field(default_factory=list)
    matched_target_hints: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PatchPruneReport:
    instance_id: str
    prune_candidates: List[str] = field(default_factory=list)
    review_candidates: List[str] = field(default_factory=list)
    keep_candidates: List[str] = field(default_factory=list)
    total_edited_files: int = 0
    prune_ratio: float = 0.0
    suggestions: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def suggest(
    instance_id: str,
    support_map: dict,
    patch_scope_report: dict | None = None,
) -> PatchPruneReport:
    """Build a PatchPruneReport from the support map."""
    files = support_map.get("files", []) or []
    suggestions: list[PruneSuggestion] = []

    for f in files:
        path = f.get("path", "")
        verdict = f.get("support", "unsupported")
        score = int(f.get("score", 0))
        sources = f.get("support_sources", []) or []
        anti = f.get("anti_support_signals", []) or []
        hints = f.get("matched_target_hints", []) or []
        is_pattern_only = bool(f.get("pattern_only_edits", False))

        if verdict == "supported":
            action = "keep"
            reason = (f"File contains target_hints {hints}; "
                      f"supported by {sources}.")
            suggestion = ("Keep this edit. Anchor the retry patch on this file.")

        elif verdict == "weak":
            action = "review"
            reason = (f"Viewed/searched but no target_hint identifier present; "
                      f"sources={sources}; pattern_only={is_pattern_only}.")
            suggestion = ("Revisit this edit. Drop unless you can tie it to a "
                          "specific failing test or issue-mentioned symbol.")

        else:  # unsupported
            if is_pattern_only:
                action = "drop"
                reason = (f"All edits are mechanical pattern matches "
                          f"({anti[:2]}); no target_hint, no test, no stack-trace "
                          f"support.")
                suggestion = ("Drop this edit. The change is a broad pattern "
                              "sweep with no issue-level evidence.")
            else:
                action = "drop"
                reason = (f"No direct support from issue/test/stack/viewed-span; "
                          f"sources={sources}.")
                suggestion = ("Drop this edit unless new evidence surfaces during "
                              "retry.")

        suggestions.append(PruneSuggestion(
            path=path,
            action=action,
            current_verdict=verdict,
            reason=reason,
            suggestion=suggestion,
            anti_support_signals=anti,
            matched_target_hints=hints,
        ))

    prune = [s.path for s in suggestions if s.action == "drop"]
    review = [s.path for s in suggestions if s.action == "review"]
    keep = [s.path for s in suggestions if s.action == "keep"]
    total = len(files)
    ratio = (len(prune) / total) if total > 0 else 0.0

    return PatchPruneReport(
        instance_id=instance_id,
        prune_candidates=prune,
        review_candidates=review,
        keep_candidates=keep,
        total_edited_files=total,
        prune_ratio=round(ratio, 3),
        suggestions=[s.to_dict() for s in suggestions],
    )


def write_report(out_dir: Path, report: PatchPruneReport) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "patch_prune_report.json"
    p.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return p
