"""Gather all data needed for comprehensive reports on all 5 instances."""
import json, sys
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")

from experiments.manifest_builder import get_problem_statement, get_base_commit, get_gold_patch

INSTANCES = [
    "django__django-11820",
    "django__django-12125",
    "django__django-13513",
    "django__django-16454",
    "sympy__sympy-20428",
]

ARTIFACTS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0")
VERIFY_DIR = ARTIFACTS_ROOT / "verify_fixes"

# Batch mapping
BATCH_MAP = {
    "django__django-11820": "d4_9_batch2_17x4",
    "django__django-12125": "batch2_d4_7",
    "django__django-13513": "batch2_d4_7",
    "django__django-16454": "d4_9_batch2_17x4",
    "sympy__sympy-20428": "batch2_d4_7",
}

for iid in INSTANCES:
    print(f"\n{'#'*70}")
    print(f"# INSTANCE: {iid}")
    print(f"{'#'*70}")

    batch = BATCH_MAP[iid]
    attempt_dir = ARTIFACTS_ROOT / batch / "runs" / "miniswe" / "base_miniswe" / iid / "attempt_1"

    # 1. Problem statement
    ps = get_problem_statement(iid)
    print(f"\n## PROBLEM_STATEMENT")
    print(ps[:200] + "..." if len(ps) > 200 else ps)

    # 2. Gold patch
    gp = get_gold_patch(iid)
    print(f"\n## GOLD_PATCH ({len(gp)} chars)")
    print(gp[:1500] if len(gp) > 1500 else gp)

    # 3. Runtime signals
    rs = json.loads((attempt_dir / "runtime_signals.json").read_text())
    print(f"\n## RUNTIME_SIGNALS")
    print(f"exit_status={rs.get('exit_status')}")
    print(f"agent={rs.get('agent')}")
    print(f"n_api_calls={rs.get('api_calls')}")
    edited = rs.get("edited_files", [])
    print(f"edited_files={edited}")
    viewed = rs.get("viewed_files_in_order", [])
    print(f"viewed_files={viewed}")
    failures = rs.get("test_failures", [])
    print(f"test_failures={failures}")

    # 4. Patch diff
    patch_file = attempt_dir / "patch.diff"
    if patch_file.is_file():
        patch = patch_file.read_text()
        print(f"\n## ATTEMPT_1_PATCH ({len(patch)} chars)")
        print(patch[:2000] if len(patch) > 2000 else patch)

    # 5. Failure witness
    fw_file = ARTIFACTS_ROOT / "failure_witness" / iid / "failure_witness.json"
    if fw_file.is_file():
        fw = json.loads(fw_file.read_text())
        print(f"\n## FAILURE_WITNESS")
        print(f"failure_type={fw.get('failure_type')}")
        print(f"mode={fw.get('mode')}")
        print(f"error_message={fw.get('error_message','')[:500]}")

    # 6. Packet
    pkt_file = VERIFY_DIR / iid / "intervention" / "context_packet.md"
    if pkt_file.is_file():
        pkt = pkt_file.read_text()
        print(f"\n## CONTEXT_PACKET ({len(pkt)} chars, {len(pkt.splitlines())} lines)")
        print(pkt[:500] + "...")
        print(f"(full in {pkt_file})")

    # 7. Selected evidence
    ev_file = VERIFY_DIR / iid / "intervention" / "selected_evidence.json"
    if ev_file.is_file():
        ev = json.loads(ev_file.read_text())
        print(f"\n## SELECTED_EVIDENCE ({len(ev.get('evidence',[]))} items)")
        for e in ev.get("evidence", []):
            print(f"  {e.get('id')}: op={e.get('operation')} path={e.get('path')}:{e.get('start_line')}-{e.get('end_line')} score={e.get('score')}")

    # 8. Recovery report
    rr_file = VERIFY_DIR / iid / "intervention" / "recovery_report.json"
    if rr_file.is_file():
        rr = json.loads(rr_file.read_text())
        print(f"\n## RECOVERY_REPORT")
        print(f"trigger={rr.get('trigger_type')}")
        print(f"pathology={rr.get('diagnosis',{}).get('pathology')}")
        print(f"5r={rr.get('diagnosis',{}).get('primary_5r_action')}")
        print(f"actions={rr.get('executed_actions_summary')}")

    print(f"\n{'#'*70}")
    print(f"# END {iid}")
