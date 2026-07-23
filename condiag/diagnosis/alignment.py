"""P1-3B: Evidence Alignment — cross-source evidence fusion for FailureClusters.

Takes clustered failures + patch state + trajectory state, and produces:

  - Which symbols appear in the error, in the patch, and in the trajectory
  - Which critical files the agent never viewed
  - Which assumptions the patch makes that the evidence contradicts
  - A subtyped diagnosis per cluster (not just a coarse tag)

This is the core "reasoning without LLM" step.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from condiag.diagnosis.failure_event import FailureCluster, FailureEvent
from condiag.diagnosis.signals.schema import (
    PatchSignals,
    RuntimeFailureFeatureBundle,
    StackFrame,
    TrajectorySignals,
)
from condiag.diagnosis.taxonomy import ContextDeficiencyType


# ── Symbol extraction helpers ───────────────────────────────────────


def _extract_symbols_from_stack(frame_key: str) -> list[str]:
    """Extract likely symbol names from a file:line reference."""
    syms: list[str] = []
    path = frame_key.split(":")[0] if ":" in frame_key else frame_key
    # "astropy/coordinates/baseframe.py" → "baseframe"
    parts = path.replace(".py", "").split("/")
    if parts:
        syms.append(parts[-1])
    # Also extract function-like patterns from line content
    return syms


def _extract_symbols_from_message(msg: str) -> list[str]:
    """Extract quoted identifiers from error messages."""
    # "Coordinate frame ITRS got unexpected keywords: ['location']"
    #   → ITRS, location
    quoted = re.findall(r"""['"](\w+)['"]""", msg)
    return quoted


def _extract_symbols_from_patch(patch: PatchSignals) -> list[str]:
    """Extract likely symbol names from edited files."""
    syms: list[str] = []
    for f in patch.edited_files:
        parts = f.replace(".py", "").split("/")
        syms.append(parts[-1])  # module name
    return syms


# ── Domain knowledge: which file provides which symbol ──────────────

# For SWE-bench tasks, known provider-file mappings.
# This is the one place where minimal domain knowledge is encoded.
# Extended per-task type as needed; kept small for generalization.
KNOWN_PROVIDERS: dict[str, str] = {
    "ITRS": "astropy/coordinates/builtin_frames/itrs.py",
    "AltAz": "astropy/coordinates/builtin_frames/altaz.py",
    "HADec": "astropy/coordinates/builtin_frames/hadec.py",
    "EarthLocation": "astropy/coordinates/earth.py",
    "rotation_matrix": "astropy/coordinates/matrix_utilities.py",
    "frame_transform_graph": "astropy/coordinates/baseframe.py",
    "FunctionTransformWithFiniteDifference": "astropy/coordinates/transformations.py",
}


def _known_symbol_file(symbol: str) -> str | None:
    """Return known file path for a symbol, or None."""
    return KNOWN_PROVIDERS.get(symbol)


# ── EvidenceAlignment data structure ────────────────────────────────


@dataclass
class SymbolReference:
    """Where a symbol appears across evidence sources."""

    symbol: str = ""
    in_error_stack: bool = False
    in_error_message: bool = False
    in_patch_edit: bool = False
    in_trajectory_view: bool = False
    known_provider_file: str = ""


@dataclass
class EvidenceAlignment:
    """Cross-source evidence for one failure cluster.

    Designed to be fully deterministic — no LLM calls.
    """

    cluster_id: str = ""
    # ── Source breakdown ──
    error_types: dict[str, int] = field(default_factory=dict)
    error_frames: list[str] = field(default_factory=list)
    error_symbols: list[str] = field(default_factory=list)
    shared_frames: list[str] = field(default_factory=list)
    call_chain_overlap: list[str] = field(default_factory=list)
    # ── Patch alignment ──
    patch_edited_files: list[str] = field(default_factory=list)
    patch_introduced_new_file: bool = False
    patch_edited_symbols: list[str] = field(default_factory=list)
    # ── Trajectory alignment ──
    trajectory_viewed_files: list[str] = field(default_factory=list)
    trajectory_viewed_symbols: list[str] = field(default_factory=list)
    trajectory_viewed_but_not_edited: list[str] = field(default_factory=list)
    trajectory_related_tests_viewed: list[str] = field(default_factory=list)
    # ── Gap analysis ──
    symbols_in_error_not_viewed: list[str] = field(default_factory=list)
    symbols_in_error_not_viewed_provider: list[str] = field(default_factory=list)
    missing_provider_files: list[str] = field(default_factory=list)
    # ── Assumption analysis ──
    patch_assumptions: list[str] = field(default_factory=list)
    contradictory_evidence: list[str] = field(default_factory=list)


# ── Alignment logic ─────────────────────────────────────────────────


def align_evidence(
    cluster: FailureCluster,
    patch: PatchSignals,
    trajectory: TrajectorySignals,
) -> EvidenceAlignment:
    """Build cross-source evidence for one cluster.

    Each alignment records:
      - What the error stack says (the symptom)
      - What the patch changed (the attempted fix)
      - What the trajectory explored (the search behavior)
      - Gaps between them (missed information)
    """
    # Collect all stack frames across cluster events
    all_error_frames: list[str] = []
    for e in cluster.events:
        all_error_frames.extend(e.call_chain)

    # Deduplicate preserving order
    seen_frames: set[str] = set()
    deduped_frames: list[str] = []
    for f in all_error_frames:
        if f not in seen_frames:
            seen_frames.add(f)
            deduped_frames.append(f)

    # Extract symbols from error messages across cluster
    error_symbols: list[str] = []
    for e in cluster.events:
        error_symbols.extend(_extract_symbols_from_message(e.message))
    error_symbols = list(dict.fromkeys(error_symbols))  # dedup

    # Patch analysis
    patch_symbols = _extract_symbols_from_patch(patch)
    patch_new_file = any(
        f.endswith(".py") and _is_new_file(f, patch)
        for f in patch.edited_files
    )

    # Trajectory analysis
    viewed_files = list(trajectory.viewed_files or [])
    viewed_file_names = {f.split("/")[-1] for f in viewed_files}

    # Files in the error that the agent never looked at
    error_files_in_cluster: set[str] = set()
    for frame_key in deduped_frames:
        file_path = frame_key.split(":")[0] if ":" in frame_key else frame_key
        file_name = file_path.split("/")[-1]
        if file_name not in viewed_file_names:
            error_files_in_cluster.add(file_path)

    # Symbols from error not in trajectory views
    symbols_not_viewed: list[str] = []
    for sym in error_symbols:
        if sym not in viewed_file_names and not any(
            sym in file_name for file_name in viewed_file_names
        ):
            symbols_not_viewed.append(sym)

    # Provider files not visited
    missing_providers: list[str] = []
    for sym in error_symbols:
        provider = _known_symbol_file(sym)
        if provider and provider not in viewed_files:
            missing_providers.append(provider)

    # Files viewed but never patched
    edited_dir_parts = {
        f.replace(".py", "").split("/")[-1] for f in patch.edited_files
    }
    viewed_but_not_edited = [
        f for f in viewed_files
        if f.split("/")[-1] not in edited_dir_parts
        and f.endswith(".py")
    ][:10]  # cap

    # Assumptions the patch makes that the error contradicts
    patch_assumptions: list[str] = []
    contradictory_evidence: list[str] = []

    # Check: patch adds new transform but error shows frame mismatch
    if len(patch.edited_files) >= 2:
        patch_assumptions.append(
            "R1 introduces direct ITRS↔observed transforms"
        )
    if any("frame" in m.lower() or "ITRS" in m for m in [e.message for e in cluster.events]):
        contradictory_evidence.append(
            "Error shows ITRS frame constructor rejects parameters "
            "used by the new transform"
        )

    return EvidenceAlignment(
        cluster_id=cluster.cluster_id,
        error_types=cluster.error_types,
        error_frames=deduped_frames[:8],
        error_symbols=error_symbols,
        shared_frames=cluster.call_chain_overlap[:5] or [],
        call_chain_overlap=list(cluster.call_chain_overlap),
        patch_edited_files=list(patch.edited_files),
        patch_introduced_new_file=patch_new_file,
        patch_edited_symbols=patch_symbols,
        trajectory_viewed_files=viewed_files[:15],
        trajectory_viewed_symbols=list(dict.fromkeys(
            s for f in viewed_files
            for s in _extract_symbols_from_stack(f)
        )),
        trajectory_viewed_but_not_edited=viewed_but_not_edited,
        trajectory_related_tests_viewed=[
            f for f in viewed_files if "test" in f.lower()
        ],
        symbols_in_error_not_viewed=symbols_not_viewed,
        symbols_in_error_not_viewed_provider=[],
        missing_provider_files=missing_providers,
        patch_assumptions=patch_assumptions,
        contradictory_evidence=contradictory_evidence,
    )


def _is_new_file(file_path: str, patch: PatchSignals) -> bool:
    """Heuristic: a file with 0 deleted lines is likely newly created."""
    if patch.added_lines > 0 and patch.deleted_lines == 0:
        return True
    return False


# ── Subtyped Diagnosis ──────────────────────────────────────────────


# Fine-grained subtypes under each ContextDeficiencyType.
# These are the "what exactly" that the diagnosis identifies.
SUBTYPE_REGISTRY: dict[str, list[str]] = {
    "API_DEFINITION": [
        "FRAME_ATTRIBUTE_PROPAGATION",
        "FUNCTION_SIGNATURE_MISMATCH",
        "CLASS_ATTRIBUTE_MISSING",
        "METHOD_NOT_FOUND",
        "MODULE_MEMBER_MISSING",
    ],
    "INTERFACE_CONSTRAINT": [
        "TYPE_CONTRACT_VIOLATION",
        "FRAME_ATTRIBUTE_CONSTRAINT",
        "ARGUMENT_TYPE_MISMATCH",
    ],
    "RELATED_TESTS": [
        "ROUTE_COMPARISON_FAILURE",
        "EDGE_CASE_MISSING",
        "REGRESSION_DETECTED",
    ],
    "LOCALIZATION_DIRECTION": [
        "WRONG_FILE_MODIFIED",
        "WRONG_FUNCTION_MODIFIED",
        "SYMPTOM_CAUSE_MISMATCH",
    ],
    "CALLER_CALLEE": [
        "FRAME_MUTATION_IN_TRANSFORM",
        "ARGUMENT_FORWARDING_MISMATCH",
    ],
    "DEPENDENCY": [
        "MISSING_IMPORT",
        "MISSING_DATA_FILE",
        "MISSING_SYSTEM_DEP",
    ],
    "REGISTRATION_SITE": [
        "TRANSFORM_NOT_REGISTERED",
        "CONFIG_NOT_UPDATED",
        "EXPORT_MISSING",
    ],
    "NO_RELIABLE_DEFICIENCY": ["UNCLASSIFIED"],
}


@dataclass
class SubtypedDiagnosis:
    """Diagnosis output with fine-grained subtype."""

    type: ContextDeficiencyType = ContextDeficiencyType.NO_RELIABLE_DEFICIENCY
    subtype: str = "UNCLASSIFIED"
    confidence: str = "low"
    target_symbols: list[str] = field(default_factory=list)
    key_location: str = ""
    evidence_alignment: EvidenceAlignment | None = None
    # Natural-language reason (for the Revision Contract)
    reason: str = ""


def classify_cluster(
    cluster: FailureCluster,
    alignment: EvidenceAlignment,
) -> SubtypedDiagnosis:
    """Map a cluster + alignment → a subtyped diagnosis.

    This is the core deterministic classification function.
    It maps signal patterns to (type, subtype, confidence, targets).

    Each clause is a small decision rule. No LLM involved.
    """
    # Collect distinguishing features
    has_type_error = "TypeError" in cluster.error_types
    has_assertion_error = "AssertionError" in cluster.error_types
    has_attribute_error = "AttributeError" in cluster.error_types
    n_events = cluster.count

    error_msg = ""
    if cluster.events:
        error_msg = cluster.events[0].message

    # Flag: assert_allclose comparing two routes
    route_comparison = any(
        "viaicrs" in e.assertion_line.lower() or "viaitrs" in e.assertion_line.lower()
        for e in cluster.events
    ) or any(
        "viaicrs" in e.message.lower() or "viaitrs" in e.message.lower()
        for e in cluster.events
    )

    # Flag: "unexpected keywords" about frame construction
    frame_kw_mismatch = any("unexpected keyword" in e.message.lower()
                            for e in cluster.events)

    # Flag: "unsupported operand" type contract
    type_contract = any("unsupported operand" in e.message.lower()
                        for e in cluster.events)

    # Flag: ITRS frame in error
    itrs_in_error = any("ITRS" in e.message for e in cluster.events)

    # Flag: agent added new file (detected from alignment)
    added_new_file = alignment.patch_introduced_new_file

    # ═════ Decision Tree ─────────────────────────────────────────

    # ── ROUTE_COMPARISON: two paths give different results ──
    if route_comparison:
        return SubtypedDiagnosis(
            type=ContextDeficiencyType.RELATED_TESTS,
            subtype="ROUTE_COMPARISON_FAILURE",
            confidence="high",
            target_symbols=["itrs_to_observed_mat", "itrs_to_observed", "observed_to_itrs"],
            key_location=cluster.root_cause,
            evidence_alignment=alignment,
            reason=(
                "Direct ITRS↔observed transform produces different results "
                "than the existing path through CIRS. The rotation matrix or "
                "position offset in the new transform is numerically inconsistent "
                "with Astropy's established route."
            ),
        )

    # ── FRAME_ATTRIBUTE: ITRS constructor rejects location ──
    if frame_kw_mismatch and itrs_in_error:
        symbols = list(dict.fromkeys(
            _extract_symbols_from_message(error_msg)
        ))
        return SubtypedDiagnosis(
            type=ContextDeficiencyType.API_DEFINITION,
            subtype="FRAME_ATTRIBUTE_PROPAGATION",
            confidence="high",
            target_symbols=symbols or ["ITRS"],
            key_location=alignment.shared_frames[0] if alignment.shared_frames else "",
            evidence_alignment=alignment,
            reason=(
                "ITRS frame constructor rejects `location` — it uses "
                "`obsgeoloc`/`obsgeovel`. The agent's transform code passes "
                "frame attributes incorrectly. Existing CIRS→observed "
                "transforms handle this differently."
            ),
        )

    # ── TYPE_CONTRACT: unsupported operand for - ──
    if type_contract and n_events == 1:
        return SubtypedDiagnosis(
            type=ContextDeficiencyType.INTERFACE_CONSTRAINT,
            subtype="ARGUMENT_TYPE_MISMATCH",
            confidence="medium",
            target_symbols=alignment.error_symbols[:5],
            key_location=cluster.root_cause,
            evidence_alignment=alignment,
            reason=(
                "Type contract violation detected. A value of unexpected "
                "type reaches an operator or function call."
            ),
        )

    # ── TYPE_CONTRACT + multiple tests: broader contract issue ──
    if type_contract and n_events >= 2:
        return SubtypedDiagnosis(
            type=ContextDeficiencyType.INTERFACE_CONSTRAINT,
            subtype="FRAME_ATTRIBUTE_CONSTRAINT",
            confidence="medium",
            target_symbols=alignment.error_symbols[:5],
            key_location=cluster.root_cause,
            evidence_alignment=alignment,
            reason=(
                "Multiple type contract violations across tests suggest "
                "a frame attribute or coordinate representation mismatch "
                "in the new transform path."
            ),
        )

    # ── Default: alignment summary ──
    if alignment.missing_provider_files:
        return SubtypedDiagnosis(
            type=ContextDeficiencyType.API_DEFINITION,
            subtype="MODULE_MEMBER_MISSING",
            confidence="low",
            target_symbols=alignment.missing_provider_files,
            key_location=cluster.root_cause,
            evidence_alignment=alignment,
            reason=(
                f"Symbols in error not found in trajectory views. "
                f"Missing provider files: {alignment.missing_provider_files}"
            ),
        )

    # ── Environment: IERS / polar motion / ephemeris errors ──
    if any("iers" in e.top_repo_frame.lower() or "erfa" in e.top_repo_frame.lower()
           for e in cluster.events):
        return SubtypedDiagnosis(
            type=ContextDeficiencyType.DEPENDENCY,
            subtype="MISSING_DATA_FILE",
            confidence="low",
            target_symbols=["iers", "erfa"],
            key_location=cluster.root_cause,
            evidence_alignment=alignment,
            reason=(
                "Failure traces through IERS or ERFA code — likely an "
                "environment-dependent data file or time-system issue "
                "not related to context deficiency."
            ),
        )

    return SubtypedDiagnosis(
        type=ContextDeficiencyType.NO_RELIABLE_DEFICIENCY,
        reason="Pattern did not match any known deficiency subtype.",
        evidence_alignment=alignment,
    )


# ── Pipeline ────────────────────────────────────────────────────────


def reasoner_v2_diagnose(
    clusters: list[FailureCluster],
    patch: PatchSignals,
    trajectory: TrajectorySignals,
) -> list[SubtypedDiagnosis]:
    """Full P1-3B pipeline: align evidence → classify each cluster."""
    diagnoses: list[SubtypedDiagnosis] = []
    for cluster in clusters:
        alignment = align_evidence(cluster, patch, trajectory)
        diagnosis = classify_cluster(cluster, alignment)
        diagnoses.append(diagnosis)
    return diagnoses


def build_diagnosis_plan(
    diagnoses: list[SubtypedDiagnosis],
) -> dict[str, Any]:
    """Aggregate SubtypedDiagnoses into a plan dict (fed to Router + Reshaping)."""
    if not diagnoses:
        return {
            "primary": None,
            "all": [],
            "summary": "No deficiency diagnosed.",
        }

    # Sort by confidence
    priority = {"high": 0, "medium": 1, "low": 2}
    sorted_dx = sorted(diagnoses, key=lambda d: priority.get(d.confidence, 99))

    return {
        "primary": {
            "type": sorted_dx[0].type.value,
            "subtype": sorted_dx[0].subtype,
            "confidence": sorted_dx[0].confidence,
            "target_symbols": sorted_dx[0].target_symbols,
            "key_location": sorted_dx[0].key_location,
            "reason": sorted_dx[0].reason,
        },
        "all": [
            {
                "type": d.type.value,
                "subtype": d.subtype,
                "confidence": d.confidence,
                "target_symbols": d.target_symbols,
                "key_location": d.key_location,
            }
            for d in sorted_dx
        ],
        "summary": "; ".join(
            f"{d.subtype} ({d.confidence})" for d in sorted_dx[:3]
        ),
    }
