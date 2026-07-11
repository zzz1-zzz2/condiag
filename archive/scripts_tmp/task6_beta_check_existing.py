"""Check feedback_retry intervention_report content for both cases + verify plain_rerun runner behavior."""
import json
from pathlib import Path

D4_9 = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
CASES = ["django__django-11820", "django__django-13513"]

for iid in CASES:
    print(f"=== {iid} ===")
    # feedback_retry
    fr_ir = D4_9 / "feedback_retry" / iid / "intervention" / "intervention_report.json"
    if fr_ir.is_file():
        d = json.loads(fr_ir.read_text())
        print(f"  feedback_retry intervention_report:")
        print(f"    should_retry: {d.get('should_retry')}")
        print(f"    baseline: {d.get('baseline')}")
        print(f"    trigger_type: {d.get('trigger_type')}")
        print(f"    has_context_packet: {d.get('has_context_packet')}")
        print(f"    context_packet_path: {d.get('context_packet_path')}")
    # broad_expansion
    be_ir = D4_9 / "broad_expansion" / iid / "intervention" / "intervention_report.json"
    if be_ir.is_file():
        d = json.loads(be_ir.read_text())
        print(f"  broad_expansion intervention_report:")
        print(f"    should_retry: {d.get('should_retry')}")
        print(f"    baseline: {d.get('baseline')}")
        print(f"    trigger_type: {d.get('trigger_type')}")
    print()
