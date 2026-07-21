"""ConDiag v4 Diagnosis Prompt Builder.

⚠️ FROZEN (2026-07-19): v4 soft diagnosis prompt. Replaced by the new
`condiag/diagnosis/` module (AAAI direction). Do NOT modify.

Pure rule extraction + template filling.
No semantic inference, no LLM calls, no CDType classification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TrajectorySnapshot:
    """Lightweight trajectory info extracted at round boundary."""
    viewed_files: list[str] = field(default_factory=list)
    edited_files: list[str] = field(default_factory=list)


class DiagnosisPromptBuilder:
    """Builds a structured diagnosis prompt from FailureWitness + trajectory.

    Stateless: takes witness and trajectory as build() arguments.
    """

    CATEGORIES = {
        "API_DEFINITION": "Missing class/function/attribute definition",
        "INTERFACE_CONSTRAINT": "Missing parameter/return/exception constraint",
        "RELATED_TESTS": "Missing adjacent test or regression condition",
        "CALLER_CALLEE": "Missing caller or callee context",
        "DEPENDENCY": "Missing cross-module dependency or configuration",
        "PARALLEL_IMPLEMENTATION": "Missing sibling/parallel implementation",
        "REGISTRATION_SITE": "Missing registration, routing, or export point",
        "LOCALIZATION_DIRECTION": "Edit location is symptom, not root cause",
    }

    def build(self, witness: dict, trajectory: TrajectorySnapshot | None = None) -> str:
        """Build the full diagnosis prompt string."""
        trajectory = trajectory or TrajectorySnapshot()
        parts = [
            self._header(),
            self._failure_summary(witness),
            self._stack_vs_edit_analysis(witness, trajectory),
            self._investigation_suggestions(witness),
            self._validation_obligations(),
        ]
        return "\n\n".join(p for p in parts if p)

    def _header(self) -> str:
        return (
            "## Diagnosis — Targeted Repair Guidance\n"
            "Your patch did not pass validation. Below is a structured analysis "
            "of what the failure signals suggest about potentially missing context.\n"
            "This is guidance, not a mandatory execution plan."
        )

    def _failure_summary(self, witness: dict) -> str:
        failed = witness.get("failed_tests", [])
        # Defensive: ensure all items are strings
        failed = [str(f) if not isinstance(f, str) else f for f in failed]
        error = witness.get("error_message", "")
        frames = witness.get("stack_frames", [])

        lines = ["### Failure Summary"]
        if failed:
            lines.append(f"**Failed tests:** {', '.join(failed)}")
        if error:
            lines.append(f"**Error:**\n```\n{error}\n```")
        if frames:
            def fmt_frame(f):
                if isinstance(f, dict):
                    return f"  {f.get('file','?')}:{f.get('line','?')} in {f.get('function','?')}"
                return str(f)
            frames_text = "\n".join(fmt_frame(x) for x in frames[:5])
    def _stack_vs_edit_analysis(self, witness: dict, trajectory: TrajectorySnapshot) -> str:
        frames = witness.get("stack_frames", [])
        viewed = set(trajectory.viewed_files)
        edited = set(trajectory.edited_files)

        gap_files = []
        for frame in frames[:5]:
            if isinstance(frame, dict):
                fname = frame.get("file", "unknown")
            else:
                fname = str(frame).split(":")[0].strip()
            if not fname:
                continue
            if fname not in viewed:
                gap_files.append(f"  - `{fname}` — stack frame, not yet viewed")
            elif fname not in edited:
                gap_files.append(f"  - `{fname}` — stack frame, viewed but not edited")

        if not gap_files:
            return ""

        return (
            "### Stack Frame vs Exploration Gap\n"
            "The following files appear in the failure stack but are absent "
            "from your exploration or edits:\n" + "\n".join(gap_files)
        )

    def _investigation_suggestions(self, witness: dict) -> str:
        error = (witness.get("error_message", "") or "").lower()
        suggestions = []

        if "attributeerror" in error:
            suggestions.append(
                "- **Possible API Definition gap** — The code references "
                "an attribute/method that doesn't exist on the object. "
                "Search for the class definition and check its interface."
            )
        elif "typeerror" in error:
            suggestions.append(
                "- **Possible Interface Constraint gap** — A function received "
                "an argument of unexpected type. Check the expected signature "
                "and the actual call site."
            )
        elif "assertionerror" in error or "assert " in error:
            suggestions.append(
                "- **Possible Related Tests gap** — The assertion failure reveals "
                "a behavioral assumption not met. Check adjacent test files and "
                "related behavior expectations."
            )
        elif "importerror" in error or "modulenotfounderror" in error:
            suggestions.append(
                "- **Possible Dependency gap** — A missing import suggests "
                "cross-module dependency not satisfied. Check import paths and "
                "module availability."
            )
        else:
            suggestions.append(
                "- Review the failing test logic and stack frames to identify "
                "what code path is not being covered by your patch."
            )

        lines = ["### Suggested Investigation Directions"]
        lines.extend(suggestions)
        lines.append(
            "- Compare the files you edited against the files in the stack trace. "
            "If they differ, the edit location may not address the root cause."
        )
        return "\n".join(lines)

    def _validation_obligations(self) -> str:
        return (
            "### Validation Requirements\n"
            "Before final submission, ensure:\n"
            "1. Your revised patch resolves **all** failing tests listed above\n"
            "2. You haven't introduced new regression failures\n"
        )
