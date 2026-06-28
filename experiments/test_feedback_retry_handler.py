"""D4-5 acceptance test — Feedback Retry handler (packet_only mode).

Validates the 10 acceptance criteria from the D4-5 spec against real
Batch2 instances. Uses 2 smoke instances that hit different trigger branches
when possible.

Also runs a synthetic unit-style test to exercise the should_retry=False
branch even when real Batch2 instances all happen to trigger retry.

Run:
    python3 -m experiments.test_feedback_retry_handler
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from experiments.baseline_runner import main as runner_main
from experiments.manifest_builder import build_manifest
from experiments.artifact_validator import validate_run
from experiments.baseline_handlers import (
    handle_feedback_retry,
    _build_feedback_packet,
    _summarize_previous_patch,
)
from experiments.retry_trigger import RetryTriggerResult


BATCH2_ROOT = Path("/mnt/d/condiag-artifacts/runs/pilot50_batch2_20260628_114704/miniswe/Verified")
TMP = Path("/mnt/d/condiag-artifacts/condiag/v0/smoke_d4_5_feedback_retry")
MANIFEST_CSV = TMP / "manifest.csv"
INSTANCES_FILE = TMP / "instances.txt"
OUT_ROOT = TMP / "runs"

# 2 smoke instances — pick seed-case-like ones likely to trigger retry
SMOKE_INSTANCES = [
    "django__django-10880",
    "astropy__astropy-14995",
]


def _setup() -> None:
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    INSTANCES_FILE.write_text("\n".join(SMOKE_INSTANCES) + "\n", encoding="utf-8")
    build_manifest(BATCH2_ROOT, MANIFEST_CSV)
    print(f"[setup] manifest built -> {MANIFEST_CSV}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    marker = "OK " if ok else "FAIL"
    print(f"[{marker}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def _run_base_miniswe_first() -> bool:
    """Run base_miniswe on the same instances so feedback_retry can read attempt_1."""
    rc = runner_main([
        "--agent", "miniswe",
        "--baseline", "base_miniswe",
        "--instances", str(INSTANCES_FILE),
        "--out", str(OUT_ROOT),
        "--mode", "smoke",
        "--manifest", str(MANIFEST_CSV),
    ])
    return rc == 0


def _run_feedback_retry() -> int:
    return runner_main([
        "--agent", "miniswe",
        "--baseline", "feedback_retry",
        "--instances", str(INSTANCES_FILE),
        "--out", str(OUT_ROOT),
        "--mode", "smoke",
        "--manifest", str(MANIFEST_CSV),
    ])


def test_acceptance() -> bool:
    results = []

    # Pre: run base_miniswe first so attempt_1 exists
    base_ok = _run_base_miniswe_first()
    results.append(check("pre. base_miniswe runs first (rc=0)", base_ok,
                         "" if base_ok else "BASE FAILED, retry test invalid"))

    rc = _run_feedback_retry()
    results.append(check("0. feedback_retry smoke run end-to-end (rc=0)", rc == 0, f"rc={rc}"))

    inst_dir = OUT_ROOT / "miniswe" / "feedback_retry" / SMOKE_INSTANCES[0]

    # 1. handler reads base_miniswe attempt_1
    rr = inst_dir / "run_report.json"
    rr_data = json.loads(rr.read_text()) if rr.is_file() else {}
    hr = rr_data.get("handler_result") or {}
    results.append(check("1. handler reads base_miniswe attempt_1 (handled=True, reason=feedback_retry_packet_only)",
                         hr.get("handled") is True and hr.get("reason") == "feedback_retry_packet_only",
                         f"reason={hr.get('reason')} source={hr.get('source_attempt_1', '')[-60:]}"))

    # 2. runs retry_trigger.classify
    trig_path = inst_dir / "intervention" / "retry_trigger_result.json"
    trig_ok = False
    trig_detail = "missing"
    if trig_path.is_file():
        trig = json.loads(trig_path.read_text())
        trig_ok = "trigger_type" in trig and "should_retry" in trig and "runtime_gap_status" in trig
        trig_detail = f"trigger_type={trig.get('trigger_type')} should_retry={trig.get('should_retry')}"
    results.append(check("2. retry_trigger_result.json written with classify() output",
                         trig_ok, trig_detail))

    # 3 + 4. branching logic
    ireport_path = inst_dir / "intervention" / "intervention_report.json"
    ireport = json.loads(ireport_path.read_text()) if ireport_path.is_file() else {}
    should_retry = ireport.get("should_retry")
    intervention_status = ireport.get("status")
    packet_path = inst_dir / "intervention" / "context_packet.md"

    branch_ok = (
        (should_retry is False and intervention_status == "skipped_no_retry" and not packet_path.is_file())
        or (should_retry is True and intervention_status == "feedback_packet_built" and packet_path.is_file())
    )
    results.append(check("3+4. should_retry branching correct (False→skipped, True→packet_built)",
                         branch_ok,
                         f"should_retry={should_retry} status={intervention_status} packet_exists={packet_path.is_file()}"))

    # 5 + 6. leakage guard: packet excludes ConDiag evidence / gold / contextbench
    leak_ok = True
    leak_hits = []
    if packet_path.is_file():
        text = packet_path.read_text(encoding="utf-8")
        forbidden = [
            "selected_evidence", "selected locations", "condiag retrieval",
            "gold_check", "gold_patch", "gold_context",
            "contextbench_metrics", "fail_to_pass", "pass_to_pass",
            "official_eval", "file_coverage", "line_coverage", "editloc_recall",
        ]
        for kw in forbidden:
            if kw.lower() in text.lower():
                leak_ok = False
                leak_hits.append(kw)
    results.append(check("5+6. context_packet excludes ConDiag evidence + gold/eval keywords",
                         leak_ok, f"hits={leak_hits}"))

    # 7. final/patch.diff = attempt_1/patch.diff (packet_only)
    a_patch = inst_dir / "attempt_1" / "patch.diff"
    f_patch = inst_dir / "final" / "patch.diff"
    match_ok = (
        a_patch.is_file() and f_patch.is_file()
        and a_patch.read_bytes() == f_patch.read_bytes()
    )
    results.append(check("7. final/patch.diff = attempt_1/patch.diff (packet_only mode)",
                         match_ok,
                         f"a_size={a_patch.stat().st_size if a_patch.is_file() else 0} "
                         f"f_size={f_patch.stat().st_size if f_patch.is_file() else 0}"))

    # 8. run_report has_intervention=True, has_attempt_2=False
    flags_ok = (
        rr_data.get("has_intervention") is True
        and rr_data.get("has_attempt_2") is False
    )
    results.append(check("8. run_report has_intervention=True, has_attempt_2=False",
                         flags_ok,
                         f"intervention={rr_data.get('has_intervention')} attempt_2={rr_data.get('has_attempt_2')}"))

    # 9. cost.json inherits from base (no new agent cost)
    base_cost = OUT_ROOT / "miniswe" / "base_miniswe" / SMOKE_INSTANCES[0] / "cost.json"
    fr_cost = inst_dir / "cost.json"
    cost_ok = False
    cost_detail = "missing"
    if base_cost.is_file() and fr_cost.is_file():
        bc = json.loads(base_cost.read_text())
        fc = json.loads(fr_cost.read_text())
        # same api_calls means no new agent invocation
        ba = (bc.get("attempts") or [{}])[0].get("api_calls")
        fa = (fc.get("attempts") or [{}])[0].get("api_calls")
        cost_ok = ba == fa
        cost_detail = f"base_api_calls={ba} fr_api_calls={fa}"
    results.append(check("9. cost.json inherits attempt_1 cost (no new agent cost)",
                         cost_ok, cost_detail))

    # 10. validator=ok
    val = validate_run(inst_dir, "feedback_retry", "miniswe", mode="smoke")
    val_ok = val["status"] == "ok"
    results.append(check("10. validator passes smoke mode, no leakage",
                         val_ok,
                         f"status={val['status']} missing={val.get('missing', [])} "
                         f"leakage={val.get('leakage_hits', [])}"))

    # Extra: synthetic test for should_retry=False branch
    # (in case both real instances happen to trigger retry)
    syn_ok = _test_no_trigger_branch_synthetic()
    results.append(check("extra. synthetic should_retry=False branch: no packet, status=skipped_no_retry",
                         syn_ok, "see _test_no_trigger_branch_synthetic"))

    # Extra: synthetic test for should_retry=True branch packet content
    syn_packet_ok = _test_packet_template_synthetic()
    results.append(check("extra. synthetic should_retry=True packet has 4 sections + retry instruction",
                         syn_packet_ok, ""))

    # Extra: 2nd instance also works
    inst2 = OUT_ROOT / "miniswe" / "feedback_retry" / SMOKE_INSTANCES[1]
    inst2_ok = ((inst2 / "attempt_1" / "runtime_signals.json").is_file()
                and (inst2 / "intervention" / "intervention_report.json").is_file()
                and (inst2 / "final" / "final_report.json").is_file())
    results.append(check("extra. 2nd smoke instance also works",
                         inst2_ok, f"instance={SMOKE_INSTANCES[1]}"))

    passed = sum(1 for ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} acceptance checks passed ===")
    return passed == total


def _test_no_trigger_branch_synthetic() -> bool:
    """Drive the handler with a synthetic NO_TRIGGER scenario in a tmp dir.

    Uses handle_feedback_retry directly with a pre-built attempt_1 that has
    minimal runtime_signals (no test_runs, no failures, no anomaly).
    """
    syn_dir = TMP / "synthetic_no_trigger"
    if syn_dir.exists():
        shutil.rmtree(syn_dir)
    attempt_1 = syn_dir / "attempt_1"
    attempt_1.mkdir(parents=True)
    # minimal rs that should NOT fire any rule
    rs = {
        "schema_version": "condiag.runtime_signals.v0.1",
        "instance_id": "synthetic__no-trigger",
        "exit_status": "Submitted",
        "test_runs_count": 0,
        "test_failures_count": 0,
        "git_checkout_count": 0,
        "edited_files_count": 1,
        "changed_lines_total": 10,
        "edited_files": ["somefile.py"],
    }
    (attempt_1 / "runtime_signals.json").write_text(
        json.dumps(rs, indent=2), encoding="utf-8")
    (attempt_1 / "patch.diff").write_text(
        "--- a/f.py\n+++ b/f.py\n@@ -1,3 +1,4 @@\n+pass\n", encoding="utf-8")
    (attempt_1 / "local_test_outputs.md").write_text(
        "# Local Test Outputs\n\n(no failures recorded)\n", encoding="utf-8")

    # synthesize a base_miniswe-style dir so handler finds base_attempt_1
    base_root = TMP / "synthetic_base_root"
    if base_root.exists():
        shutil.rmtree(base_root)
    base_a1 = base_root / "miniswe" / "base_miniswe" / "synthetic__no-trigger" / "attempt_1"
    base_a1.mkdir(parents=True)
    for f in attempt_1.iterdir():
        shutil.copyfile(f, base_a1 / f.name)
    (base_a1.parent / "cost.json").write_text(
        json.dumps({"schema_version": "condiag.cost.v0", "attempts": [{"api_calls": 1}]}, indent=2),
        encoding="utf-8",
    )

    # minimal fake adapter
    class _FakeAdapter:
        name = "miniswe"
    result = handle_feedback_retry(
        run_dir=syn_dir,
        instance_id="synthetic__no-trigger",
        mode="smoke",
        adapter=_FakeAdapter(),
        config={"base_run_root": str(base_root), "manifest": {}},
    )

    if not result.get("handled"):
        return False
    if result.get("should_retry") is not False:
        return False
    if result.get("intervention_status") != "skipped_no_retry":
        return False
    if result.get("has_context_packet") is not False:
        return False
    # check files on disk
    if (syn_dir / "intervention" / "context_packet.md").is_file():
        return False
    if not (syn_dir / "intervention" / "intervention_report.json").is_file():
        return False
    if not (syn_dir / "intervention" / "retry_trigger_result.json").is_file():
        return False
    return True


def _test_packet_template_synthetic() -> bool:
    """Verify packet template has the 4 user-spec sections + retry instruction."""
    trig = RetryTriggerResult(
        should_retry=True,
        trigger_type="PARTIAL_FIX_SUSPICION",
        trigger_reason=["small patch (1 file, 12 lines), no positive correctness signal"],
        runtime_gap_status="UNSEEN_CANDIDATE",
        confidence="medium",
        evidence={},
        alternative_trigger_types=[],
    )
    patch_summary = {
        "has_patch": True,
        "files": ["django/foo.py"],
        "files_count": 1,
        "added_lines": 8,
        "removed_lines": 2,
        "patch_chars": 100,
    }
    test_feedback = {
        "has_output": True,
        "excerpt": "FAILED test_foo\nAssertionError: bad",
        "lines_total": 5,
        "lines_excerpt": 5,
    }
    md = _build_feedback_packet(
        instance_id="synthetic__trigger",
        trigger_result=trig,
        patch_summary=patch_summary,
        test_feedback=test_feedback,
    )
    required_sections = [
        "# Feedback Retry Packet",
        "## Previous Attempt Summary",
        "## Runtime Feedback",
        "## Previous Patch Summary",
        "## Retry Instruction",
    ]
    for sec in required_sections:
        if sec not in md:
            return False
    # check forbidden keywords
    forbidden = ["selected_evidence", "gold_check", "contextbench_metrics", "fail_to_pass"]
    for kw in forbidden:
        if kw.lower() in md.lower():
            return False
    return True


if __name__ == "__main__":
    _setup()
    ok = test_acceptance()
    sys.exit(0 if ok else 1)
