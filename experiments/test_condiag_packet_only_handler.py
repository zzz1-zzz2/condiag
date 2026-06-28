"""D4-7 acceptance test — ConDiag packet_only handler.

Validates that condiag_packet_only:
  - is the ONLY baseline allowed to import ConDiag retrieval machinery
  - produces typed/5R/diagnosis-style artifacts
  - cannot read gold/contextbench_metrics/official_eval (leakage scan)

Run:
    python3 -m experiments.test_condiag_packet_only_handler
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from experiments.baseline_runner import main as runner_main
from experiments.manifest_builder import build_manifest
from experiments.artifact_validator import validate_run


BATCH2_ROOT = Path("/mnt/d/condiag-artifacts/runs/pilot50_batch2_20260628_114704/miniswe/Verified")
TMP = Path("/mnt/d/condiag-artifacts/condiag/v0/smoke_d4_7_condiag_packet_only")
MANIFEST_CSV = TMP / "manifest.csv"
INSTANCES_FILE = TMP / "instances.txt"
OUT_ROOT = TMP / "runs"

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
    rc = runner_main([
        "--agent", "miniswe",
        "--baseline", "base_miniswe",
        "--instances", str(INSTANCES_FILE),
        "--out", str(OUT_ROOT),
        "--mode", "smoke",
        "--manifest", str(MANIFEST_CSV),
    ])
    return rc == 0


def _run_condiag_packet_only() -> int:
    return runner_main([
        "--agent", "miniswe",
        "--baseline", "condiag_packet_only",
        "--instances", str(INSTANCES_FILE),
        "--out", str(OUT_ROOT),
        "--mode", "smoke",
        "--manifest", str(MANIFEST_CSV),
    ])


def test_source_audit() -> bool:
    """condiag_packet_only.py IS allowed to import condiag retrieval.

    But it must NOT actively READ gold/eval/contextbench_metrics fields.
    We look for actual read patterns (.get / [ access / attribute access)
    on the forbidden tokens. Empty dict initialization (`gold_check={}`)
    and guarantee flags (`did_not_read_gold_check`) are accepted.
    """
    src_path = Path(__file__).parent / "condiag_packet_only.py"
    src = src_path.read_text(encoding="utf-8")

    # Filter out docstring lines (triple-quoted blocks).
    audit_lines = []
    in_docstring = False
    for ln in src.splitlines():
        triple_count = ln.count('"""')
        if triple_count == 1:
            in_docstring = not in_docstring
            continue
        elif triple_count >= 2:
            continue
        if in_docstring:
            continue
        audit_lines.append(ln)
    audit_src = "\n".join(audit_lines)

    # Look for actual READ patterns on forbidden tokens:
    #   <token>.get(        <- dict method call
    #   <token>[            <- dict subscript
    #   .<token>            <- attribute access (e.g. md.gold_check)
    #   from_dict(...).<token>
    forbidden_tokens = [
        "gold_check",
        "fail_to_pass",
        "pass_to_pass",
        "official_eval",
        "contextbench_metrics",
        "file_coverage",
        "line_coverage",
        "editloc_recall",
        "gold_patch",
        "gold_context",
    ]
    import re
    hits = []
    for tok in forbidden_tokens:
        # match dict-style read: token.get( OR token[
        pat1 = r"(?<![A-Za-z_])" + re.escape(tok) + r"(\.get\(|\[)"
        # match attribute read: .token (with word boundary on the right)
        pat2 = r"\." + re.escape(tok) + r"(?![A-Za-z_])"
        if re.search(pat1, audit_src) or re.search(pat2, audit_src):
            hits.append(tok)
    print(f"[audit] forbidden_read patterns (active reads): {hits}")
    return not hits


def test_acceptance() -> bool:
    results = []

    # 0. source audit (gating)
    audit_ok = test_source_audit()
    results.append(check("0. condiag_packet_only.py source audit: no gold/eval reads",
                         audit_ok, "see above"))

    # Pre: run base_miniswe first
    base_ok = _run_base_miniswe_first()
    results.append(check("pre. base_miniswe runs first (rc=0)", base_ok,
                         "" if base_ok else "BASE FAILED"))

    rc = _run_condiag_packet_only()
    results.append(check("1. condiag_packet_only handler runs end-to-end (rc=0)",
                         rc == 0, f"rc={rc}"))

    inst_dir = OUT_ROOT / "miniswe" / "condiag_packet_only" / SMOKE_INSTANCES[0]

    # 2. handler reuses base attempt_1
    rr = inst_dir / "run_report.json"
    rr_data = json.loads(rr.read_text()) if rr.is_file() else {}
    hr = rr_data.get("handler_result") or {}
    results.append(check("2. handler reuses base attempt_1 (reason=condiag_packet_only)",
                         hr.get("handled") is True and hr.get("reason") == "condiag_packet_only",
                         f"reason={hr.get('reason')}"))

    # 3. retry_trigger_result.json written
    trig_path = inst_dir / "intervention" / "retry_trigger_result.json"
    trig_ok = False
    trig_detail = "missing"
    if trig_path.is_file():
        trig = json.loads(trig_path.read_text())
        trig_ok = "trigger_type" in trig and "should_retry" in trig
        trig_detail = f"trigger_type={trig.get('trigger_type')}"
    results.append(check("3. retry_trigger_result.json written", trig_ok, trig_detail))

    # 4. recovery_report.json with ConDiag diagnosis
    rec_path = inst_dir / "intervention" / "recovery_report.json"
    rec_ok = False
    rec_detail = "missing"
    if rec_path.is_file():
        rec = json.loads(rec_path.read_text())
        diag = rec.get("diagnosis") or {}
        rec_ok = (
            rec.get("schema_version") == "condiag.recovery_report.v0"
            and rec.get("baseline") == "condiag_packet_only"
            and "pathology" in diag
            and "primary_5r_action" in diag
            and "retry_intent" in diag
        )
        rec_detail = (
            f"pathology={diag.get('pathology')} "
            f"5r={diag.get('primary_5r_action')} "
            f"intent={diag.get('retry_intent')}"
        )
    results.append(check("4. recovery_report.json has ConDiag diagnosis (pathology + 5R + retry_intent)",
                         rec_ok, rec_detail))

    # 5. selected_evidence.json written (ConDiag flavor — empty list allowed)
    se_path = inst_dir / "intervention" / "selected_evidence.json"
    se_ok = False
    se_detail = "missing"
    if se_path.is_file():
        se = json.loads(se_path.read_text())
        se_ok = "evidence" in se and "selection_summary" in se
        se_detail = f"evidence_count={len(se.get('evidence') or [])}"
    results.append(check("5. selected_evidence.json written with ConDiag schema",
                         se_ok, se_detail))

    # 6. executed_actions.json written
    ea_path = inst_dir / "intervention" / "executed_actions.json"
    ea_ok = False
    ea_detail = "missing"
    if ea_path.is_file():
        ea = json.loads(ea_path.read_text())
        ea_ok = "actions" in ea and "summary" in ea and "repo_status" in ea
        ea_detail = f"actions={len(ea.get('actions') or [])} repo_status={ea.get('repo_status')}"
    results.append(check("6. executed_actions.json written with repo_status",
                         ea_ok, ea_detail))

    # 7. context_packet.md is ConDiag-flavored (has Diagnosis section + 5R label)
    pkt_path = inst_dir / "intervention" / "context_packet.md"
    pkt_ok = False
    pkt_detail = "missing"
    if pkt_path.is_file():
        text = pkt_path.read_text(encoding="utf-8")
        pkt_ok = (
            "# ConDiag Context Packet" in text
            and "## Diagnosis" in text
            and "**5R action**" in text
            and ("## Retrieved Evidence" in text or "## Repair Constraints" in text)
        )
        pkt_detail = f"size={pkt_path.stat().st_size}"
    results.append(check("7. context_packet.md is ConDiag-flavored (Diagnosis + 5R + Retrieved Evidence)",
                         pkt_ok, pkt_detail))

    # 8. intervention_report.json wrapper
    ir_path = inst_dir / "intervention" / "intervention_report.json"
    ir_ok = False
    ir_detail = "missing"
    if ir_path.is_file():
        ir = json.loads(ir_path.read_text())
        ir_ok = (
            ir.get("schema_version") == "condiag.intervention_report.v0"
            and ir.get("baseline") == "condiag_packet_only"
            and ir.get("mode") == "packet_only"
            and ir.get("context_packet_kind") == "condiag_typed_recovery"
            and ir.get("recovery_report_path") is not None
        )
        ir_detail = f"status={ir.get('status')}"
    results.append(check("8. intervention_report.json wrapper has ConDiag kind + recovery_report_path",
                         ir_ok, ir_detail))

    # 9. NO attempt_2 (packet_only)
    a2_dir = inst_dir / "attempt_2"
    results.append(check("9. no attempt_2/ directory (packet_only mode)",
                         not a2_dir.exists(),
                         f"exists={a2_dir.exists()}"))

    # 10. final = attempt_1 (same patch)
    a1_patch = inst_dir / "attempt_1" / "patch.diff"
    f_patch = inst_dir / "final" / "patch.diff"
    final_same = (
        a1_patch.is_file() and f_patch.is_file()
        and a1_patch.read_bytes() == f_patch.read_bytes()
    )
    results.append(check("10. final/patch.diff == attempt_1/patch.diff (packet_only)",
                         final_same,
                         f"a1={a1_patch.is_file()} f={f_patch.is_file()}"))

    # 11. leakage scan: no gold_check / contextbench_metrics / fail_to_pass / etc
    #     in any ConDiag-produced artifact
    leak_targets = [
        "intervention/context_packet.md",
        "intervention/recovery_report.json",
        "intervention/selected_evidence.json",
        "intervention/executed_actions.json",
        "intervention/intervention_report.json",
        "final/final_report.json",
    ]
    forbidden_kw = [
        "gold_check", "gold_patch", "gold_context",
        "contextbench_metrics", "fail_to_pass", "pass_to_pass",
        "official_eval", "file_coverage", "line_coverage", "editloc_recall",
    ]
    leak_hits = []
    for rel in leak_targets:
        f = inst_dir / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="ignore")
        for kw in forbidden_kw:
            import re
            if re.search(r"(?<![A-Za-z_])" + re.escape(kw) + r"(?![A-Za-z_])", text):
                leak_hits.append(f"{rel}:{kw}")
    results.append(check("11. ConDiag artifacts free of gold/eval/contextbench_metrics keywords",
                         not leak_hits, f"hits={leak_hits}"))

    # 12. cost.json inherited from base
    base_cost = OUT_ROOT / "miniswe" / "base_miniswe" / SMOKE_INSTANCES[0] / "cost.json"
    cp_cost = inst_dir / "cost.json"
    cost_ok = (
        base_cost.is_file() and cp_cost.is_file()
        and base_cost.read_bytes() == cp_cost.read_bytes()
    )
    results.append(check("12. cost.json inherited from base_miniswe (packet_only: no new agent cost)",
                         cost_ok,
                         f"base={base_cost.is_file()} cp={cp_cost.is_file()}"))

    # 13. 5R mapping: at least one of RECONCILE/RESTRAIN/REHYDRATE/RETRIEVE/RELOCALIZE
    #     appears in pathology mapping (NO_TRIGGER -> ABSTAIN/UNKNOWN also OK)
    if rec_path.is_file():
        rec = json.loads(rec_path.read_text())
        trig = rec.get("trigger_type", "")
        path = (rec.get("diagnosis") or {}).get("pathology", "")
        r5 = (rec.get("diagnosis") or {}).get("primary_5r_action", "")
        if trig == "NO_TRIGGER":
            map_ok = path in {"UNKNOWN", ""} or r5 in {"NOOP", ""}
            map_detail = f"NO_TRIGGER -> pathology={path} 5r={r5}"
        else:
            valid_5r = {"RECONCILE", "RESTRAIN", "REHYDRATE", "RETRIEVE", "RELOCALIZE", "NOOP"}
            map_ok = r5 in valid_5r
            map_detail = f"{trig} -> pathology={path} 5r={r5}"
        results.append(check("13. auto-diagnoser maps trigger_type to pathology + 5R action",
                             map_ok, map_detail))
    else:
        results.append(check("13. auto-diagnoser mapping check (skipped — no recovery_report)",
                             False, "missing recovery_report"))

    # 14. validator=ok
    val = validate_run(inst_dir, "condiag_packet_only", "miniswe", mode="smoke")
    val_ok = val["status"] == "ok"
    results.append(check("14. validator passes smoke mode",
                         val_ok,
                         f"status={val['status']} missing={val.get('missing', [])} "
                         f"leakage={val.get('leakage_hits', [])}"))

    # 15. batch-runnable: 2nd instance also produces full artifact set
    inst2 = OUT_ROOT / "miniswe" / "condiag_packet_only" / SMOKE_INSTANCES[1]
    inst2_ok = (
        (inst2 / "attempt_1" / "runtime_signals.json").is_file()
        and (inst2 / "intervention" / "context_packet.md").is_file()
        and (inst2 / "intervention" / "recovery_report.json").is_file()
        and (inst2 / "final" / "final_report.json").is_file()
    )
    results.append(check("15. batch-runnable: 2nd instance also produces full artifact set",
                         inst2_ok, f"instance={SMOKE_INSTANCES[1]}"))

    # 16. baseline_handlers source: handle_condiag_packet_only imports condiag_packet_only
    #     (the ONLY baseline allowed to import ConDiag retrieval machinery)
    src = (Path(__file__).parent / "baseline_handlers.py").read_text(encoding="utf-8")
    has_condiag_import = "from . import condiag_packet_only as cpo" in src
    results.append(check("16. handle_condiag_packet_only imports ConDiag machinery (allowed)",
                         has_condiag_import,
                         f"import_found={has_condiag_import}"))

    passed = sum(1 for ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} acceptance checks passed ===")
    return passed == total


if __name__ == "__main__":
    _setup()
    ok = test_acceptance()
    sys.exit(0 if ok else 1)
