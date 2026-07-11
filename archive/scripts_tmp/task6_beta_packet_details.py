"""Detailed check of v2-alpha packet structure for 2 cases + intervention_report reuse options."""
import json
from pathlib import Path

CASES = ["django__django-11820", "django__django-13513"]
V2_ALPHA_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/context_packet_v2_alpha")
API_NAV_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/api_navigation")
D4_9 = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")

# Also check django-12125 for reference (how Task 6-alpha staged it)
REF = "django__django-12125"

print("=== Reference: how django-12125 was staged for Task 6-alpha ===")
ref_intervention = V2_ALPHA_ROOT / REF / "intervention"
print(f"  ref dir: {ref_intervention}")
if ref_intervention.is_dir():
    for f in sorted(ref_intervention.iterdir()):
        print(f"    {f.name}: {f.stat().st_size} bytes")
    ir = ref_intervention / "intervention_report.json"
    if ir.is_file():
        d = json.loads(ir.read_text())
        print(f"    intervention_report.should_retry: {d.get('should_retry')}")
        print(f"    intervention_report.baseline: {d.get('baseline')}")
        print(f"    intervention_report.trigger_type: {d.get('trigger_type')}")
        print(f"    intervention_report.context_packet_path: {d.get('context_packet_path')}")

print()
print("=== Per-case v2-alpha packet + api_nav details ===")
for iid in CASES:
    print(f"\n--- {iid} ---")
    # v2-alpha packet
    pkt_root = V2_ALPHA_ROOT / iid
    print(f"  v2_alpha dir: {pkt_root}")
    if pkt_root.is_dir():
        for f in sorted(pkt_root.iterdir()):
            print(f"    {f.name}: {f.stat().st_size} bytes")
        # check if there's an intervention_report already
        ir = pkt_root / "intervention_report.json"
        if ir.is_file():
            d = json.loads(ir.read_text())
            print(f"    intervention_report.should_retry: {d.get('should_retry')}")
            print(f"    intervention_report.baseline: {d.get('baseline')}")
            print(f"    intervention_report.trigger_type: {d.get('trigger_type')}")
        else:
            print(f"    intervention_report.json: MISSING (need synthetic)")
    # api nav
    api = API_NAV_ROOT / iid / "api_navigation_hint.json"
    print(f"  api_nav: {api}")
    if api.is_file():
        d = json.loads(api.read_text())
        hints = d.get("hints", []) if isinstance(d, dict) else []
        print(f"    hints count: {len(hints)}")
        if hints and isinstance(hints[0], dict):
            print(f"    first hint source: {hints[0].get('hint_source')}")
            print(f"    first hint target: {hints[0].get('target_symbol', hints[0].get('target_file', 'n/a'))}")
            print(f"    first hint supporting_artifact: {str(hints[0].get('supporting_artifact', ''))[:100]}")

print()
print("=== Existing d4_9_batch2 plain_rerun + condiag_retry dirs (older v0 baselines) ===")
for iid in CASES:
    print(f"\n--- {iid} ---")
    for bl in ["plain_rerun", "condiag_retry", "condiag_packet_only"]:
        d = D4_9 / bl / iid / "intervention"
        if d.is_dir():
            files = sorted([f.name for f in d.iterdir()])
            print(f"  {bl}/intervention: {files}")
            ir = d / "intervention_report.json"
            if ir.is_file():
                rep = json.loads(ir.read_text())
                print(f"    should_retry: {rep.get('should_retry')}  baseline: {rep.get('baseline')}  trigger: {rep.get('trigger_type')}")
