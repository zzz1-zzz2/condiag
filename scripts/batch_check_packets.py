"""Batch regenerate 5 first-failed packets — v2 with diagnosis fix."""
import json, sys, re, traceback
sys.path.insert(0, "/home/swelite/condiag")
from pathlib import Path
from experiments.condiag_packet_only import run_packet_only

INSTANCES = [
    ("django-11820", "django__django-11820",
     "admin views for RelatedOnlyFieldListFilter crash when dealing with deferred objects",
     "c2678e49759e5c4c329bff0eeca2886267005d21"),
    ("django-12125", "django__django-12125",
     "ModelManagerSerializer fails with AttributeError on non-model fields",
     "89d41cba392b759732ba9f1db4ff29ed47da6a56"),
    ("django-13513", "django__django-13513",
     "Suppress context should not override the exception's native traceback",
     "6599608c4d0befdcb820ddccce55f183f247ae4f"),
    ("django-16454", "django__django-16454",
     "The handling of subparsers within an ArgumentParser can lead to errors",
     "1250483ebf73f7a82ff820b94092c63ce4238264"),
    ("sympy-20428", "sympy__sympy-20428",
     "Poly.is_zero returns False for zero polynomial after clear_denoms",
     "c0e85160406f9bf2bcaa2992138587668a1cd0bc"),
]

RUNS_ROOT = "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe"
OUTPUT_BASE = Path("/tmp/condiag-v2-check")

for instance_id, dir_name, issue, base_commit in INSTANCES:
    print(f"\n{'='*80}")
    print(f"  {instance_id}")
    print(f"{'='*80}")
    sys.stdout.flush()

    attempt_1_dir = Path(f"{RUNS_ROOT}/base_miniswe/{dir_name}/attempt_1")
    trigger_path = Path(f"{RUNS_ROOT}/condiag_packet_only/{dir_name}/intervention/retry_trigger_result.json")
    repo_path = Path(f"/home/swelite/condiag/workspaces/{dir_name}/repo_base")

    if not attempt_1_dir.exists() or not trigger_path.exists() or not repo_path.exists():
        print("  SKIP: missing files")
        continue

    trigger_data = json.loads(trigger_path.read_text())
    class TriggerResult:
        pass
    tr = TriggerResult()
    tr.trigger_type = trigger_data.get("trigger_type", "")
    tr.trigger_reason = trigger_data.get("trigger_reason", [])
    tr.should_retry = trigger_data.get("should_retry", False)
    tr.confidence = trigger_data.get("confidence", 0.0)
    tr.runtime_gap_status = trigger_data.get("runtime_gap_status", "")

    out_dir = OUTPUT_BASE / instance_id
    try:
        rr = run_packet_only(
            attempt_1_dir=attempt_1_dir,
            intervention_dir=out_dir,
            instance_id=instance_id,
            agent="miniswe",
            model="deepseek-v4",
            trigger_result=tr,
            repo_path=repo_path,
            base_commit=base_commit,
            issue=issue,
        )
        selected = json.loads((out_dir / "selected_evidence.json").read_text())
        evidence = selected.get("evidence", [])
        packet_text = (out_dir / "context_packet.md").read_text()

        print(f"  Diagnosis: {rr['diagnosis']['context_deficiency_type']}")

        parts = packet_text.split("## Primary Edit Target")
        if len(parts) > 1:
            section = parts[1].split("##")[0]
            f_match = re.search(r"\*\*File\*\*:\s*`([^`]+)`", section)
            g_match = re.search(r"\*\*Goal\*\*:\s*(.+?)(?:\n\s*\n|\n#|\Z)", section, re.DOTALL)
            if f_match:
                print(f"  Primary target file: {f_match.group(1)}")
            if g_match:
                print(f"  Primary target goal: {g_match.group(1).strip()[:100]}")

        print(f"  Evidence count: {len(evidence)}")
        print(f"  Relations: {sorted(set(e.get('relation','') for e in evidence))}")
        print(f"  Packet chars: {len(packet_text)}")

        for ev in evidence:
            rel = ev.get("relation", "?")
            path = ev.get("path", "?")
            sym = ev.get("symbol", "") or ""
            print(f"    {rel:35s} {path:50s} {sym:30s} L{ev.get('start_line','?')}-{ev.get('end_line','?')}")

    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        traceback.print_exc()

print("\n" + "="*80)
print("  All done.")
print("="*80)
