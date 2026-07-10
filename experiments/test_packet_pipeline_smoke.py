"""ConDiag-v1 packet pipeline smoke test.

Tests that the core pipeline compiles, accepts inputs, and produces
non-empty outputs. Does NOT require a full case_bundle or repo checkout.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")

passed = 0
failed = 0

def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}  {detail}")

# ── 1. diagnosis_generator.generate() ──
print("\n=== 1. diagnosis_generator ===")
from condiag.diagnosis_generator import generate, DiagnosisResult, DEFICIENCY_TYPES

runtime_signals = {
    "exit_status": "submitted",
    "edited_files_count": 2,
    "viewed_files_count": 15,
    "api_calls": 35,
    "n_messages": 20,
    "test_runs_count": 3,
    "test_failures_count": 1,
    "changed_lines_added": 8,
    "changed_lines_removed": 2,
    "changed_files_count": 1,
    "repeated_edit_pattern_detected": False,
    "edited_but_not_viewed_files_count": 0,
    "viewed_but_not_final_files_count": 3,
    "viewed_spans": {"some/file.py": [[10, 30], [50, 70]]},
    "possible_regression_failures": [],
    "test_failures": [
        {"test": "tests/test_something.py::test_foo", "status": "failed", "message": "AssertionError"}
    ],
}

result = generate(
    trigger_type="MODERATE_INSUFFICIENT_ISSUE_CONTEXT",
    trigger_reason=["edited_but_not_viewed", "multiple_test_runs"],
    runtime_signals=runtime_signals,
    issue="Fix the foo method to handle empty input correctly",
)

check("result is DiagnosisResult", isinstance(result, DiagnosisResult))
check("context_deficiency_type is non-empty", bool(result.context_deficiency_type))
check("  valid type", result.context_deficiency_type in DEFICIENCY_TYPES, result.context_deficiency_type)
check("retrieval_plan is non-empty", len(result.retrieval_plan) > 0)
check("target_hints is non-empty", len(result.target_hints) > 0)

# ── 2. trigger module ──
print("\n=== 2. trigger ===")
from condiag.trigger import TriggerResult

tr = TriggerResult(
    instance_id="smoke_test",
    triggered=True,
    trigger_type="MODERATE_INSUFFICIENT_ISSUE_CONTEXT",
    trigger_reasons=["edited_but_not_viewed", "viewed_but_not_final"],
)
check("trigger result type set", bool(tr.trigger_type))
check("trigger reasons non-empty", len(tr.trigger_reasons) > 0)

# ── 3. retrieval_executor module functions ──
print("\n=== 3. retrieval_executor ===")
from condiag.retrieval_executor import EvidenceCandidate, ActionResult, _normalize_viewed_spans, _parse_lines_field

ec = EvidenceCandidate(id="e1", operation="find_symbol_definition", relation="definition", path="foo.py", start_line=42, end_line=50, symbol="bar", score=0.8)
check("EvidenceCandidate works", ec.score == 0.8 and ec.path == "foo.py")

ar = ActionResult(operation="find_symbol_definition", target="bar", budget=3, status="done")
check("ActionResult works", ar.operation == "find_symbol_definition" and ar.status == "done")

norm = _normalize_viewed_spans({"foo.py": [[1, 10]]})
check("normalize_viewed_spans works", len(norm) > 0)

# ── 4. evidence_selector ──
print("\n=== 4. evidence_selector ===")
from condiag.evidence_selector import select as select_evidence
from condiag.retrieval_executor import ActionResult, EvidenceCandidate

action_results = [
    ActionResult(
        operation="find_symbol_definition", target="Foo.bar", budget=5, status="done",
        candidates=[
            EvidenceCandidate(id="e1", operation="find_symbol_definition", relation="definition", path="foo.py", start_line=42, end_line=50, symbol="Foo.bar", score=0.85),
            EvidenceCandidate(id="e2", operation="find_symbol_definition", relation="call_chain", path="bar.py", start_line=10, end_line=15, symbol="baz", score=0.45),
        ]
    ),
]
selected = select_evidence(action_results, retry_intent="retry", instance_id="smoke_test")
check("selected non-empty", len(selected.get("evidence", [])) > 0)
check("selected has scores", all("score" in s for s in selected.get("evidence", [])))
check("selected has file paths", all("path" in s for s in selected.get("evidence", [])))

# ── 5. context_packet_builder (structural check) ──
print("\n=== 5. context_packet_builder ===")
from condiag.context_packet_builder import build_context_packet_md
from condiag.schemas import NormalizedDiagnosis, ManualDiagnosis, RuntimeSignals as RS

nd = NormalizedDiagnosis(
    instance_id="smoke_test",
    context_deficiency_type="API_DEFINITION_CONTEXT",
    pathology="API_DEFINITION_CONTEXT",
    primary_5r_action="RETRIEVE",
    retry_intent="retry_with_api_definition",
    action_family="retrieval",
    confidence=0.65,
)
md = ManualDiagnosis(
    instance_id="smoke_test",
    mode="auto",
    diagnosis={"pathology": "API_DEFINITION_CONTEXT"},
)
rs = RS.from_dict(runtime_signals)

selected_dict = {
    "evidence": [
        {"id": "e1", "operation": "find_symbol_definition", "relation": "definition", "path": "foo.py", "start_line": 42, "end_line": 50, "symbol": "Foo.bar", "score": 0.85},
    ]
}

packet_md = build_context_packet_md(
    repo_root=Path("/tmp"),
    nd=nd,
    md=md,
    rs=rs,
    selected=selected_dict,
)
check("packet non-empty", bool(packet_md) and len(packet_md) > 100)
check("no no_repo_provided", "no_repo_provided" not in packet_md)
check("has diagnosis header", "What Went Wrong" in packet_md)
check("has retry context", "Retry Context" in packet_md)

# ── 6. leakage_guard ──
print("\n=== 6. leakage_guard ===")
from condiag.leakage_guard import check_runtime_signals
from condiag.schemas import RuntimeSignals as RS
from condiag.schemas import PathologyTaxonomy

rs_obj = RS.from_dict(runtime_signals)
taxonomy = PathologyTaxonomy(schema_version="v0.2", framework="condiag")
try:
    lg = check_runtime_signals(rs_obj, taxonomy)
    check("leakage guard ok", lg.ok)
except Exception as e:
    check("leakage guard", False, str(e))

# ── Summary ──
print(f"\n{'='*50}")
print(f"  {passed} passed, {failed} failed, {passed+failed} total")
print(f"{'='*50}")
sys.exit(0 if failed == 0 else 1)
