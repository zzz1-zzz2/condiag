"""ConDiag Diagnosis — rule-based reasoner (Phase 2).

Takes structured failure signals → outputs context deficiency classification.

Architecture:
  FailureFeatureBundle → DiagnoserCore → DiagnosisResult
                              ↓
                        Router / Compressor

Phase 2 implementation: rule-based (no LLM calls).
Phase 3 enhancement: LLM reasoner for ablation comparison.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from condiag.diagnosis.signals.schema import FailureFeatureBundle
from condiag.diagnosis.taxonomy import ContextDeficiencyType, ConfidenceLevel


class DeficiencyFinding(BaseModel):
    """One proposed deficiency diagnosis."""

    type: ContextDeficiencyType = Field(description="Predicted deficiency type")
    confidence: ConfidenceLevel = Field(default="low")
    evidence: list[str] = Field(
        default_factory=list,
        description="Human-readable evidence strings supporting this finding",
    )
    key_location: str = Field(
        default="",
        description="File:line anchor for the finding (e.g. 'iers.py:271')",
    )


class DiagnosisResult(BaseModel):
    """Diagnoser output — fed into Compressor and Router."""

    primary: DeficiencyFinding = Field(description="Highest-confidence finding")
    secondary: list[DeficiencyFinding] = Field(
        default_factory=list,
        description="Alternative/secondary findings (usually lower confidence)",
    )
    rejected_assumptions: list[str] = Field(
        default_factory=list,
        description="What the R1 approach got wrong — used to prevent repeating the same mistake",
    )
    raw_signals_sha: str = Field(
        default="", description="SHA of input signals for traceability"
    )


class DiagnoserCore:
    """Rule-based diagnoser: signal patterns → context deficiency types.

    Pure pattern matching — no LLM inference.
    Each rule is one (signal_pattern → diagnosis) mapping, weighted by confidence.
    """

    def diagnose(self, bundle: FailureFeatureBundle) -> DiagnosisResult:
        """Run all rules against the signal bundle, return aggregated diagnosis."""

        findings: list[DeficiencyFinding] = []
        rejected: list[str] = []
        tl = bundle.test_log
        patch = bundle.patch

        # ── Rule 1: TypeError + "unexpected keywords" → API_DEFINITION ──
        unexpected_kw = [em for em in tl.error_messages if "unexpected keyword" in em.lower()]
        if unexpected_kw:
            findings.append(DeficiencyFinding(
                type=ContextDeficiencyType.API_DEFINITION,
                confidence="high",
                evidence=[f"TypeError with unexpected keywords: {unexpected_kw[0]}"],
                key_location=_extract_location(tl, unexpected_kw[0]),
            ))

        # ── Rule 2: TypeError + unsupported operand → INTERFACE_CONSTRAINT ──
        if "TypeError" in tl.error_types:
            for em in tl.error_messages:
                if "unsupported operand" in em.lower():
                    findings.append(DeficiencyFinding(
                        type=ContextDeficiencyType.INTERFACE_CONSTRAINT,
                        confidence="high",
                        evidence=[f"Type contract violation: {em}"],
                        key_location=_extract_location(tl, em),
                    ))

        # ── Rule 3: ImportError / ModuleNotFoundError → DEPENDENCY ──
        for err_type in ("ImportError", "ModuleNotFoundError"):
            if err_type in tl.error_types:
                findings.append(DeficiencyFinding(
                    type=ContextDeficiencyType.DEPENDENCY,
                    confidence="high",
                    evidence=[f"Missing module: {err_type}"],
                ))

        # ── Rule 4: AttributeError → API_DEFINITION ──
        if "AttributeError" in tl.error_types:
            findings.append(DeficiencyFinding(
                type=ContextDeficiencyType.API_DEFINITION,
                confidence="medium",
                evidence=["AttributeError — symbol not found on object"],
            ))

        # ── Rule 5: AssertionError → RELATED_TESTS ──
        if "AssertionError" in tl.error_types:
            findings.append(DeficiencyFinding(
                type=ContextDeficiencyType.RELATED_TESTS,
                confidence="medium",
                evidence=["AssertionError — test logic or behavior mismatch"],
            ))

        # ── Rule 6: Edit vs Error location mismatch → LOCALIZATION_DIRECTION ──
        if patch.edited_files and tl.stack_frames:
            edit_files = set(patch.edited_files)
            error_files = {f.file for f in tl.stack_frames if f.is_repo_frame and not f.is_test_file}
            error_files_short = {f.split("/")[-1] for f in error_files}
            edit_files_short = {f.split("/")[-1] for f in edit_files}
            overlap = edit_files_short & error_files_short
            if len(overlap) == 0:
                rejected.append(
                    f"R1 edited {', '.join(patch.edited_files)} but error is in "
                    f"{', '.join(list(error_files)[:3])} — wrong localization"
                )

        # ── Aggregate findings ──
        if not findings:
            findings.append(DeficiencyFinding(
                type=ContextDeficiencyType.API_DEFINITION,
                confidence="low",
                evidence=["No strong signal pattern matched — defaulting to API_DEFINITION"],
            ))

        # Sort by confidence, pick primary
        priority = {"high": 0, "medium": 1, "low": 2}
        findings.sort(key=lambda f: priority.get(f.confidence.value, 99))

        primary = findings[0]
        secondary = findings[1:]

        return DiagnosisResult(
            primary=primary,
            secondary=secondary,
            rejected_assumptions=rejected,
            raw_signals_sha=_sha_bundle(bundle),
        )


def _extract_location(tl, error_text: str) -> str:
    """Find the most likely file:line from error text + stack frames."""
    for f in tl.stack_frames:
        if f.is_repo_frame and not f.is_test_file:
            return f"{f.file}:{f.line}"
    return ""


def _sha_bundle(bundle: FailureFeatureBundle) -> str:
    """Quick hash of the input signals for traceability."""
    import hashlib, json
    raw = json.dumps(bundle.model_dump(), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]
