"""Trajectory Signals — extract diagnostic signals from mini-SWE-agent trajectories.

Two output namespaces:
  - runtime_signals: signals visible at runtime (enter contract / agent input)
  - oracle_audit:    gold-based signals (eval-only, NOT for runtime/contract)

Runtime signals are rule-based, deterministic, zero LLM cost.
Oracle audit signals are for offline analysis only — NEVER feed into
search_contract_builder, diagnosis_generator, or retry prompts.

Usage:
    from condiag.trajectory_signals import TrajParser, RuntimeSignals, OracleAudit

    parser = TrajParser(traj_path)
    signals = RuntimeSignals.extract(parser, failure_witness_path)
    audit = OracleAudit.extract(parser, contextbench_gold_path)

Rule 5 (Contract Provenance): runtime_signals ONLY.
Rule 1 (Gold Leakage): oracle_audit NEVER enters runtime/contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# -- Mutation detection patterns (for edit timing) --
# These regex patterns detect file-mutation commands in trajectory assistant messages.
# Used ONLY for edit timing, NOT for inspection evidence.

_SED_MUTATION_RE = re.compile(
    r"sed\s+-i(?:\s+\S+)?\s+(?:'[^']*'|\"[^\"]*\")\s+(\S+)"
)
_CAT_WRITE_RE = re.compile(
    r"cat\s+(?:>\||>>|>)\s*(\S+)"
)
_ECHO_WRITE_RE = re.compile(
    r"(?:echo|printf)\s+['\"].*?['\"]\s+(?:>\||>>|>)\s*(\S+)"
)
_PYTHON_WRITE_RE = re.compile(
    r"""python\s+-c\s+['\"].*?open\(['\"](\S+)['\"]\s*,\s*['\"]w['\"]"""
)
_STRUCTURED_TOOL_RE = re.compile(
    r"(?:apply_patch|write_file|replace)\s+(\S+)"
)


# =====================================================================
# TrajParser — low-level trajectory access
# =====================================================================

class TrajParser:
    """Parse and provide structured access to a mini-SWE-agent traj.json."""

    def __init__(self, traj_path: str | Path):
        with open(traj_path, encoding="utf-8", errors="replace") as f:
            self._raw = json.load(f)
        self._parse_messages()

    def _parse_messages(self):
        """Index messages by role and extract structured events."""
        self.messages = self._raw.get("messages", [])
        self._explore_events: list[dict] = []
        self._bash_commands: list[dict] = []
        self._test_commands: list[dict] = []
        self._checkout_events: list[int] = []
        self._mutation_events: list[dict] = []

        for i, msg in enumerate(self.messages):
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "assistant":
                self._parse_assistant(i, content)
            elif role == "user":
                self._parse_user(i, content)

    def _parse_assistant(self, idx: int, content: str):
        """Extract bash commands, test runs, checkouts, mutation events."""
        cmds = self._extract_commands(content)

        for cmd in cmds:
            entry = {"step": idx, "cmd": cmd}
            self._bash_commands.append(entry)

            # Test commands
            if any(kw in cmd for kw in ["pytest", "python -m pytest",
                                        "python -m django", "tox"]):
                self._test_commands.append(entry)

            # Git checkouts
            if "git checkout" in cmd and "--" not in cmd:
                self._checkout_events.append(idx)

        # Detect mutations from all commands
        for cmd in cmds:
            self._detect_mutations(idx, cmd)

    def _extract_commands(self, content: str) -> list[str]:
        """Extract all commands from assistant message content.

        Handles both ```bash code blocks and <command> tags inside
        <tool_calls> blocks.
        """
        cmds = []
        for match in re.finditer(r"```bash\s*\n(.+?)```", content, re.DOTALL):
            cmds.append(match.group(1).strip())
        for match in re.finditer(r"<command>(.+?)</command>", content, re.DOTALL):
            cmds.append(match.group(1).strip())
        return cmds

    def _detect_mutations(self, step: int, cmd: str):
        """Detect file mutation events from a command string.

        Detects: sed -i, cat >, echo/printf >, python file writes,
        and structured tools (apply_patch/write_file/replace).
        Matches against module-level compiled regex patterns.
        """
        cmd_trunc = cmd[:300]

        for m in _SED_MUTATION_RE.finditer(cmd):
            self._mutation_events.append({
                "step": step, "file": m.group(1),
                "type": "sed_replace", "command": cmd_trunc,
            })

        for m in _CAT_WRITE_RE.finditer(cmd):
            self._mutation_events.append({
                "step": step, "file": m.group(1),
                "type": "cat_write", "command": cmd_trunc,
            })

        for m in _ECHO_WRITE_RE.finditer(cmd):
            self._mutation_events.append({
                "step": step, "file": m.group(1),
                "type": "echo_write", "command": cmd_trunc,
            })

        for m in _PYTHON_WRITE_RE.finditer(cmd):
            self._mutation_events.append({
                "step": step, "file": m.group(1),
                "type": "python_write", "command": cmd_trunc,
            })

        for m in _STRUCTURED_TOOL_RE.finditer(cmd):
            self._mutation_events.append({
                "step": step, "file": m.group(1),
                "type": "structured_tool", "command": cmd_trunc,
            })

    def _parse_user(self, idx: int, content: str):
        """Extract explore_context events from user (tool output) messages."""
        for match in re.finditer(
            r"(?i)<explore_context>\s*File:\s*(\S+)\s*Lines:\s*(\d+)-(\d+)",
            content,
        ):
            self._explore_events.append({
                "step": idx,
                "file": match.group(1),
                "line_start": int(match.group(2)),
                "line_end": int(match.group(3)),
            })

    # -- Accessors --

    @property
    def instance_id(self) -> str:
        return self._raw.get("instance_id", "")

    @property
    def exit_status(self) -> str:
        return self._raw.get("info", {}).get("exit_status", "")

    @property
    def submission_patch(self) -> str:
        return self._raw.get("info", {}).get("submission", "")

    @property
    def api_calls(self) -> int:
        return self._raw.get("info", {}).get("model_stats", {}).get("api_calls", 0)

    @property
    def total_steps(self) -> int:
        return len(self._bash_commands)

    def visited_files(self) -> set[str]:
        """Set of filenames (short) the agent viewed via explore_context."""
        return {e["file"].split("/")[-1] for e in self._explore_events}

    def visited_files_fullpath(self) -> set[str]:
        """Set of full paths the agent viewed via explore_context."""
        return {e["file"] for e in self._explore_events}

    def explore_events(self) -> list[dict]:
        return list(self._explore_events)

    def bash_commands(self) -> list[dict]:
        return list(self._bash_commands)

    def test_commands(self) -> list[dict]:
        return list(self._test_commands)

    def checkout_count(self) -> int:
        return len(self._checkout_events)

    def edited_files(self) -> list[str]:
        """Extract edited file paths from the submission patch diff."""
        patch = self.submission_patch
        files = []
        for match in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE):
            files.append(match.group(1))
        return files

    # -- Mutation Events (for edit timing, NOT inspection evidence) --

    def mutation_events(self) -> list[dict]:
        """Return list of file-mutation events from trajectory messages.

        Each event: {"step": int, "file": str, "type": str, "command": str}
        Mutation types: sed_replace, cat_write, echo_write,
                        python_write, structured_tool.
        Used ONLY for edit timing in contract compliance analysis.
        """
        return list(self._mutation_events)

    def first_edit_step(self) -> int | None:
        """Return the step index of the first detected mutation event."""
        if not self._mutation_events:
            return None
        return min(e["step"] for e in self._mutation_events)

    def last_edit_step(self) -> int | None:
        """Return the step index of the last detected mutation event."""
        if not self._mutation_events:
            return None
        return max(e["step"] for e in self._mutation_events)

    def edited_files_by_step(self) -> dict[int, list[str]]:
        """Return dict mapping step indices to files edited at that step."""
        by_step: dict[int, list[str]] = {}
        for e in self._mutation_events:
            by_step.setdefault(e["step"], []).append(e["file"])
        return by_step

    def mutation_timing_reliable(self) -> bool:
        """Check if mutation_events covers all files in the final patch.

        Returns True when every file in edited_files() was detected as a
        mutation event. A mismatch means some edits happened without
        detectable mutation events, so before-edit / after-final-edit
        timing for any result becomes UNDETERMINED.
        """
        mutated = {self._normalize_path(e["file"]) for e in self._mutation_events}
        patch_files = set(self.edited_files())
        if not patch_files:
            return True
        return patch_files.issubset(mutated)

    def unmatched_final_patch_files(self) -> list[str]:
        """Files in final patch but not detected as mutation events."""
        mutated = {self._normalize_path(e["file"]) for e in self._mutation_events}
        patch_files = set(self.edited_files())
        return sorted(patch_files - mutated)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """Normalize a file path to repo-relative form.

        Strips known prefixes (/testbed/, /workspace/, ./),
        normalizes backslashes to forward slashes.
        """
        path = path.replace("\\", "/")
        for prefix in ["/testbed/", "/workspace/", "./"]:
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        return path


# =====================================================================
# FailureWitnessLoader — load structured failure info
# =====================================================================

class FailureWitnessLoader:
    """Load and parse a FailureWitness JSON file."""

    def __init__(self, path: str | Path):
        with open(path, encoding="utf-8", errors="replace") as f:
            self._data = json.load(f)

    @property
    def instance_id(self) -> str:
        return self._data.get("instance_id", "")

    @property
    def failure_type(self) -> str:
        return self._data.get("failure_type", "")

    @property
    def error_message(self) -> str:
        return self._data.get("error_message", "")

    @property
    def stack_trace(self) -> list[dict]:
        st = self._data.get("stack_trace", [])
        if st:
            # Handle string-format stack traces (flat list of traceback lines)
            if isinstance(st[0], str):
                return self._parse_string_stack_trace(st)
            return st
        # Fallback: try to extract stack frames from error_message text
        return self._parse_stack_from_error_message()

    def _parse_string_stack_trace(self, lines: list[str]) -> list[dict]:
        """Parse a list of traceback strings into structured dict frames.

        Preserves ALL frames (no dedup — dedup is the responsibility of
        callers like _build_required_inspections, which use full path keys).
        """
        frames = []
        for line in lines:
            # Standard:   File "path/to/file.py", line N, in func
            m = re.match(r'\s*File "([^"]+)", line (\d+)(?:, in (\S+))?', line)
            if m:
                fpath = m.group(1)
                is_repo = not fpath.startswith("/opt/") and not fpath.startswith("/usr/")
                frames.append({
                    "file": fpath,
                    "line": int(m.group(2)),
                    "func": m.group(3) or "",
                    "repo_frame": is_repo,
                })
        if frames:
            return frames
        # If regex didn't match, try the error_message fallback
        return self._parse_stack_from_error_message()

    def _parse_stack_from_error_message(self) -> list[dict]:
        """Fallback: extract stack frames from the error_message text."""
        err = self._data.get("error_message", "")
        if not err:
            return []
        frames = []
        # Fallback 1: standard Python traceback lines
        for match in re.finditer(
            r'  File "([^"]+)", line (\d+), in (\S+)', err
        ):
            fpath = match.group(1)
            is_repo = not fpath.startswith("/opt/") and not fpath.startswith("/usr/")
            frames.append({
                "file": fpath,
                "line": int(match.group(2)),
                "func": match.group(3),
                "repo_frame": is_repo,
            })
        if frames:
            return frames
        # Fallback 2: pytest FAILED line (contains test file and test name)
        for match in re.finditer(
            r"FAILED\s+(\S+)::(\S+)", err
        ):
            fpath = match.group(1)
            frames.append({
                "file": fpath if "/" in fpath else f"tests/{fpath}",
                "line": 0,
                "func": match.group(2),
                "repo_frame": True,
            })
        return frames

    @property
    def top_repo_frames(self) -> list[dict]:
        trf = self._data.get("top_repo_frames", [])
        if trf and isinstance(trf[0], str):
            return self._parse_string_stack_trace(trf)
        return trf

    def has_failure(self) -> bool:
        return self._data.get("has_failure_witness", False)

    def top_error_file(self) -> str:
        """Short filename of the top repo frame in stack trace."""
        frames = self.top_repo_frames
        if not frames:
            return ""
        return frames[0].get("file", "").split("/")[-1]

    def top_error_file_fullpath(self) -> str:
        """Full path of the top repo frame."""
        frames = self.top_repo_frames
        if not frames:
            return ""
        return frames[0].get("file", "")

    def all_stack_source_files(self) -> list[dict]:
        """All unique non-test repo files from stack trace, sorted by proximity to root cause.

        Returns list of dicts with 'file' (short), 'fullpath', 'line', 'func'.
        Test files are deprioritized — source files come first.
        """
        seen = set()
        files = []
        for frame in self.stack_trace:
            fpath = frame.get("file", "")
            fname = fpath.split("/")[-1]
            if fname in seen:
                continue
            seen.add(fname)
            files.append({
                "file": fname,
                "fullpath": fpath,
                "line": frame.get("line", 0),
                "func": frame.get("func", ""),
                "is_test": "test" in fname.lower(),
            })
        # Sort: non-test first, then by line number
        files.sort(key=lambda x: (x["is_test"], x["line"]))
        return files

    def error_symbols(self) -> list[str]:
        """Function/class names from stack trace frames or error message.

        When stack trace frames don't include function names (e.g., Rust
        or abbreviated Python traces), falls back to extracting likely
        symbol names from the error_message text.
        """
        symbols = []
        for frame in self.stack_trace:
            func = frame.get("func", "")
            if func and func not in symbols:
                symbols.append(func)
        if symbols:
            return symbols
        # Fallback: extract capitalized symbols from error_message
        return self._error_symbols_from_message()

    def _error_symbols_from_message(self) -> list[str]:
        """Extract likely symbol names from error_message as fallback."""
        import re
        err = self._data.get("error_message", "")
        if not err:
            return []
        symbols = []
        seen = set()
        # Look for capitalized or dotted names (TypeError, module.Class, etc.)
        for m in re.finditer(r"\b[A-Z][a-zA-Z0-9_]*(?:\.[a-zA-Z_]\w*)*", err):
            name = m.group()
            # Skip noise words and single chars
            if len(name) <= 2 or name in _ERROR_MSG_NOISE:
                continue
            if name not in seen:
                seen.add(name)
                symbols.append(name)
        return symbols[:6]


_ERROR_MSG_NOISE = frozenset({
    "True", "False", "None", "Error", "Warning", "Exception",
    "Std", "Linux", "Windows", "Mac", "OSError", "Errno",
    "Python", "File", "Line", "Raised", "During", "Handling",
    "Collecting", "Installing", "Downloading", "Successfully",
    "Short", "Total", "Config", "Found", "Ran", "Docs",
})


# =====================================================================
# RuntimeSignals — diagnostic signals visible at runtime
# =====================================================================

@dataclass
class RuntimeSignals:
    """Diagnostic signals extracted from trajectory at runtime.

    All fields use ONLY runtime-visible evidence (traj, patch, failure witness).
    NO gold data enters this namespace.
    """

    # -- Error-Visit Alignment --
    # Did the agent visit the stack trace files?
    error_visit_alignment: str = "unknown"  # visited_error_file / visited_stack_frame / visited_none / unknown

    # -- Error-Edit Alignment (4-layer) --
    error_edit_file_level: bool = False       # top error file was edited
    error_edit_symbol_level: bool = False     # error function/class was edited
    error_edit_line_window: bool = False      # edit within ±N lines of error
    error_edit_term_overlap: bool = False     # error/issue terms in edited span
    error_edit_alignment: str = "unknown"     # aggregated: aligned / viewed_not_edited / edited_elsewhere / error_file_never_viewed / unknown

    # -- Exploration Mode --
    exploration_mode: str = "unknown"         # focused / oscillating / jumping / shallow_scan

    # -- Exploration numeric signals --
    total_bash_commands: int = 0
    total_test_commands: int = 0
    total_checkouts: int = 0
    total_api_calls: int = 0
    unique_files_visited: int = 0
    unique_files_edited: int = 0

    # -- Test Behavior --
    test_runs: int = 0
    has_regression_signal: bool = False

    # -- Viewed-then-Dropped Evidence --
    viewed_then_dropped_files: list[str] = field(default_factory=list)

    # -- Versioning --
    signal_version: str = "1.0"

    @classmethod
    def extract(
        cls,
        parser: TrajParser,
        witness_path: Optional[str | Path] = None,
    ) -> RuntimeSignals:
        """Extract all runtime signals from a parsed trajectory.

        Args:
            parser: Initialized TrajParser for the trajectory.
            witness_path: Optional path to FailureWitness JSON.
        """
        signals = cls()
        signals.total_bash_commands = parser.total_steps
        signals.total_test_commands = len(parser.test_commands())
        signals.total_checkouts = parser.checkout_count()
        signals.total_api_calls = parser.api_calls
        signals.unique_files_visited = len(parser.visited_files())
        signals.unique_files_edited = len(parser.edited_files())

        # Test behavior
        signals.test_runs = len(parser.test_commands())

        # Load failure witness
        witness = None
        if witness_path and Path(witness_path).exists():
            witness = FailureWitnessLoader(witness_path)

        # Error-visit alignment
        signals.error_visit_alignment = cls._compute_error_visit(parser, witness)

        # Error-edit alignment (4-layer)
        ee = cls._compute_error_edit_alignment(parser, witness)
        signals.error_edit_file_level = ee["file_level"]
        signals.error_edit_symbol_level = ee["symbol_level"]
        signals.error_edit_line_window = ee["line_window"]
        signals.error_edit_term_overlap = ee["term_overlap"]
        signals.error_edit_alignment = ee["aggregated"]

        # Exploration mode
        signals.exploration_mode = cls._compute_exploration_mode(signals)

        # Viewed-then-dropped
        signals.viewed_then_dropped_files = cls._compute_viewed_then_dropped(
            parser, witness
        )

        return signals

    # ------------------------------------------------------------------
    # Error-Visit Alignment
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_error_visit(
        parser: TrajParser,
        witness: Optional[FailureWitnessLoader],
    ) -> str:
        """Check if agent visited any stack trace files.

        Returns:
            visited_error_file: visited the primary source file from stack
            visited_stack_frame: visited some stack frame file (but not the primary source)
            visited_none: no stack frame file was visited
            unknown: no failure witness
        """
        if witness is None or not witness.has_failure():
            return "unknown"

        visited = parser.visited_files()
        stack_sources = witness.all_stack_source_files()

        if not stack_sources:
            return "unknown"

        # Check if any non-test source file was visited
        source_visited = False
        frame_visited = False
        for sf in stack_sources:
            fname = sf["file"]
            if fname in visited:
                if not sf["is_test"]:
                    source_visited = True
                else:
                    frame_visited = True

        if source_visited:
            return "visited_error_file"
        if frame_visited:
            return "visited_stack_frame"
        return "visited_none"

    # ------------------------------------------------------------------
    # Error-Edit Alignment (4-layer)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_error_edit_alignment(
        parser: TrajParser,
        witness: Optional[FailureWitnessLoader],
    ) -> dict:
        """4-layer alignment between error location and edit location.

        Checks ALL stack trace source files (not just the top frame),
        prioritizing non-test source files.

        Returns dict with file_level, symbol_level, line_window,
        term_overlap bools and aggregated enum string.
        """
        result = {
            "file_level": False,
            "symbol_level": False,
            "line_window": False,
            "term_overlap": False,
            "aggregated": "unknown",
        }

        if witness is None or not witness.has_failure():
            return result

        edited = parser.edited_files()
        edited_short = {f.split("/")[-1] for f in edited}
        visited_short = parser.visited_files()
        stack_sources = witness.all_stack_source_files()

        if not stack_sources:
            return result

        # Find the best-matching stack source file for the edit
        best_source = None  # The stack source file that best matches edited files
        for sf in stack_sources:
            fname = sf["file"]
            if fname in edited_short:
                best_source = sf
                result["file_level"] = True
                break
            # Check partial match (e.g. base.py matches django/db/models/base.py)
            for ef in edited:
                if ef.endswith(fname):
                    best_source = sf
                    result["file_level"] = True
                    break
            if result["file_level"]:
                break

        # If no file-level alignment, check if at least visited
        if not result["file_level"]:
            unvisited = []
            for sf in stack_sources:
                fname = sf["file"]
                if fname in visited_short:
                    unvisited.append(fname)
                elif not sf["is_test"]:
                    # Check if any visited file is a partial match
                    for vf in visited_short:
                        if vf.endswith(fname) or fname.endswith(vf):
                            unvisited.append(fname)
                            break
            if unvisited:
                result["aggregated"] = "viewed_not_edited"
            else:
                result["aggregated"] = "error_file_never_viewed"
            return result

        # Layer 2: Symbol-level alignment
        error_symbols = witness.error_symbols()
        patch = parser.submission_patch
        for symbol in error_symbols:
            # Check in patch context (hunk headers, added lines)
            if symbol in patch:
                result["symbol_level"] = True
                break

        # Layer 3: Line-window alignment
        edit_lines = set()
        for match in re.finditer(
            r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", patch, re.MULTILINE
        ):
            edit_lines.add(int(match.group(1)))

        LINE_WINDOW = 50  # ±50 lines — accounts for function-level proximity
        best_line = best_source["line"] if best_source else None
        if best_line and edit_lines:
            for el in edit_lines:
                if abs(el - best_line) <= LINE_WINDOW:
                    result["line_window"] = True
                    break

        # Layer 4: Term overlap (error/issue terms in patch)
        error_msg = witness.error_message
        error_terms = set()
        for term in re.finditer(r"\b[A-Z][a-zA-Z0-9_]+(?:\.[a-zA-Z]+)?\b", error_msg):
            if len(term.group()) > 3:
                error_terms.add(term.group())

        patch_context = patch[:2000]
        overlap_count = 0
        for term in error_terms:
            if term.lower() in patch_context.lower():
                overlap_count += 1
        result["term_overlap"] = overlap_count >= 2

        # Aggregate
        # "aligned" requires file + (symbol OR line) — strong location evidence
        # "edited_elsewhere" = right file but wrong location (term_overlap
        #   alone doesn't indicate aligned targeting — it only means error
        #   terminology appears in the patch, which can happen coincidentally)
        if result["file_level"] and (result["symbol_level"] or result["line_window"]):
            result["aggregated"] = "aligned"
        elif result["file_level"]:
            result["aggregated"] = "edited_elsewhere"
        # else stays unknown (was determined in the no-file-level section above)

        return result

    # ------------------------------------------------------------------
    # Exploration Mode
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_exploration_mode(signals: RuntimeSignals) -> str:
        """Classify exploration mode from numeric signals.

        Heuristics based on file switching breadth vs depth:
        - shallow_scan:  very few steps, no testing
        - focused:       moderate steps, few files edited, some testing
        - oscillating:   many test reruns (cycling without progress),
                         or many edits without corresponding visits
        - jumping:       many files visited but few tests (browsing without focus)

        Thresholds validated on 52 trajectories (2026-07-08).
        """
        b = signals.total_bash_commands
        t = signals.total_test_commands
        c = signals.total_checkouts
        v = signals.unique_files_visited
        e = signals.unique_files_edited

        if b <= 5 and t == 0:
            return "shallow_scan"

        if c >= 2:
            return "oscillating"

        # High test cycling → oscillating
        if t >= 5:
            return "oscillating"

        # Moderate test cycling + broad visits → oscillating
        if t >= 4 and v >= 5:
            return "oscillating"

        # Editing many files without viewing them → suspicious
        if e >= 5 and v <= 3:
            return "oscillating"

        # Broad exploration without testing → jumping
        if v >= 8 and t <= 1:
            return "jumping"

        # High file:edit ratio → jumping
        if v >= 5 and e <= 1 and t <= 2:
            return "jumping"

        # Focused: low files, reasonable tests
        if v <= 5 and e <= 2:
            return "focused"

        # Default: focused (most agents are reasonably targetted)
        return "focused"

    # ------------------------------------------------------------------
    # Viewed-then-Dropped Evidence
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_viewed_then_dropped(
        parser: TrajParser,
        witness: Optional[FailureWitnessLoader],
    ) -> list[str]:
        """Identify files the agent viewed but did not edit."""
        visited = parser.visited_files()
        edited_short = {f.split("/")[-1] for f in parser.edited_files()}

        dropped = []
        seen = set()
        for f in sorted(visited):
            if f not in edited_short:
                dropped.append(f)
            seen.add(f)  # track all visited (even if edited) to avoid false unvisited

        # Add stack frame files not visited at all (deduplicated)
        if witness and witness.has_failure():
            for frame in witness.stack_trace:
                fname = frame.get("file", "").split("/")[-1]
                if fname and fname not in seen:
                    dropped.append(f"{fname} (stack frame, unvisited)")
                    seen.add(fname)

        return dropped


# =====================================================================
# OracleAudit — gold-based signals (EVAL ONLY, NOT FOR RUNTIME)
# =====================================================================

@dataclass
class OracleAudit:
    """Gold-based audit metrics.

    WARNING: This is EVAL-ONLY. NEVER pass OracleAudit data into:
      - search_contract_builder.py
      - diagnosis_generator.py
      - retry prompts / context injection
      - any runtime module

    These signals are for offline analysis (Table 2, error analysis) only.
    """

    instance_id: str = ""
    gold_file_first_seen_step: Optional[int] = None
    gold_file_coverage: float = 0.0
    gold_seen_then_dropped: list[str] = field(default_factory=list)
    gold_context_files: list[str] = field(default_factory=list)
    eval_only: bool = True  # Sentinel — always True, guards against runtime use

    audit_version: str = "1.0"

    @classmethod
    def extract(
        cls,
        parser: TrajParser,
        gold_files: Optional[list[str]] = None,
    ) -> OracleAudit:
        """Extract gold-based audit signals.

        Args:
            parser: Initialized TrajParser.
            gold_files: List of gold file paths (from ContextBench gold data).
                        If None, audit returns empty defaults.
        """
        audit = cls(instance_id=parser.instance_id)
        if not gold_files:
            return audit

        audit.gold_context_files = list(gold_files)
        gold_short = {f.split("/")[-1] for f in gold_files}

        # Gold file first seen step
        first_seen = None
        gold_seen = set()
        for event in parser.explore_events():
            fname = event["file"].split("/")[-1]
            if fname in gold_short:
                gold_seen.add(fname)
                if first_seen is None:
                    first_seen = event["step"]

        audit.gold_file_first_seen_step = first_seen
        if gold_short:
            audit.gold_file_coverage = len(gold_seen) / len(gold_short)

        # Gold seen but not edited (dropped)
        edited_short = {f.split("/")[-1] for f in parser.edited_files()}
        dropped = [f for f in gold_short if f in gold_seen and f not in edited_short]
        audit.gold_seen_then_dropped = sorted(dropped)

        return audit


# =====================================================================
# Convenience: extract all from a traj
# =====================================================================

def extract_all(
    traj_path: str | Path,
    witness_path: Optional[str | Path] = None,
    gold_files: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Extract both runtime signals and oracle audit from a trajectory.

    Returns a dict with 'runtime_signals' and 'oracle_audit' keys.
    The oracle_audit section is marked eval_only=true.
    """
    parser = TrajParser(traj_path)
    runtime = RuntimeSignals.extract(parser, witness_path)
    audit = OracleAudit.extract(parser, gold_files)
    return {
        "runtime_signals": {
            "error_visit_alignment": runtime.error_visit_alignment,
            "error_edit_file_level": runtime.error_edit_file_level,
            "error_edit_symbol_level": runtime.error_edit_symbol_level,
            "error_edit_line_window": runtime.error_edit_line_window,
            "error_edit_term_overlap": runtime.error_edit_term_overlap,
            "error_edit_alignment": runtime.error_edit_alignment,
            "exploration_mode": runtime.exploration_mode,
            "total_bash_commands": runtime.total_bash_commands,
            "total_test_commands": runtime.total_test_commands,
            "total_checkouts": runtime.total_checkouts,
            "total_api_calls": runtime.total_api_calls,
            "unique_files_visited": runtime.unique_files_visited,
            "unique_files_edited": runtime.unique_files_edited,
            "test_runs": runtime.test_runs,
            "has_regression_signal": runtime.has_regression_signal,
            "viewed_then_dropped_files": runtime.viewed_then_dropped_files,
            "signal_version": runtime.signal_version,
        },
        "oracle_audit": {
            "instance_id": audit.instance_id,
            "gold_file_first_seen_step": audit.gold_file_first_seen_step,
            "gold_file_coverage": audit.gold_file_coverage,
            "gold_seen_then_dropped": audit.gold_seen_then_dropped,
            "gold_context_files": audit.gold_context_files,
            "eval_only": True,
            "audit_version": audit.audit_version,
        },
    }
