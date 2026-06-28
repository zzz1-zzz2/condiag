"""ConDiag Scope Guard — patch-shape anomaly scorer.

Per design拍板 2 (2026-06-27):
    scope_anomaly_score =
      1[changed_files >= 5]
    + 1[changed_lines >= 200]
    + 1[api_calls >= 80]
    + 1[repeated_edit_pattern detected]
    + 1[many edited files have no support from issue/stack/failed tests/viewed evidence]

    score >= 2 → warning
    score >= 3 → strong over-edit

Does not consult gold; only patch-shape signals visible at runtime.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict

from .schemas import RuntimeSignals


@dataclass
class ScopeGuardResult:
    score: int = 0
    threshold_warning: int = 2
    threshold_strong: int = 3
    signals: Dict[str, bool] = field(default_factory=dict)
    signal_values: Dict[str, object] = field(default_factory=dict)
    triggered_warning: bool = False
    triggered_strong: bool = False

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "threshold_warning": self.threshold_warning,
            "threshold_strong": self.threshold_strong,
            "signals": self.signals,
            "signal_values": self.signal_values,
            "triggered_warning": self.triggered_warning,
            "triggered_strong": self.triggered_strong,
        }


def score_scope(rs: RuntimeSignals) -> ScopeGuardResult:
    """Compute Scope Guard v0 score from runtime signals.

    Thresholds are v0 starting points; final values frozen after Pilot 15/20.
    """
    res = ScopeGuardResult()

    # Signal 1: many edited files
    s1 = rs.changed_files_count >= 5
    res.signals["changed_files_ge_5"] = s1
    res.signal_values["changed_files_count"] = rs.changed_files_count

    # Signal 2: many changed lines (use changed_lines_total which counts + and -)
    s2 = rs.changed_lines_total >= 200
    res.signals["changed_lines_ge_200"] = s2
    res.signal_values["changed_lines_total"] = rs.changed_lines_total

    # Signal 3: high API call count (proxy for long unfocused trajectory)
    s3 = rs.api_calls >= 80
    res.signals["api_calls_ge_80"] = s3
    res.signal_values["api_calls"] = rs.api_calls

    # Signal 4: repeated lexical edit pattern across >= 3 files
    s4 = bool(rs.repeated_edit_pattern_detected) or len(rs.repeated_edit_patterns) > 0
    res.signals["repeated_edit_pattern"] = s4
    res.signal_values["repeated_edit_patterns_count"] = len(rs.repeated_edit_patterns)

    # Signal 5: edited files lacking runtime evidence support.
    # v0 proxy: edited-but-not-viewed OR submitted without any tests
    #           OR viewed->edited ratio > 0.7 (saw it, changed it)
    if rs.viewed_files_count > 0:
        ratio = rs.edited_files_count / rs.viewed_files_count
    else:
        ratio = 1.0 if rs.edited_files_count > 0 else 0.0
    s5 = (
        rs.edited_but_not_viewed_files_count > 0
        or rs.submitted_without_tests
        or ratio > 0.7
    )
    res.signals["edited_files_lack_evidence"] = s5
    res.signal_values["viewed_to_edited_ratio"] = round(ratio, 3)
    res.signal_values["submitted_without_tests"] = rs.submitted_without_tests
    res.signal_values["edited_but_not_viewed_count"] = rs.edited_but_not_viewed_files_count

    res.score = sum(1 for v in res.signals.values() if v)
    res.triggered_warning = res.score >= res.threshold_warning
    res.triggered_strong = res.score >= res.threshold_strong
    return res
