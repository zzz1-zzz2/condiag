"""Diagnostic Search Contract Builder — structured failure-guided exploration directives.

ConDiag's core output: a DiagnosticSearchContract that tells the Attempt-2 agent
WHERE to look and WHAT to search for, without doing the retrieval itself.

Input:  FailureWitness + runtime TrajectorySignals
Output: DiagnosticSearchContract (JSON)

All required_inspections and required_searches are generated from runtime-only
sources (stack frames, visited files, edited files, error messages, issue text).
NO gold data enters the contract — Rule 1 and Rule 5 enforced here.

Usage:
    builder = DiagnosticSearchContractBuilder(traj_parser, witness_path)
    contract = builder.build(runtime_signals)
    contract_json = contract.to_dict()  # serializable dict
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

from condiag.diagnosis_generator import classify_deficiency
from condiag.diagnosis_generator import classify_deficiency
from condiag.trajectory_signals import (
    FailureWitnessLoader,
    RuntimeSignals,
    TrajParser,
)


# =====================================================================
# DiagnosticSearchContract — output schema
# =====================================================================


@dataclass
class FailureSummary:
    error_type: str = "Unknown"
    failing_test: str = ""
    failure_mode: str = "unknown"  # post_validation_failure / post_validation_no_failure


@dataclass
class TrajectorySignalSnapshot:
    """Key signals that informed the contract (subset of RuntimeSignals)."""
    error_edit_alignment: str = "unknown"
    exploration_mode: str = "unknown"


@dataclass
class RequiredInspection:
    file: str = ""
    lines: list[int] = field(default_factory=list)
    reason: str = ""


@dataclass
class RequiredSearch:
    query: str = ""
    type: str = ""  # symbol_definition / grep / caller_search / file_search
    scope: str = ""


@dataclass
class ValidationTarget:
    test_command: str = ""
    expected_behavior: str = "test passes without error"


@dataclass
class EvidenceProvenance:
    contract_source: str = ""  # failure_witness_signals / trajectory_signal_analysis / issue_driven_deduction
    supporting_artifact: str = ""


@dataclass
class DiagnosticSearchContract:
    """Structured diagnostic search contract.

    Serializes to the JSON schema defined in condiag_plan_v2_search_contract.md.
    Every field is generated from runtime-only sources — no gold leakage.
    """
    contract_version: str = "1.0"
    instance_id: str = ""
    failure_summary: FailureSummary = field(default_factory=FailureSummary)
    trajectory_signals: TrajectorySignalSnapshot = field(default_factory=TrajectorySignalSnapshot)
    required_inspections: list[RequiredInspection] = field(default_factory=list)
    required_searches: list[RequiredSearch] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    validation_target: ValidationTarget = field(default_factory=ValidationTarget)
    evidence_provenance: EvidenceProvenance = field(default_factory=EvidenceProvenance)
    context_deficiency_diagnosis: dict[str, Any] = field(default_factory=dict)
    context_deficiency_diagnosis: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict matching plan schema."""
        def _serialize(obj: Any) -> Any:
            if hasattr(obj, "_asdict"):
                return obj._asdict()
            if isinstance(obj, dict):
                return {k: _serialize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_serialize(v) for v in obj]
            if dataclasses_is_dataclass(obj):
                return asdict(obj)
            return obj

        return {
            "contract_version": self.contract_version,
            "instance_id": self.instance_id,
            "failure_summary": asdict(self.failure_summary),
            "trajectory_signals": asdict(self.trajectory_signals),
            "required_inspections": [asdict(i) for i in self.required_inspections],
            "required_searches": [asdict(s) for s in self.required_searches],
            "anti_patterns": list(self.anti_patterns),
            "validation_target": asdict(self.validation_target),
            "evidence_provenance": asdict(self.evidence_provenance),
            "context_deficiency_diagnosis": dict(self.context_deficiency_diagnosis),
        }


def dataclasses_is_dataclass(obj):
    """Check if obj is a dataclass without importing the full inspect module."""
    return hasattr(obj, "__dataclass_fields__")


# =====================================================================
# SearchContractBuilder
# =====================================================================


class DiagnosticSearchContractBuilder:
    """Build a DiagnosticSearchContract from trajectory + failure witness.

    All generated inspections and searches are traceable to specific runtime
    evidence. No gold data used. Verification gates:
      - contract_source in {failure_witness_signals, trajectory_signal_analysis,
        issue_driven_deduction}
      - supporting_artifact non-empty and points to specific evidence
      - required_inspections file+line+reason all non-empty
      - no reference to gold patch, gold context, or resolved status
    """

    def __init__(
        self,
        parser: TrajParser,
        witness_path: Optional[str | Path] = None,
        issue_text: str = "",
    ):
        self.parser = parser
        self.witness: Optional[FailureWitnessLoader] = None
        if witness_path and Path(witness_path).exists():
            self.witness = FailureWitnessLoader(witness_path)
        self.issue_text = issue_text

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build(self, signals: RuntimeSignals) -> DiagnosticSearchContract:
        """Build a complete contract from trajectory signals.

        Args:
            signals: Pre-computed RuntimeSignals from trajectory_signals.py.

        Returns:
            DiagnosticSearchContract with all fields populated from runtime evidence.
        """
        contract = DiagnosticSearchContract(
            contract_version="1.0",
            instance_id=self.parser.instance_id,
        )

        contract.failure_summary = self._build_failure_summary()
        contract.trajectory_signals = self._build_signal_snapshot(signals)
        contract.required_inspections = self._build_required_inspections(signals)
        contract.required_searches = self._build_required_searches(signals)
        contract.anti_patterns = self._build_anti_patterns(signals)
        contract.validation_target = self._build_validation_target()
        contract.evidence_provenance = self._build_evidence_provenance(signals)

        # Classify context deficiency type from runtime signals
        try:
            # Map RuntimeSignals field names -> classify_deficiency expected format
            rs_fields = asdict(signals) if hasattr(signals, '__dataclass_fields__') else {}
            # Compute patch line count
            patch_text = self.parser.submission_patch
            changed_lines = patch_text.count('\n') if patch_text else 0
            # Derive trigger_type from error_edit_alignment
            align = signals.error_edit_alignment
            if align in ('viewed_not_edited', 'edited_elsewhere', 'error_file_never_viewed'):
                derived_trigger = 'EVIDENCE_EDIT_MISMATCH'
            elif align == 'aligned':
                derived_trigger = 'PARTIAL_FIX_SUSPICION'
            else:
                derived_trigger = 'UNKNOWN'
            # Build adapted signal dict matching _patch_shape_signals expectations
            adapted_signals = {
                'edited_files_count': signals.unique_files_edited,
                'changed_lines_total': changed_lines,
                'viewed_files_count': signals.unique_files_visited,
                'viewed_but_not_final_files_count': len(signals.viewed_then_dropped_files),
                'test_runs_count': signals.total_test_commands,
                'test_failures_count': signals.test_runs,
            }
            trigger_reason = []
            if self.witness and self.witness.has_failure():
                frames = self.witness.all_stack_source_files()
                trigger_reason = [f.get('func', '') for f in frames if f.get('func')]
            cdtype_primary, cdtype_secondary, cdtype_confidence = classify_deficiency(
                trigger_type=derived_trigger,
                trigger_reason=trigger_reason,
                runtime_signals=adapted_signals,
                issue=self.issue_text,
            )
            contract.context_deficiency_diagnosis = {
                'scores': {cdtype_primary: cdtype_confidence},
                'explanation': f'Diagnosed {cdtype_primary} from trajectory signals and failure witness',
                'action': f'Guide search toward resolving {cdtype_primary} deficiency',
            }
        except Exception as exc:
            contract.context_deficiency_diagnosis = {
                'scores': {'unknown': 0.0},
                'explanation': f'Classification failed: {exc}',
                'action': 'Fall back to general search guidance',
            }

        return contract

    # ------------------------------------------------------------------
    # Failure Summary
    # ------------------------------------------------------------------

    def _build_failure_summary(self) -> FailureSummary:
        """Extract error type, failing test, and failure mode from witness."""
        if self.witness is None or not self.witness.has_failure():
            return FailureSummary(failure_mode="post_validation_no_failure")

        failure_type = self.witness.failure_type or "Unknown"

        # Try to extract failing test name from failed_tests or error_message
        failing_test = ""
        err = self.witness.error_message

        # Check failed_tests list
        ft = self.witness._data.get("failed_tests", [])
        if ft:
            failing_test = ft[0]

        # Fallback: extract from FAILED line
        if not failing_test:
            m = re.search(r"FAILED\s+(\S+::\S+)", err)
            if m:
                failing_test = m.group(1)

        # Fallback: extract test method name
        if not failing_test:
            m = re.search(r"(test_\w+)", err)
            if m:
                failing_test = m.group(1)

        return FailureSummary(
            error_type=failure_type,
            failing_test=failing_test,
            failure_mode="post_validation_failure",
        )

    # ------------------------------------------------------------------
    # Trajectory Signal Snapshot
    # ------------------------------------------------------------------

    def _build_signal_snapshot(
        self, signals: RuntimeSignals
    ) -> TrajectorySignalSnapshot:
        """Copy key trajectory signals into the contract."""
        return TrajectorySignalSnapshot(
            error_edit_alignment=signals.error_edit_alignment,
            exploration_mode=signals.exploration_mode,
        )

    # ------------------------------------------------------------------
    # Required Inspections (core output — 6 runtime-only sources)
    # ------------------------------------------------------------------

    def _build_required_inspections(
        self, signals: RuntimeSignals
    ) -> list[RequiredInspection]:
        """Generate required file inspections from runtime evidence.

        Sources (all runtime-only):
        1. Stack trace frames — files mentioned in the traceback
        2. Failed test file — the test that failed
        3. Error source file — primary source file raising the error
        4. Viewed-but-dropped — files agent viewed but didn't edit
        5. Edited but error-unrelated — files agent edited that aren't in stack
        6. Error-term matched files — files whose names match error terms

        Deduplication: same file appears only once (earliest/highest priority source wins).
        """
        inspections: list[RequiredInspection] = []
        seen_fullpaths: set[str] = set()  # track by full path to avoid duplicates

        # Get visited/edited file sets
        visited_short = self.parser.visited_files()
        edited_short = {f.split("/")[-1] for f in self.parser.edited_files()}

        def _is_repo_file(filepath: str) -> bool:
            """Check if file is in the repository (not system/framework)."""
            return not (
                filepath.startswith("/opt/")
                or filepath.startswith("/usr/")
                or filepath.startswith("<frozen")
                or filepath.startswith("/var/")
                or "/miniconda3/" in filepath
                or "/lib/python" in filepath
            )

        def _add(filepath: str, line: int, reason: str):
            """Add inspection if file is a repo file and not already covered."""
            if not _is_repo_file(filepath):
                return
            fname = filepath.split("/")[-1]
            if not fname:
                return
            # Dedup by full path (preferred) or short name (for path-less entries)
            dedup_key = filepath if "/" in filepath else fname
            if dedup_key in seen_fullpaths:
                return
            seen_fullpaths.add(dedup_key)
            inspections.append(RequiredInspection(
                file=filepath,
                lines=[line] if line > 0 else [],
                reason=reason,
            ))

        # Source 1: Stack trace frames
        if self.witness and self.witness.has_failure():
            stack_sources = self.witness.all_stack_source_files()
            for sf in stack_sources:
                fname = sf["file"]
                fullpath = sf.get("fullpath", fname)
                line = sf.get("line", 0)
                func = sf.get("func", "")

                # Mark unvisited stack frames as higher priority
                was_visited = fname in visited_short or any(
                    vf.endswith(fname) for vf in visited_short
                )
                if was_visited and fname in edited_short:
                    _add(fullpath, line, f"stack trace frame (visited, edited: {func})")
                elif was_visited:
                    _add(fullpath, line, f"stack trace frame (visited but not edited: {func})")
                else:
                    _add(fullpath, line, f"stack trace frame (NOT visited by agent: {func})")

        # Source 2: Failed test file
        failing_test = self._build_failure_summary().failing_test
        if failing_test:
            test_file = failing_test.split("::")[0]
            if test_file:
                _add(test_file, 0, "failed test file")

        # Source 3: Error source file (top repo error file if not already covered)
        if self.witness and self.witness.has_failure():
            top_file = self.witness.top_error_file_fullpath()
            top_short = self.witness.top_error_file()
            if top_short:
                # Find the line from the first repo frame
                frames = self.witness.top_repo_frames
                err_line = frames[0].get("line", 0) if frames else 0
                _add(
                    top_file or top_short,
                    err_line,
                    "primary error source file",
                )

        # Source 4: Viewed-then-dropped evidence
        for dropped in signals.viewed_then_dropped_files:
            # Parse out the filename (may have " (stack frame, unvisited)" suffix)
            fname = dropped.split(" (stack")[0].strip()
            if fname:
                _add(fname, 0, "viewed but not utilized by agent")

        # Source 5: Edited files not in stack trace
        if self.witness and self.witness.has_failure():
            stack_file_names = {sf["file"] for sf in self.witness.all_stack_source_files()}
            for ef in self.parser.edited_files():
                ef_short = ef.split("/")[-1]
                if ef_short not in stack_file_names:
                    # Check if it's a source file (not test)
                    if "test" not in ef_short.lower():
                        _add(ef, 0, "edited file (not in error stack — verify relevance)")

        # Sort inspections: stack frames with explicit lines first, then by having a line number
        inspections.sort(key=lambda x: (
            -bool(x.lines),       # inspections with line numbers first
            -(x.reason.count("NOT visited") if hasattr(x.reason, "count") else 0),  # unvisited files higher priority
        ))
        # Limit to top 10
        inspections = inspections[:10]

        return inspections

    # ------------------------------------------------------------------
    # Required Searches
    # ------------------------------------------------------------------

    def _build_required_searches(
        self, signals: RuntimeSignals
    ) -> list[RequiredSearch]:
        """Generate required search queries from runtime evidence.

        Types:
        - symbol_definition: Look up a function/class definition from stack trace
        - grep: Search for a pattern in the codebase
        - caller_search: Find callers of a function
        - file_search: Find a specific file

        All queries derived from error/issue signals, not gold.
        """
        searches: list[RequiredSearch] = []
        seen_queries: set[str] = set()

        def _add(query: str, typ: str, scope: str = ""):
            if query and query not in seen_queries:
                seen_queries.add(query)
                searches.append(RequiredSearch(
                    query=query,
                    type=typ,
                    scope=scope,
                ))

        # Source 1: Error symbols (function/class names from stack trace)
        if self.witness and self.witness.has_failure():
            for symbol in self.witness.error_symbols():
                if symbol and symbol != "<module>":
                    _add(symbol, "symbol_definition")

            # Source 2: Grep for error-specific terms from error message
            error_msg = self.witness.error_message
            # Noise words to skip — pip output, common test infrastructure
            _NOISE_TERMS = frozenset({
                "Collecting", "Downloading", "Installing", "Successfully",
                "WARNING", "Running", "FAILED", "ERROR", "Traceback",
                "Skipping", "System", "Docs", "Found", "Total",
                "Short", "Ran", "DeprecationWarning", "PytestConfigWarning",
                "Unknown", "Warning", "Config",
            })
            # Also skip terms ending in "Warning" (noisy deprecation messages)
            def _is_noise(term: str) -> bool:
                if term in _NOISE_TERMS:
                    return True
                if term.endswith("Warning"):
                    return True
                if term in ("Config", "Unknown"):
                    return True
                return False
            # Extract distinctive capitalized terms (class names, type names)
            for match in re.finditer(
                r"\b[A-Z][a-zA-Z0-9_]+(?:\.[a-zA-Z]+)?\b", error_msg
            ):
                term = match.group()
                if len(term) > 4 and term not in seen_queries and not _is_noise(term):
                    _add(term, "grep")

        # Source 3: Issue-derived searches
        if self.issue_text:
            # Extract capitalized multi-word phrases from issue
            for match in re.finditer(
                r"\b[A-Z][a-zA-Z0-9_]+\.[a-zA-Z]+\b", self.issue_text
            ):
                term = match.group()
                if term not in seen_queries:
                    _add(term, "symbol_definition")

        # Source 4: Exploration-mode-specific searches
        if signals.exploration_mode == "jumping":
            # Jumping agents browse without focus — suggest targeted grep
            error_types = []
            if self.witness and self.witness.has_failure():
                error_msg = self.witness.error_message
                for match in re.finditer(r"\b(\w+Error|assert \w+)\b", error_msg, re.IGNORECASE):
                    et = match.group(1)
                    if et not in seen_queries and et not in error_types:
                        error_types.append(et)
            for et in error_types[:2]:
                _add(et, "grep")

        # Source 5: Search for the failed test file
        failing_test = self._build_failure_summary().failing_test
        if failing_test:
            test_file = failing_test.split("::")[0]
            if test_file:
                _add(test_file, "file_search")

        # Limit to top 10 most important searches (prioritize symbol_definitions)
        searches.sort(key=lambda x: (
            0 if x.type == "symbol_definition" else
            1 if x.type == "grep" else
            2
        ))
        searches = searches[:10]

        return searches

    # ------------------------------------------------------------------
    # Anti-Patterns
    # ------------------------------------------------------------------

    def _build_anti_patterns(
        self, signals: RuntimeSignals
    ) -> list[str]:
        """Generate anti-pattern warnings based on trajectory signals.

        Each anti-pattern targets a specific failure mode observed in the
        Attempt-1 trajectory. Warnings are concrete and actionable.
        """
        patterns: list[str] = []

        # Anti-pattern 1: Exploration-mode specific
        if signals.exploration_mode == "oscillating":
            patterns.append(
                "Avoid repetitive test runs without intervening edits. "
                "Each test run should follow a specific code change. "
                "If a test fails, inspect the error location before re-running."
            )

        if signals.exploration_mode == "jumping":
            patterns.append(
                "Limit file browsing. Investigate the specific error "
                "(stack trace, assertion failure) before opening new files. "
                "Use the required_inspections list as your starting point."
            )

        # Anti-pattern 2: Error-edit alignment specific
        if signals.error_edit_alignment == "viewed_not_edited":
            patterns.append(
                "The stack trace files were viewed but not modified in attempt_1. "
                "Start by inspecting the error source file at the failing line, "
                "then trace the call to understand what needs to change."
            )

        if signals.error_edit_alignment == "edited_elsewhere":
            patterns.append(
                "The edit location in attempt_1 does not align with the error location. "
                "The correct file was edited but at a different function or line. "
                "Use required_inspections to find the exact error location before editing."
            )

        if signals.error_edit_alignment == "error_file_never_viewed":
            patterns.append(
                "The primary error source file was never viewed in attempt_1. "
                "Use required_inspections to locate and inspect it before editing."
            )

        # Anti-pattern 3: Viewed-then-dropped
        if len(signals.viewed_then_dropped_files) >= 3:
            patterns.append(
                f"Several files were viewed but not used ({len(signals.viewed_then_dropped_files)} total). "
                "Revisit the viewed-but-dropped files — they may contain relevant "
                "context that was overlooked."
            )

        # Anti-pattern: CDType-specific anti-patterns
        cdtype = contract.context_deficiency_diagnosis.get('primary_cdtype', 'unknown') if hasattr(self, 'contract') else 'unknown'
        # (CDType anti-patterns added when self.contract is set)

        # Anti-pattern: CDType-specific anti-patterns
        cdtype = contract.context_deficiency_diagnosis.get('primary_cdtype', 'unknown') if hasattr(self, 'contract') else 'unknown'
        # (CDType anti-patterns added when self.contract is set)

        # Anti-pattern 4: Default — always include general guardrails
        patterns.append(
            "Do not modify test files unless the fix specifically requires test changes. "
            "Prefer minimal, targeted edits over broad sweeping changes."
        )

        # Check if agent edited many files for a small error
        if signals.unique_files_edited >= 4:
            patterns.append(
                f"Attempt-1 edited {signals.unique_files_edited} files. "
                "Focus on the root cause rather than making changes across many files. "
                "A single-file fix is often sufficient."
            )

        return patterns

    # ------------------------------------------------------------------
    # Validation Target
    # ------------------------------------------------------------------

    def _build_validation_target(self) -> ValidationTarget:
        """Build validation target from failure witness.

        Uses only runtime-visible test command info.
        Constructs the most specific test command available.
        """
        test_cmd = ""
        if self.witness and self.witness.has_failure():
            validation_cmd = self.witness._data.get("validation_command", "")
            if validation_cmd:
                test_cmd = validation_cmd

        # Infer from failing test + stack trace for more specific path
        if not test_cmd:
            failing_test = self._build_failure_summary().failing_test
            if not failing_test:
                return ValidationTarget(
                    test_command=test_cmd,
                    expected_behavior="test passes without error",
                )

            # Try to find the test file path from stack trace frames
            test_file_path = ""
            if self.witness and self.witness.has_failure():
                for frame in self.witness.stack_trace:
                    fpath = frame.get("file", "")
                    fname = fpath.split("/")[-1]
                    if "test" in fname.lower():
                        # Strip /testbed/ prefix for repo-relative path
                        test_file_path = fpath.replace("/testbed/", "", 1)
                        break

            if "::" in failing_test:
                parts = failing_test.split("::")
                if test_file_path:
                    test_cmd = f"python -m pytest {test_file_path}::{parts[-1]} -x"
                else:
                    test_cmd = f"python -m pytest {parts[0]}::{parts[-1]} -x"
            elif test_file_path:
                # Use the test file path + test method
                test_cmd = f"python -m pytest {test_file_path} -k {failing_test} -x"
            else:
                test_cmd = f"python -m pytest {failing_test} -x"

        return ValidationTarget(
            test_command=test_cmd,
            expected_behavior="test passes without error",
        )

    # ------------------------------------------------------------------
    # Evidence Provenance
    # ------------------------------------------------------------------

    def _build_evidence_provenance(
        self, signals: RuntimeSignals
    ) -> EvidenceProvenance:
        """Document the provenance of contract evidence.

        contract_source must be one of:
          failure_witness_signals / trajectory_signal_analysis / issue_driven_deduction
        """
        # Determine contract source based on available evidence
        sources = []
        if self.witness and self.witness.has_failure():
            sources.append("failure_witness_signals")
        else:
            sources.append("trajectory_signal_analysis")

        if self.issue_text:
            sources.append("issue_driven_deduction")

        contract_source = "+".join(sources)

        # Build supporting_artifact reference
        artifact = ""
        if self.witness and self.witness.has_failure():
            # Try to find the failure witness path from the data
            raw_path = self.witness._data.get("raw_output_path", "")
            artifact = raw_path if raw_path else f"failure_witness/{self.parser.instance_id}"

        if not artifact:
            artifact = f"trajectory/{self.parser.instance_id}/traj.json"

        return EvidenceProvenance(
            contract_source=contract_source,
            supporting_artifact=artifact,
        )


# =====================================================================
# Convenience: build contract directly from paths
# =====================================================================


def build_contract(
    traj_path: str | Path,
    witness_path: Optional[str | Path] = None,
    issue_text: str = "",
    signals: Optional[RuntimeSignals] = None,
) -> DiagnosticSearchContract:
    """Build a DiagnosticSearchContract directly from a trajectory path.

    One-shot convenience function:
      1. Parse trajectory
      2. Extract runtime signals (or reuse pre-computed)
      3. Build contract
      4. Return

    Args:
        traj_path: Path to traj.json
        witness_path: Optional path to failure_witness.json
        issue_text: Optional issue/description text
        signals: Pre-computed RuntimeSignals (will compute if not provided)

    Returns:
        DiagnosticSearchContract ready for serialization.
    """
    parser = TrajParser(traj_path)

    if signals is None:
        signals = RuntimeSignals.extract(parser, witness_path)

    builder = DiagnosticSearchContractBuilder(parser, witness_path, issue_text)
    return builder.build(signals)


def contract_to_file(
    contract: DiagnosticSearchContract,
    output_path: str | Path,
) -> None:
    """Serialize a contract to JSON file."""
    with open(output_path, "w") as f:
        json.dump(contract.to_dict(), f, indent=2, ensure_ascii=False)
    print(f"Contract written to {output_path}")


# =====================================================================
# CLI entry point
# =====================================================================


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Build a DiagnosticSearchContract from trajectory + witness"
    )
    parser.add_argument("traj_path", type=str, help="Path to traj.json")
    parser.add_argument("--witness", type=str, default="", help="Path to failure_witness.json")
    parser.add_argument("--issue", type=str, default="", help="Issue text file path")
    parser.add_argument("--output", type=str, default="", help="Output JSON path")
    args = parser.parse_args()

    issue_text = ""
    if args.issue:
        with open(args.issue) as f:
            issue_text = f.read()

    witness_path = args.witness or None
    contract = build_contract(args.traj_path, witness_path, issue_text)

    output = args.output or f"contract_{contract.instance_id}.json"
    contract_to_file(contract, output)
    print(json.dumps(contract.to_dict(), indent=2, ensure_ascii=False))
