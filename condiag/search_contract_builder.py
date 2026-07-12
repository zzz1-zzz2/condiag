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

from condiag.context_deficiency_diagnoser import (
    CDTYPE_API_DEFINITION,
    CDTYPE_CALLER_CALLEE,
    CDTYPE_DEPENDENCY,
    CDTYPE_INTERFACE_CONSTRAINT,
    CDTYPE_REGRESSION_CONSTRAINT,
    CDTYPE_RELATED_TEST,
    CDTYPE_ROOT_CAUSE,
    ContextDeficiencyDiagnoser,
    ContextDeficiencyDiagnosis,
    PatchBehavior,
)
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
    source: str = ""           # "stack_frame" / "failed_test" / "cdtype_driven_action" / etc.
    diagnosis_type: str = ""   # CDType if source="cdtype_driven_action", else ""
    severity: str = ""         # "required" / "recommended" / "informational"
    action_role: str = ""      # "caller" / "callee" / "definition" / "usage" /
                               # "failed_test" / "neighbor_test" /
                               # "interface_producer" / "interface_consumer" /
                               # "error_frame" / "edit_target"
    action_group_id: str = ""  # Shared between paired actions (e.g. "pair_1" for caller+callee)


@dataclass
class RequiredSearch:
    query: str = ""
    type: str = ""  # symbol_definition / grep / caller_search / file_search
    scope: str = ""
    source: str = ""           # "error_symbol" / "issue_term" / "cdtype_driven_action" / etc.
    diagnosis_type: str = ""   # CDType if source="cdtype_driven_action", else ""
    severity: str = ""         # "required" / "recommended" / "informational"
    action_role: str = ""
    action_group_id: str = ""


@dataclass
class ValidationTarget:
    test_command: str = ""
    expected_behavior: str = "test passes without error"


@dataclass
class EvidenceProvenance:
    contract_source: str = ""  # failure_witness_signals / trajectory_signal_analysis / issue_driven_deduction
    supporting_artifact: str = ""


@dataclass
class StructuredConstraint:
    """A structured constraint for agent behavior during attempt-2.

    Each constraint has a concrete type, severity level, and resolved
    parameters. The compliance analyzer evaluates constraints individually
    rather than parsing natural-language anti_patterns.

    Types:
      FORBID_TEST_EDITS            — required  — no params
      REQUIRE_INSPECTION_BEFORE_EDIT — required — {target_path, target_provenance}
      AVOID_REPETITIVE_TEST_RUNS   — recommended — {min_gap_actions}
      REVISIT_DROPPED_FILES        — recommended — {file_paths}
      PREFER_SINGLE_FILE_FIX       — informational — {max_files}
    """
    constraint_type: str = ""
    severity: str = ""              # required / recommended / informational
    parameters: dict = field(default_factory=dict)
    display_text: str = ""          # Natural language for agent rendering


@dataclass
class DiagnosticSearchContract:
    """Structured diagnostic search contract.

    Serializes to the JSON schema defined in condiag_plan_v2_search_contract.md.
    Every field is generated from runtime-only sources — no gold leakage.
    """
    contract_version: str = "1.1"
    instance_id: str = ""
    failure_summary: FailureSummary = field(default_factory=FailureSummary)
    trajectory_signals: TrajectorySignalSnapshot = field(default_factory=TrajectorySignalSnapshot)
    context_deficiency_diagnosis: ContextDeficiencyDiagnosis = field(default_factory=ContextDeficiencyDiagnosis)
    required_inspections: list[RequiredInspection] = field(default_factory=list)
    required_searches: list[RequiredSearch] = field(default_factory=list)
    structured_constraints: list[StructuredConstraint] = field(default_factory=list)
    anti_patterns: list[str] = field(default_factory=list)
    validation_target: ValidationTarget = field(default_factory=ValidationTarget)
    evidence_provenance: EvidenceProvenance = field(default_factory=EvidenceProvenance)
    contract_mode: str = "legacy"  # typed / abstain / degraded / legacy

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
            "context_deficiency_diagnosis": asdict(self.context_deficiency_diagnosis),
            "required_inspections": [asdict(i) for i in self.required_inspections],
            "required_searches": [asdict(s) for s in self.required_searches],
            "structured_constraints": [asdict(sc) for sc in self.structured_constraints],
            "anti_patterns": list(self.anti_patterns),
            "validation_target": asdict(self.validation_target),
            "evidence_provenance": asdict(self.evidence_provenance),
            "contract_mode": self.contract_mode,
        }


def dataclasses_is_dataclass(obj):
    """Check if obj is a dataclass without importing the full inspect module."""
    return hasattr(obj, "__dataclass_fields__")


# =====================================================================
# ContractAnchorResolver — unified anchor resolution
# =====================================================================

class ContractAnchorResolver:
    """Unified resolver for contract anchors from all available sources.

    Parses FailureWitness + TrajParser + RuntimeSignals + issue_text once,
    produces structured anchors that CDType branches select from.

    Centralizes all edge-case parsing (non-Python stacks, import errors,
    panic locations, multi-language test references) so each CDType branch
    only needs to select from pre-resolved anchors -- no inline parsing.

    Outputs:
      files:    [{path, line, func, source, is_test}] -- all known repo files
      symbols:  [{symbol, source}] -- error/issue/assertion symbols
      tests:    [{test_id, source}] -- test references from all sources
      modules:  [{module, attribute, source}] -- import-time module refs
    """

    # Non-Python error location patterns (ordered by specificity)
    _LOCATION_PATTERNS = [
        (r"panicked at\s+'([^']+)':(\d+)", "rust_panic"),
        (r"panicked at\s+([^,\s]+):(\d+)", "rust_panic"),
        (r"at\s+([^\s]+?)\.(?:js|ts|jsx|tsx):(\d+)", "js_location"),
        (r"at\s+([^\s]+?)\.(?:rs|go|rb|php):(\d+)", "source_location"),
        (r"([^\s]+?)\.go:(\d+)", "go_location"),
        (r"([^\s]+?)\.(?:rs|rb|php):(\d+)", "generic_location"),
    ]

    # Noise symbols -- filtered from symbol resolution
    _NOISE_SYMBOLS = frozenset({
        "True", "False", "None", "Error", "Warning",
        "AttributeError", "TypeError", "ValueError", "KeyError",
        "ImportError", "ModuleNotFoundError", "RuntimeError",
        "NameError", "AssertionError", "SyntaxError",
        "IndexError", "StopIteration", "OSError",
        "DeprecationWarning", "PytestConfigWarning",
        "Collecting", "Downloading", "Installing", "Successfully",
        "WARNING", "Running", "FAILED", "ERROR", "Traceback",
        "Skipping", "System", "Docs", "Found", "Total",
        "Short", "Ran", "Unknown", "Warning", "Config",
    })

    _IMPORT_PATTERNS = [
        r"cannot import name\s+'(\w+)'\s+from\s+'([\w.]+)'",
        r"cannot import name\s+(\w+)\s+from\s+([\w.]+)",
        r"module\s+'([\w.]+)'\s+has no attribute\s+'(\w+)'",
        r"'([\w.]+)'\s+has no attribute\s+'(\w+)'",
        r"undefined\s+(?:symbol|name)\s+'?(\w+)'?",
        r"cannot find\s+(?:module|class)\s+'?([\w.]+)'?",
    ]

    def __init__(
        self,
        witness,
        parser,
        signals,
        issue_text: str = "",
    ):
        self.witness = witness
        self.parser = parser
        self.signals = signals
        self.issue_text = issue_text or ""

        # Resolved anchor collections
        self.files: list[dict] = []
        self.symbols: list[dict] = []
        self.tests: list[dict] = []
        self.modules: list[dict] = []

        self._resolve_all()

    def _resolve_all(self):
        self._resolve_stack_files()
        self._resolve_non_python_locations()
        self._resolve_visited_and_edited()
        self._resolve_symbols()
        self._resolve_import_errors()
        self._resolve_tests()

    # ------------------------------------------------------------------
    # File resolution
    # ------------------------------------------------------------------

    def _resolve_stack_files(self):
        """Resolve Python-format stack frames."""
        if not self.witness or not self.witness.has_failure():
            return
        seen_paths = {f["path"] for f in self.files}
        for sf in self.witness.all_stack_source_files():
            fpath = sf.get("fullpath") or sf.get("file", "")
            if fpath and fpath not in seen_paths:
                self.files.append({
                    "path": fpath,
                    "line": sf.get("line", 0),
                    "func": sf.get("func", ""),
                    "source": "stack_frame",
                    "is_test": "test" in fpath.lower(),
                })
                seen_paths.add(fpath)

    def _resolve_non_python_locations(self):
        """Parse non-Python error locations from stack strings + error_message.

        Covers: Rust panicked at, JS/TS at file.js:N, Go file.go:N
        Only fires when no structured frames found (pure fallback).
        """
        if not self.witness or not self.witness.has_failure():
            return
        has_stack_frames = any(f["source"] == "stack_frame" for f in self.files)
        if has_stack_frames:
            return

        raw_st = self.witness._data.get("stack_trace", [])
        msg = self.witness.error_message
        seen_paths = {f["path"] for f in self.files}

        for source_str in list(raw_st) + [msg]:
            if not isinstance(source_str, str):
                continue
            for pat, source_name in self._LOCATION_PATTERNS:
                m = re.search(pat, source_str)
                if m:
                    fpath = m.group(1).strip()
                    line = int(m.group(2))
                    if fpath and fpath not in seen_paths:
                        self.files.append({
                            "path": fpath,
                            "line": line,
                            "func": "",
                            "source": source_name,
                            "is_test": "test" in fpath.lower(),
                        })
                        seen_paths.add(fpath)

    def _resolve_visited_and_edited(self):
        """Resolve files from trajectory visited/edited sources."""
        seen_paths = {f["path"] for f in self.files}
        for fp in sorted(self.parser.visited_files_fullpath(),
                         key=lambda p: (p.count("/"), p)):
            if fp and fp not in seen_paths:
                self.files.append({
                    "path": fp, "line": 0, "func": "",
                    "source": "visited", "is_test": "test" in fp.lower(),
                })
                seen_paths.add(fp)
        for fp in self.parser.edited_files():
            if fp and fp not in seen_paths:
                self.files.append({
                    "path": fp, "line": 0, "func": "",
                    "source": "edited", "is_test": "test" in fp.lower(),
                })
                seen_paths.add(fp)

    # ------------------------------------------------------------------
    # Symbol resolution
    # ------------------------------------------------------------------

    def _resolve_symbols(self):
        """Resolve symbols from error, issue, and assertion sources."""
        seen = {s["symbol"] for s in self.symbols}

        def _add(sym: str, source: str):
            sym = sym.strip()
            if sym and sym not in seen and sym not in self._NOISE_SYMBOLS:
                self.symbols.append({"symbol": sym, "source": source})
                seen.add(sym)

        # Source 1: error_symbols from witness
        if self.witness and self.witness.has_failure():
            for sym in self.witness.error_symbols():
                _add(sym, "error_symbol")

        # Source 2: issue terms from issue_text
        for m in re.finditer(r"\b[A-Z][a-zA-Z0-9_]+\b", self.issue_text):
            term = m.group()
            if len(term) > 4:
                _add(term, "issue_term")

        # Source 3: capitalized terms from error_message (non-noise)
        if self.witness and self.witness.has_failure():
            msg = self.witness.error_message
            for m in re.finditer(r"\b[A-Z][a-zA-Z0-9_]{2,}\b", msg):
                _add(m.group(), "error_term")

    # ------------------------------------------------------------------
    # Module / import-error resolution
    # ------------------------------------------------------------------

    def _resolve_import_errors(self):
        """Parse import-time errors for module/attribute resolution."""
        if not self.witness or not self.witness.has_failure():
            return
        msg = self.witness.error_message
        for pat in self._IMPORT_PATTERNS:
            m = re.search(pat, msg)
            if m:
                groups = m.groups()
                if len(groups) >= 2:
                    self.modules.append({
                        "module": groups[1], "attribute": groups[0],
                        "source": "import_error",
                    })
                elif len(groups) == 1:
                    self.modules.append({
                        "module": "", "attribute": groups[0],
                        "source": "import_error",
                    })
                break  # first match only

    # ------------------------------------------------------------------
    # Test resolution
    # ------------------------------------------------------------------

    def _resolve_tests(self):
        """Resolve test references from all sources, prioritized."""
        if not self.witness:
            return

        def _add(tid: str, source: str):
            if tid and tid not in {t["test_id"] for t in self.tests}:
                self.tests.append({"test_id": tid, "source": source})

        # Source 1: failed_tests list from witness
        if self.witness and self.witness.has_failure():
            for ft in self.witness._data.get("failed_tests", []):
                _add(ft, "failed_tests")

        # Source 2: FAILED line in error_message
        if self.witness and self.witness.has_failure():
            for m in re.finditer(r"FAILED\s+(\S+)", self.witness.error_message):
                _add(m.group(1), "error_message")

        # Source 3: test files from resolved file anchors
        for f in self.files:
            if f["is_test"]:
                _add(f["path"], f["source"])

    # ------------------------------------------------------------------
    # High-level accessors
    # ------------------------------------------------------------------

    def top_error_file(self) -> str:
        """Resolve top error source file with priority chain.

        1. Stack frame files
        2. Non-Python parsed locations (panic/at)
        3. Error_message file:line regex (last resort)
        """
        for f in self.files:
            if f["source"] == "stack_frame":
                return f["path"]
        for f in self.files:
            if f["source"] not in ("visited", "edited"):
                return f["path"]
        if self.witness and self.witness.has_failure():
            m = re.search(r"([^\s]+?)\.\w+:(\d+)", self.witness.error_message)
            if m:
                return m.group(1)
        return ""

    def top_error_file_provenance(self) -> tuple[str, str]:
        """Resolve top error file WITH provenance source.

        Returns (file_path, provenance) where provenance is one of:
          stack_frame, rust_panic, js_location, go_location,
          source_location, generic_location, error_message, unknown
        """
        for f in self.files:
            if f["source"] == "stack_frame":
                return (f["path"], "stack_frame")
        for f in self.files:
            if f["source"] not in ("visited", "edited"):
                return (f["path"], f["source"])
        if self.witness and self.witness.has_failure():
            m = re.search(r"([^\s]+?)\.\w+:(\d+)", self.witness.error_message)
            if m:
                return (m.group(1), "error_message")
        return ("", "unknown")

    def test_file_path(self) -> str:
        """Resolve best test file path from all test references."""
        for t in self.tests:
            tid = t["test_id"]
            if "::" in tid:
                return tid.split("::")[0]
            if ":" in tid:
                return tid.split(":")[0]
            return tid
        return ""

    def test_method_name(self) -> str:
        """Extract test method name from best test reference."""
        for t in self.tests:
            tid = t["test_id"]
            if "::" in tid:
                return tid.split("::")[-1]
            if ":" in tid and not tid.startswith("/"):
                parts = tid.split(":")
                if len(parts) > 1:
                    return parts[-1].strip()
        return ""

    def stack_file_names(self) -> list[str]:
        return [f["path"] for f in self.files if f["source"] == "stack_frame"]

    def has_import_error(self) -> bool:
        return any(m["source"] == "import_error" for m in self.modules)

    def import_modules(self) -> list[str]:
        return [m["module"] for m in self.modules if m.get("module")]

    def import_attributes(self) -> list[str]:
        return [m["attribute"] for m in self.modules if m.get("attribute")]

    def short_to_full_path(self, short_name: str) -> str:
        """Resolve a short filename to its best full path from known files."""
        candidates = [f for f in self.files if f["path"].endswith("/" + short_name)]
        if candidates:
            return candidates[0]["path"]
        return short_name


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
        if issue_text:
            self.issue_text = issue_text
        else:
            self.issue_text = self._extract_issue_from_trajectory()

    def _extract_issue_from_trajectory(self) -> str:
        """Extract the PR description / issue text from trajectory messages.

        The issue is embedded in the first user message inside <pr_description> tags.
        Falls back to the first user message content if no tags found.
        """
        try:
            traj_data = getattr(self.parser, "_data", None)
            if not traj_data:
                return ""
            msgs = traj_data.get("messages", [])
            for msg in msgs:
                if msg.get("role") != "user":
                    continue
                content = msg.get("content", "")
                if not isinstance(content, str):
                    continue
                # Try <pr_description> tag first
                m = re.search(r"<pr_description>\s*(.*?)\s*</pr_description>", content, re.DOTALL)
                if m:
                    return m.group(1).strip()
                # Fallback: use the full message
                return content[:2000].strip()
        except Exception:
            return ""

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
            contract_version="1.1",
            instance_id=self.parser.instance_id,
        )

        contract.failure_summary = self._build_failure_summary()
        contract.trajectory_signals = self._build_signal_snapshot(signals)
        contract.context_deficiency_diagnosis = self._build_diagnosis(signals)
        contract.required_inspections = self._build_required_inspections(signals)
        contract.required_searches = self._build_required_searches(signals)
        # CDType-driven actions: use ContractAnchorResolver for unified anchor resolution
        resolver = ContractAnchorResolver(
            self.witness, self.parser, signals, self.issue_text,
        )
        self._add_cdtype_driven_actions(contract, resolver, contract.context_deficiency_diagnosis)
        # Re-dedup and re-limit after CDType additions
        contract.required_inspections = self._post_process_inspections(contract.required_inspections)
        contract.required_searches = self._post_process_searches(contract.required_searches)
        # Build structured constraints then derive anti_patterns from them
        structured = self._build_structured_constraints(
            signals, contract.context_deficiency_diagnosis, resolver,
        )
        contract.structured_constraints = structured
        contract.anti_patterns = self._build_anti_patterns(
            signals, contract.context_deficiency_diagnosis, structured,
        )
        contract.validation_target = self._build_validation_target()
        contract.evidence_provenance = self._build_evidence_provenance(signals)

        # Set contract_mode based on CDType availability
        diagnosis = contract.context_deficiency_diagnosis
        if diagnosis and diagnosis.primary_cdtype != "unknown":
            contract.contract_mode = "typed"
        else:
            contract.contract_mode = "abstain"

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
    # Context Deficiency Diagnosis (CDType)
    # ------------------------------------------------------------------

    def _compute_patch_behavior(self) -> PatchBehavior:
        """Extract PatchBehavior from Attempt-1 submission patch."""
        patch = self.parser.submission_patch
        if not patch or not patch.strip():
            return PatchBehavior(has_edit=False)

        # Count files edited from diff headers
        files = re.findall(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE)
        files_edited = len(files)

        # Count patch size (lines added + removed)
        added = len(re.findall(r"^\+", patch, re.MULTILINE))
        removed = len(re.findall(r"^\-", patch, re.MULTILINE))
        # Subtract the ---/+++ header lines
        patch_size = max(0, added + removed - files_edited * 2)

        return PatchBehavior(
            has_edit=True,
            files_edited_count=files_edited,
            multi_file_edit=files_edited > 1,
            patch_size=patch_size,
        )

    def _build_diagnosis(
        self, signals: RuntimeSignals
    ) -> ContextDeficiencyDiagnosis:
        """Run CDType diagnosis from error_type + signals + patch behavior."""
        error_type = self.witness.failure_type if self.witness else ""
        error_message = self.witness.error_message if self.witness else ""
        patch_behavior = self._compute_patch_behavior()
        diagnoser = ContextDeficiencyDiagnoser()
        return diagnoser.diagnose(error_type, signals, patch_behavior, error_message=error_message)

    # ------------------------------------------------------------------
    # CDType-driven actions (inspections/searches by CDType profile)
    # ------------------------------------------------------------------

    def _add_cdtype_driven_actions(
        self,
        contract: DiagnosticSearchContract,
        resolver: ContractAnchorResolver,
        diagnosis: ContextDeficiencyDiagnosis,
    ) -> None:
        """Append CDType-specific inspections/searches using resolved anchors.

        CDType determines the *type* of action (the action profile).
        ContractAnchorResolver provides pre-resolved anchors from all sources
        (stack frames, visited/edited files, symbols, tests, import errors).

        Each added action carries:
          source='cdtype_driven_action'
          diagnosis_type=<the CDType>
        """
        cdtype = diagnosis.primary_cdtype
        if cdtype == "unknown":
            return

        # Short-name to full-path resolver for inspection targets
        def _resolve_path(fpath: str) -> str:
            if "/" in fpath or "\\" in fpath:
                return fpath
            return resolver.short_to_full_path(fpath)

        # Counter for paired action_group_id
        _pair_counter = [0]

        def _next_group_id() -> str:
            _pair_counter[0] += 1
            return f"pair_{_pair_counter[0]}"

        def _add_inspection(fpath: str, reason: str, action_role: str = "", action_group_id: str = "", severity: str = "recommended"):
            fpath = _resolve_path(fpath)
            if not fpath:
                return
            contract.required_inspections.append(RequiredInspection(
                file=fpath,
                reason=f"[{cdtype}] {reason}",
                source="cdtype_driven_action",
                diagnosis_type=cdtype,
                severity=severity,
                action_role=action_role,
                action_group_id=action_group_id,
            ))

        def _add_search(query: str, stype: str, reason: str, scope: str = "",
                        action_role: str = "", action_group_id: str = "", severity: str = "recommended"):
            if not query or len(query) < 2:
                return
            contract.required_searches.append(RequiredSearch(
                query=query,
                type=stype,
                scope=scope,
                source="cdtype_driven_action",
                diagnosis_type=cdtype,
                severity=severity,
                action_role=action_role,
                action_group_id=action_group_id,
            ))

        # Convenience: resolved symbols, module attributes, edited short names
        symbols = [s["symbol"] for s in resolver.symbols]
        edited_files = list(self.parser.edited_files())
        edited_short = [f.split("/")[-1] for f in edited_files]

        # === API_DEFINITION_CONTEXT ===
        if cdtype == CDTYPE_API_DEFINITION:
            # Search: symbol definition for known symbols
            pgroup = _next_group_id()
            for sym in symbols[:2]:
                _add_search(sym, "symbol_definition", f"{cdtype}: definition of {sym}",
                            action_role="definition", action_group_id=pgroup)
            # Search: module import / class path
            for sym in symbols[:1]:
                if sym and sym != "<module>":
                    _add_search(sym.split(".")[0], "grep", f"{cdtype}: import/class {sym}",
                                action_role="usage", action_group_id=pgroup)
            # Import-time errors: search for the missing module/attribute
            if resolver.has_import_error():
                for attr in resolver.import_attributes()[:1]:
                    _add_search(attr, "symbol_definition", f"{cdtype}: missing symbol {attr}")
                for mod in resolver.import_modules()[:1]:
                    _add_search(mod, "grep", f"{cdtype}: module/site of {mod}")
            # Inspection: candidate definition file from stack or visited files
            candidates = [f["path"] for f in resolver.files if not f["is_test"]]
            if candidates:
                _add_inspection(
                    candidates[0],
                    f"{cdtype}: candidate definition file for {symbols[0] if symbols else 'error symbol'}",
                )

        # === INTERFACE_CONSTRAINT_CONTEXT ===
        elif cdtype == CDTYPE_INTERFACE_CONSTRAINT:
            pgroup = _next_group_id()
            for sym in symbols[:1]:
                if sym and sym != "<module>":
                    _add_search(sym, "caller_search", f"{cdtype}: callers of {sym}",
                                action_role="caller", action_group_id=pgroup)
                    _add_search(f"{sym}(", "grep", f"{cdtype}: signature/usage of {sym}",
                                action_role="interface_producer", action_group_id=pgroup)
            for f in resolver.stack_file_names()[:1]:
                _add_inspection(
                    f, f"{cdtype}: inspect function signature at error location",
                    action_role="interface_consumer", action_group_id=pgroup,
                )

        # === RELATED_TEST_CONTEXT ===
        elif cdtype == CDTYPE_RELATED_TEST:
            test_file = resolver.test_file_path()
            test_method = resolver.test_method_name()
            pgroup = _next_group_id()
            if test_file:
                _add_inspection(
                    test_file,
                    f"{cdtype}: inspect failed test and neighboring tests",
                    action_role="failed_test", action_group_id=pgroup,
                )
            # Search: assertion terms from resolved symbols
            for sym in symbols[:2]:
                _add_search(sym, "grep", f"{cdtype}: assertion term {sym}",
                            action_role="neighbor_test", action_group_id=pgroup)
            # Search: test method name
            if test_method:
                _add_search(test_method, "grep", f"{cdtype}: test method for assertion",
                            action_role="neighbor_test", action_group_id=pgroup)
            # Fallback: extract assertion expression from error_message
            if not symbols and self.witness and self.witness.has_failure():
                am = re.search(r"assert\s+(.+)", self.witness.error_message)
                if am:
                    for word in re.findall(
                        r"\b([a-zA-Z_]\w+(?:\.\w+)*)\b", am.group(1)
                    ):
                        if len(word) > 2 and word.lower() not in {
                            "is", "not", "in", "true", "false", "none"
                        }:
                            _add_search(word, "grep", f"{cdtype}: assertion term {word}")
                            break

        # === ROOT_CAUSE_RELOCALIZATION ===
        elif cdtype == CDTYPE_ROOT_CAUSE:
            # Search: resolved issue symbols globally
            issue_syms = [s["symbol"] for s in resolver.symbols if s.get("source") == "issue_term"]
            for it in issue_syms[:2]:
                _add_search(it, "grep", f"{cdtype}: root cause term {it}")
            # Search: top stack frame symbol
            if resolver.stack_file_names():
                top_file = resolver.stack_file_names()[0]
                top_stem = Path(top_file).stem
                _add_search(top_stem, "grep", f"{cdtype}: top stack frame symbol")
            # Inspection: top error frame (priority chain handled by resolver)
            top_file = resolver.top_error_file()
            if top_file:
                _add_inspection(
                    top_file, f"{cdtype}: re-inspect top error frame for root cause",
                )

        # === CALLER_CALLEE_CONTEXT ===
        elif cdtype == CDTYPE_CALLER_CALLEE:
            # Search: caller_search for error function
            pgroup = _next_group_id()
            for sym in symbols[:1]:
                if sym and sym != "<module>":
                    _add_search(sym, "caller_search", f"{cdtype}: callers of {sym}",
                                action_role="caller", action_group_id=pgroup)
            # Search: usages of edited file symbol (any language)
            for ef in edited_short[:1]:
                sym = Path(ef).stem
                if sym:
                    _add_search(f"{sym}(", "grep", f"{cdtype}: usage of edited file {ef}",
                                action_role="usage", action_group_id=pgroup)
            # Inspection: edited file definition (no .py restriction)
            for ef in edited_short[:1]:
                _add_inspection(
                    ef, f"{cdtype}: inspect definition of edited symbol {ef}",
                    action_role="callee", action_group_id=pgroup,
                )

        # === REGRESSION_CONSTRAINT_CONTEXT ===
        elif cdtype == CDTYPE_REGRESSION_CONSTRAINT:
            for ef in edited_short[:1]:
                sym = Path(ef).stem
                _add_search(sym, "grep", f"{cdtype}: tests around edited {ef}")
            test_file = resolver.test_file_path()
            if test_file:
                _add_inspection(
                    test_file, f"{cdtype}: inspect newly failing test",
                )
            for sym in symbols[:1]:
                _add_search(sym, "grep", f"{cdtype}: regression term {sym}")

        # === DEPENDENCY_CONTEXT ===
        elif cdtype == CDTYPE_DEPENDENCY:
            # Search: import for module from symbols or import-error modules
            import_targets = symbols[:1] + resolver.import_modules()
            for target in import_targets[:1]:
                if target:
                    _add_search(
                        f"import {target.split('.')[0]}", "grep",
                        f"{cdtype}: dependency import for {target}",
                    )
            # Inspection: __init__.py if in known files
            for f in resolver.files:
                if f["path"].endswith("__init__.py"):
                    _add_inspection(
                        f["path"], f"{cdtype}: inspect module exports",
                    )
                    break
            # Search: config/registration terms in error message
            if self.witness and self.witness.has_failure():
                msg_lower = self.witness.error_message.lower()
                config_terms = {"setup", "config", "registry", "install", "plugin"}
                matching = {t for t in config_terms if t in msg_lower}
                for ct in matching:
                    _add_search(ct, "grep", f"{cdtype}: config term {ct}")

    @staticmethod
    def _post_process_inspections(
        inspections: list[RequiredInspection],
    ) -> list[RequiredInspection]:
        """Re-dedup and re-limit inspections, prioritizing CDType actions."""
        seen: set[str] = set()
        deduped: list[RequiredInspection] = []

        # CDType-driven actions first — they carry diagnostic priority
        cdtype_actions = [i for i in inspections if i.source == "cdtype_driven_action"]
        base_actions = [i for i in inspections if i.source != "cdtype_driven_action"]

        for action_list in [cdtype_actions, base_actions]:
            for insp in action_list:
                key = insp.file
                if not key or key in seen:
                    continue
                seen.add(key)
                deduped.append(insp)

        # Keep up to 10 (expandable to 12 if CDType actions present)
        limit = min(len(inspections), 12) if cdtype_actions else 10
        return deduped[:limit]

    @staticmethod
    def _post_process_searches(
        searches: list[RequiredSearch],
    ) -> list[RequiredSearch]:
        """Re-dedup and re-limit searches, prioritizing CDType actions."""
        seen: set[str] = set()
        deduped: list[RequiredSearch] = []

        cdtype_actions = [s for s in searches if s.source == "cdtype_driven_action"]
        base_actions = [s for s in searches if s.source != "cdtype_driven_action"]

        for action_list in [cdtype_actions, base_actions]:
            for s in action_list:
                key = f"{s.type}:{s.query}"
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(s)

        # Sort: cdtype_driven caller_search/symbol_definition first, then by type
        deduped.sort(key=lambda x: (
            0 if (x.source == "cdtype_driven_action" and x.type in ("caller_search", "symbol_definition")) else
            1 if x.type == "symbol_definition" else
            2 if x.type == "caller_search" else
            3
        ))

        limit = min(len(searches), 12) if cdtype_actions else 10
        return deduped[:limit]

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

        def _add(filepath: str, line: int, reason: str, severity: str = "required"):
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
                severity=severity,
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
        # Build short-name → full-path map to resolve ambiguous short names
        short_to_full: dict[str, str] = {}
        for full_path in sorted(self.parser.visited_files_fullpath(), key=lambda p: (p.count("/"), p)):
            short = full_path.split("/")[-1]
            if short not in short_to_full:
                short_to_full[short] = full_path
        # Also add stack trace frames' full paths (from witness)
        if self.witness and self.witness.has_failure():
            for frame in self.witness.stack_trace:
                fpath = frame.get("file", "")
                if fpath:
                    short = fpath.split("/")[-1]
                    if short not in short_to_full:
                        short_to_full[short] = fpath

        for dropped in signals.viewed_then_dropped_files:
            # Parse out the filename (may have " (stack frame, unvisited)" suffix)
            fname = dropped.split(" (stack")[0].strip()
            if fname:
                # Resolve short name to full path when available
                full = short_to_full.get(fname, fname)
                _add(full, 0, "viewed but not utilized by agent", severity="recommended")

        # Source 5: Edited files not in stack trace
        if self.witness and self.witness.has_failure():
            stack_file_names = {sf["file"] for sf in self.witness.all_stack_source_files()}
            for ef in self.parser.edited_files():
                ef_short = ef.split("/")[-1]
                if ef_short not in stack_file_names:
                    # Check if it's a source file (not test)
                    if "test" not in ef_short.lower():
                        _add(ef, 0, "edited file (not in error stack — verify relevance)", severity="recommended")

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

        def _add(query: str, typ: str, scope: str = "", severity: str = "recommended"):
            if query and query not in seen_queries:
                seen_queries.add(query)
                searches.append(RequiredSearch(
                    query=query,
                    type=typ,
                    scope=scope,
                    severity=severity,
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
            # Environment variable terms — useless as code search terms
            _ENV_VAR_PREFIXES = ("CONDA_", "PYTHON_", "PIP_", "LD_", "DYLD_", "PKG_", "LC_")
            _ENV_VAR_EXACT = {"CPython", "PYTHONHASHSEED", "HOSTNAME", "PLATFORM"}
            # Python built-in constants — too generic for grep
            _PYTHON_KEYWORDS = {"True", "False", "None"}
            def _is_noise(term: str) -> bool:
                if term in _NOISE_TERMS:
                    return True
                if term.endswith("Warning"):
                    return True
                if term in _PYTHON_KEYWORDS:
                    return True
                if term in _ENV_VAR_EXACT:
                    return True
                if any(term.startswith(p) for p in _ENV_VAR_PREFIXES):
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

    # ------------------------------------------------------------------
    # Structured Constraints
    # ------------------------------------------------------------------

    def _build_structured_constraints(
        self,
        signals: RuntimeSignals,
        diagnosis: Optional[ContextDeficiencyDiagnosis] = None,
        resolver: Optional[ContractAnchorResolver] = None,
    ) -> list[StructuredConstraint]:
        """Build structured constraints from trajectory signals and CDType.

        Each constraint has a concrete type, severity, and resolved parameters.
        The compliance analyzer evaluates these individually rather than
        parsing natural-language anti_patterns.
        """
        constraints: list[StructuredConstraint] = []

        # FORBID_TEST_EDITS — blanket rule for all instances
        constraints.append(StructuredConstraint(
            constraint_type="FORBID_TEST_EDITS",
            severity="required",
            parameters={},
            display_text=(
                "Do not modify test files unless the fix specifically "
                "requires test changes. Prefer minimal, targeted edits "
                "over broad sweeping changes."
            ),
        ))

        # REQUIRE_INSPECTION_BEFORE_EDIT — when error file was never viewed
        if signals.error_edit_alignment == "error_file_never_viewed":
            target_path = ""
            target_provenance = "stack_frame"
            if resolver is not None:
                target_path, target_provenance = resolver.top_error_file_provenance()
            elif self.witness and self.witness.has_failure():
                target_path = self.witness.top_error_file_fullpath()
            constraints.append(StructuredConstraint(
                constraint_type="REQUIRE_INSPECTION_BEFORE_EDIT",
                severity="required",
                parameters={
                    "target_path": target_path,
                    "target_provenance": target_provenance,
                },
                display_text=(
                    "The primary error source file was never viewed "
                    "in attempt_1. Start by inspecting the error source "
                    "file at the failing line, then trace the call to "
                    "understand what needs to change."
                ),
            ))

        # AVOID_REPETITIVE_TEST_RUNS — when oscillating
        if signals.exploration_mode == "oscillating":
            constraints.append(StructuredConstraint(
                constraint_type="AVOID_REPETITIVE_TEST_RUNS",
                severity="recommended",
                parameters={"min_gap_actions": 1},
                display_text=(
                    "Avoid repetitive test runs without intervening edits. "
                    "Each test run should follow a specific code change. "
                    "If a test fails, inspect the error location before re-running."
                ),
            ))

        # REVISIT_DROPPED_FILES — when ≥3 files viewed but not used
        if len(signals.viewed_then_dropped_files) >= 3:
            file_paths = signals.viewed_then_dropped_files[:5]
            constraints.append(StructuredConstraint(
                constraint_type="REVISIT_DROPPED_FILES",
                severity="recommended",
                parameters={"file_paths": file_paths},
                display_text=(
                    f"Several files were viewed but not used "
                    f"({len(signals.viewed_then_dropped_files)} total). "
                    "Revisit the viewed-but-dropped files — they may "
                    "contain relevant context that was overlooked."
                ),
            ))

        # PREFER_SINGLE_FILE_FIX — when many files were edited
        if signals.unique_files_edited >= 4:
            constraints.append(StructuredConstraint(
                constraint_type="PREFER_SINGLE_FILE_FIX",
                severity="informational",
                parameters={"max_files": 1},
                display_text=(
                    f"Attempt-1 edited {signals.unique_files_edited} files. "
                    "Focus on the root cause rather than making changes "
                    "across many files. A single-file fix is often sufficient."
                ),
            ))

        return constraints

    # ------------------------------------------------------------------
    # Anti-Patterns (derived from structured_constraints + extra patterns)
    # ------------------------------------------------------------------

    def _build_anti_patterns(
        self,
        signals: RuntimeSignals,
        diagnosis: Optional[ContextDeficiencyDiagnosis] = None,
        structured_constraints: Optional[list[StructuredConstraint]] = None,
    ) -> list[str]:
        """Generate anti-pattern warnings from constraints, CDType, and signals.

        Derives text from structured_constraints.display_text first, then
        adds CDType-specific guidance and trajectory-specific patterns that
        are not constraint-mapped.
        """
        patterns: list[str] = []

        # Phase 1: Derive from structured_constraints.display_text
        if structured_constraints:
            for sc in structured_constraints:
                if sc.display_text:
                    patterns.append(sc.display_text)

        # Phase 2: CDType-specific guidance (informational, not a constraint)
        if diagnosis and diagnosis.primary_cdtype != "unknown":
            cdtype_patterns = {
                CDTYPE_API_DEFINITION: (
                    "Look up class/function definitions before editing. "
                    "Use symbol_definition search for unknown APIs."
                ),
                CDTYPE_INTERFACE_CONSTRAINT: (
                    "Check function signatures and type expectations before calling. "
                    "Use caller_search to find usage patterns."
                ),
                CDTYPE_RELATED_TEST: (
                    "Run the specific failing test, not full test suite. "
                    "Check test assertions for expected behavior."
                ),
                CDTYPE_CALLER_CALLEE: (
                    "Trace the call chain from test to error location. "
                    "Don't jump between files without understanding the call flow."
                ),
                CDTYPE_ROOT_CAUSE: (
                    "The edit location was aligned but the fix was incomplete. "
                    "Look deeper at the root cause near the error location."
                ),
                CDTYPE_REGRESSION_CONSTRAINT: (
                    "Preserve existing behavior. "
                    "Check that other tests depending on this code still pass."
                ),
                CDTYPE_DEPENDENCY: (
                    "Verify imports, __init__.py exports, and dependency setup. "
                    "Check that the required module is installed and importable."
                ),
            }
            msg = cdtype_patterns.get(diagnosis.primary_cdtype)
            if msg:
                patterns.append(
                    f"[{diagnosis.primary_cdtype}] {msg}"
                )

        # Phase 3: Non-constraint trajectory patterns

        # Jumping — general guidance, not a constraint
        if signals.exploration_mode == "jumping":
            patterns.append(
                "Limit file browsing. Investigate the specific error "
                "(stack trace, assertion failure) before opening new files. "
                "Use the required_inspections list as your starting point."
            )

        # Viewed-not-edited — weaker form of REQUIRE_INSPECTION_BEFORE_EDIT
        if signals.error_edit_alignment == "viewed_not_edited":
            patterns.append(
                "The stack trace files were viewed but not modified in attempt_1. "
                "Start by inspecting the error source file at the failing line, "
                "then trace the call to understand what needs to change."
            )

        # Edited-elsewhere — general guidance about focus
        if signals.error_edit_alignment == "edited_elsewhere":
            patterns.append(
                "The edit location in attempt_1 does not align with the error location. "
                "The correct file was edited but at a different function or line. "
                "Use required_inspections to find the exact error location before editing."
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
    with open(output_path, "w", encoding="utf-8") as f:
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
        with open(args.issue, encoding="utf-8", errors="replace") as f:
            issue_text = f.read()

    witness_path = args.witness or None
    contract = build_contract(args.traj_path, witness_path, issue_text)

    output = args.output or f"contract_{contract.instance_id}.json"
    contract_to_file(contract, output)
    print(json.dumps(contract.to_dict(), indent=2, ensure_ascii=False))
