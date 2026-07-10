"""
Contract Renderer — converts DiagnosticSearchContract (JSON) to Markdown
for injection into the Host Agent's attempt_2 system prompt.

The rendered contract guides the agent's tool-use loop without doing
retrieval itself. No gold data enters this output.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CDTYPE_LABELS = {
    "API_DEFINITION_CONTEXT": "API Definition Context",
    "CALLER_CALLEE_CONTEXT": "Caller/Callee Context",
    "DEPENDENCY_CONTEXT": "Dependency Context",
    "INTERFACE_CONSTRAINT_CONTEXT": "Interface Constraint Context",
    "REGRESSION_CONSTRAINT_CONTEXT": "Regression Constraint Context",
    "RELATED_TEST_CONTEXT": "Related Test Context",
    "ROOT_CAUSE_RELOCALIZATION": "Root Cause Relocalization",
}


def render_contract_to_markdown(contract: dict[str, Any] | str | Path) -> str:
    """Render a DiagnosticSearchContract to Markdown for agent injection.

    Args:
        contract: Dict, JSON string, or Path to JSON file.

    Returns:
        Markdown string suitable for inclusion in the agent's retry prompt.
    """
    if isinstance(contract, (str, Path)):
        p = Path(contract)
        if p.exists():
            with open(p) as f:
                contract = json.load(f)
        else:
            contract = json.loads(str(contract))

    lines: list[str] = []
    _w = lines.append

    _w("## Diagnostic Search Contract")
    _w("")
    _w("The following diagnostic analysis identifies the likely root cause")
    _w("of the test failure and provides structured search guidance for the")
    _w("next repair attempt. Follow the inspections and searches below to")
    _w("locate the relevant code. Do NOT attempt to guess a patch without")
    _w("first examining the identified code areas.")
    _w("")

    # -----------------------------------------------------------------
    # 1. Failure Summary
    # -----------------------------------------------------------------
    fs = contract.get("failure_summary", {})
    if fs.get("failing_test") or fs.get("error_type"):
        _w("### Failure Summary")
        _w("")
        if fs.get("failing_test"):
            _w(f"- **Failing test:** `{fs['failing_test']}`")
        if fs.get("error_type"):
            _w(f"- **Error type:** `{fs['error_type']}`")
        if fs.get("failure_mode") and fs["failure_mode"] != "unknown":
            _w(f"- **Failure mode:** {fs['failure_mode']}")
        _w("")

    # -----------------------------------------------------------------
    # 2. Trajectory Signals Snapshot
    # -----------------------------------------------------------------
    ts = contract.get("trajectory_signals", {})
    if ts.get("error_edit_alignment") and ts["error_edit_alignment"] != "unknown":
        _w("### Previous Attempt Signals")
        _w("")
        _w(f"- **Error-edit alignment:** {ts['error_edit_alignment']}")
        if ts.get("exploration_mode") and ts["exploration_mode"] != "unknown":
            _w(f"- **Exploration mode:** {ts['exploration_mode']}")
        _w("")

    # -----------------------------------------------------------------
    # 3. Context Deficiency Diagnosis
    # -----------------------------------------------------------------
    cd = contract.get("context_deficiency_diagnosis", {})
    scores = cd.get("scores", cd)  # flat or nested

    # Filter non-zero scores
    active_types = {
        k: v for k, v in scores.items()
        if isinstance(v, (int, float)) and v > 0.0
    }
    if active_types:
        _w("### Context Deficiency Diagnosis")
        _w("")
        _w("| Type | Score |")
        _w("|------|-------|")
        for ctype in sorted(active_types, key=lambda k: -active_types[k]):
            label = CDTYPE_LABELS.get(ctype, ctype)
            score = active_types[ctype]
            _w(f"| {label} | {score:.2f} |")
        _w("")

    if cd.get("explanation"):
        _w(f"**Explanation:** {cd['explanation']}")
        _w("")
    if cd.get("action"):
        _w(f"**Recommended action:** {cd['action']}")
        _w("")

    # -----------------------------------------------------------------
    # 4. Required Inspections
    # -----------------------------------------------------------------
    inspections = contract.get("required_inspections", [])
    if inspections:
        _w("### Required Code Inspections")
        _w("")
        _w("Examine each file/symbol listed below. Read the surrounding code")
        _w("to understand the relevant logic before making changes.")
        _w("")

        for i, insp in enumerate(inspections, 1):
            file = insp.get("file", "")
            lns = insp.get("lines", [])
            reason = insp.get("reason", "")
            symbol = insp.get("symbol", "")

            loc = ""
            if file:
                loc = f"`{file}`"
            if lns:
                loc += f" (lines {lns[0]}-{lns[-1] if len(lns) > 1 else lns[0]})"
            if symbol:
                loc += f" symbol=`{symbol}`"

            _w(f"{i}. **{loc}**")
            if reason:
                _w(f"   - *Reason:* {reason}")
            if insp.get("source"):
                _w(f"   - *Source:* {insp['source']}")
            _w("")

    # -----------------------------------------------------------------
    # 5. Required Searches
    # -----------------------------------------------------------------
    searches = contract.get("required_searches", [])
    if searches:
        _w("### Required Searches")
        _w("")
        _w("Use the repository search tools to find the following targets.")
        _w("Each search helps locate context that was missing in the first attempt.")
        _w("")

        for i, srch in enumerate(searches, 1):
            query = srch.get("query", "")
            search_type = srch.get("type", "")
            reason = srch.get("reason", "")
            scope = srch.get("scope", "")

            parts = []
            if query:
                parts.append(f"`{query}`")
            if search_type:
                parts.append(f"({search_type})")
            if scope:
                parts.append(f"scope={scope}")

            _w(f"{i}. **{' '.join(parts)}**")
            if reason:
                _w(f"   - *Reason:* {reason}")
            if srch.get("source"):
                _w(f"   - *Source:* {srch['source']}")
            _w("")

    # -----------------------------------------------------------------
    # 6. Anti-Patterns
    # -----------------------------------------------------------------
    anti = contract.get("anti_patterns", [])
    if anti:
        _w("### Anti-Patterns to Avoid")
        _w("")
        _w("The first attempt exhibited these patterns. Avoid repeating them:")
        _w("")
        for p in anti:
            _w(f"- {p}")
        _w("")

    # -----------------------------------------------------------------
    # 7. Validation Target
    # -----------------------------------------------------------------
    vt = contract.get("validation_target", {})
    if vt.get("test_command") or vt.get("expected_behavior"):
        _w("### Validation Target")
        _w("")
        if vt.get("test_command"):
            _w(f"- **Test command:** `{vt['test_command']}`")
        if vt.get("expected_behavior"):
            _w(f"- **Expected behavior:** {vt['expected_behavior']}")
        _w("")

    # -----------------------------------------------------------------
    # 8. Evidence Provenance
    # -----------------------------------------------------------------
    ep = contract.get("evidence_provenance", {})
    if ep.get("contract_source") or ep.get("supporting_artifact"):
        _w("---")
        _w("")
        _w("*This contract was generated from runtime-only diagnostic signals.*")
        if ep.get("contract_source"):
            _w(f"*Source: {ep['contract_source']}*")
        if ep.get("supporting_artifact"):
            _w(f"*Evidence: {ep['supporting_artifact']}*")
        _w("")

    return "\n".join(lines)


def render_contract_from_file(path: Path) -> str:
    """Convenience: load contract JSON from file, render to Markdown."""
    return render_contract_to_markdown(path)
