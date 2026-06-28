"""D4-4 acceptance test — Base mini-SWE handler (from_existing_traj mode).

Validates the 10 acceptance criteria from the D4-4 spec against real
Batch2 trajs. Uses 2 smoke instances.

Run:
    python3 -m experiments.test_base_miniswe_handler
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
TMP = Path("/mnt/d/condiag-artifacts/condiag/v0/smoke_d4_4_base_miniswe")
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
    INSTANCES_FILE.write_text(
        "\n".join(SMOKE_INSTANCES) + "\n",
        encoding="utf-8",
    )
    summary = build_manifest(BATCH2_ROOT, MANIFEST_CSV)
    assert summary["rows_written"] >= 2, f"manifest too small: {summary}"
    print(f"[setup] manifest built: {summary['rows_written']} rows -> {MANIFEST_CSV}")


def check(label: str, ok: bool, detail: str = "") -> bool:
    marker = "OK " if ok else "FAIL"
    print(f"[{marker}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def test_acceptance() -> bool:
    results = []

    rc = runner_main([
        "--agent", "miniswe",
        "--baseline", "base_miniswe",
        "--instances", str(INSTANCES_FILE),
        "--out", str(OUT_ROOT),
        "--mode", "smoke",
        "--manifest", str(MANIFEST_CSV),
    ])
    results.append(check("0. smoke run end-to-end (rc=0)", rc == 0, f"rc={rc}"))

    inst_dir = OUT_ROOT / "miniswe" / "base_miniswe" / SMOKE_INSTANCES[0]

    rr = inst_dir / "run_report.json"
    rr_data = json.loads(rr.read_text()) if rr.is_file() else {}
    hr = rr_data.get("handler_result") or {}
    results.append(check("1. handler reads manifest (handled=True, reason=from_existing_traj)",
                         hr.get("handled") is True and hr.get("reason") == "from_existing_traj",
                         f"reason={hr.get('reason')}"))

    expected_dirs = {"attempt_1", "final"}
    actual_dirs = {p.name for p in inst_dir.iterdir() if p.is_dir()}
    results.append(check("2. dir layout has attempt_1 + final",
                         expected_dirs.issubset(actual_dirs),
                         f"dirs={sorted(actual_dirs)}"))

    raw_traj = inst_dir / "attempt_1" / "raw_trajectory.json"
    raw_ok = raw_traj.is_file() and raw_traj.stat().st_size > 0
    results.append(check("3. attempt_1/raw_trajectory.json exists + non-empty",
                         raw_ok, f"size={raw_traj.stat().st_size if raw_traj.is_file() else 0}"))

    rs_path = inst_dir / "attempt_1" / "runtime_signals.json"
    rs_ok = False
    rs_detail = "missing"
    if rs_path.is_file():
        rs = json.loads(rs_path.read_text())
        sv = rs.get("schema_version", "")
        rs_ok = sv.startswith("condiag.runtime_signals")
        rs_detail = f"schema_version={sv}"
    results.append(check("4. attempt_1/runtime_signals.json schema_version correct",
                         rs_ok, rs_detail))

    patch = inst_dir / "attempt_1" / "patch.diff"
    patch_ok = patch.is_file()
    results.append(check("5. attempt_1/patch.diff exists",
                         patch_ok,
                         f"size={patch.stat().st_size if patch_ok else 0}"))

    fpc = inst_dir / "attempt_1" / "final_patch_context.json"
    fpc_ok = False
    fpc_detail = "missing"
    if fpc.is_file():
        fpc_data = json.loads(fpc.read_text())
        fpc_ok = fpc_data.get("schema_version") == "condiag.final_patch_context.v0"
        fpc_detail = f"files_count={fpc_data.get('files_count')}"
    results.append(check("6. attempt_1/final_patch_context.json schema correct",
                         fpc_ok, fpc_detail))

    lt = inst_dir / "attempt_1" / "local_test_outputs.md"
    lt_ok = lt.is_file() and lt.stat().st_size > 0
    results.append(check("7. attempt_1/local_test_outputs.md exists + non-empty",
                         lt_ok, f"size={lt.stat().st_size if lt.is_file() else 0}"))

    a_patch = inst_dir / "attempt_1" / "patch.diff"
    f_patch = inst_dir / "final" / "patch.diff"
    match_ok = (
        a_patch.is_file() and f_patch.is_file()
        and a_patch.read_bytes() == f_patch.read_bytes()
    )
    results.append(check("8. final/patch.diff identical to attempt_1/patch.diff",
                         match_ok,
                         f"a_size={a_patch.stat().st_size if a_patch.is_file() else 0} "
                         f"f_size={f_patch.stat().st_size if f_patch.is_file() else 0}"))

    cost_path = inst_dir / "cost.json"
    cost_ok = False
    cost_detail = "missing"
    if cost_path.is_file():
        c = json.loads(cost_path.read_text())
        attempts = c.get("attempts") or [{}]
        ac = attempts[0] if attempts else {}
        cost_ok = (
            c.get("schema_version") == "condiag.cost.v0"
            and isinstance(ac.get("api_calls"), int)
            and (ac.get("prompt_tokens") is None or isinstance(ac.get("prompt_tokens"), int))
        )
        cost_detail = f"api_calls={ac.get('api_calls')} tokens={ac.get('prompt_tokens')}"
    results.append(check("9. cost.json exists, schema ok, api_calls int, tokens nullable",
                         cost_ok, cost_detail))

    val = validate_run(inst_dir, "base_miniswe", "miniswe", mode="smoke")
    val_ok = val["status"] == "ok"
    results.append(check("10. validator passes smoke mode, no leakage",
                         val_ok,
                         f"status={val['status']} missing={val.get('missing', [])} "
                         f"leakage={val.get('leakage_hits', [])}"))

    fr_path = inst_dir / "final" / "final_report.json"
    if fr_path.is_file():
        fr = json.loads(fr_path.read_text())
        results.append(check("extra. final_report selected_attempt=1",
                             fr.get("selected_attempt") == 1,
                             f"selected_attempt={fr.get('selected_attempt')}"))
        results.append(check("extra. final_report has contextbench_metrics_status",
                             fr.get("contextbench_metrics_status") in ("pending_evaluation", "evaluated"),
                             f"status={fr.get('contextbench_metrics_status')}"))

    # Extra: final/runtime_signals.json copied from attempt_1
    a_rs = inst_dir / "attempt_1" / "runtime_signals.json"
    f_rs = inst_dir / "final" / "runtime_signals.json"
    rs_match = (a_rs.is_file() and f_rs.is_file()
                and a_rs.read_bytes() == f_rs.read_bytes())
    results.append(check("extra. final/runtime_signals.json copied from attempt_1",
                         rs_match,
                         f"a_size={a_rs.stat().st_size if a_rs.is_file() else 0} "
                         f"f_size={f_rs.stat().st_size if f_rs.is_file() else 0}"))

    # Extra: run_report has attempt_1_status + final_source
    if rr_data:
        results.append(check("extra. run_report has attempt_1_status=completed + final_source=attempt_1",
                             rr_data.get("attempt_1_status") == "completed"
                             and rr_data.get("final_source") == "attempt_1",
                             f"a1={rr_data.get('attempt_1_status')} fs={rr_data.get('final_source')}"))

    ar_path = inst_dir / "attempt_1" / "attempt_report.json"
    if ar_path.is_file():
        ar = json.loads(ar_path.read_text())
        results.append(check("extra. attempt_report.json schema correct",
                             ar.get("schema_version") == "condiag.attempt_report.v0",
                             f"exit_status={ar.get('exit_status')}"))

    inst2 = OUT_ROOT / "miniswe" / "base_miniswe" / SMOKE_INSTANCES[1]
    inst2_ok = ((inst2 / "attempt_1" / "raw_trajectory.json").is_file()
                and (inst2 / "final" / "final_report.json").is_file())
    results.append(check("extra. 2nd smoke instance also works",
                         inst2_ok, f"instance={SMOKE_INSTANCES[1]}"))

    passed = sum(1 for ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} acceptance checks passed ===")
    return passed == total


if __name__ == "__main__":
    _setup()
    ok = test_acceptance()
    sys.exit(0 if ok else 1)
