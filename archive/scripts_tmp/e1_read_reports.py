"""Read 4 detailed per-instance swebench reports and summarize."""
import json
from pathlib import Path

BASELINES = ["plain_rerun", "feedback_retry", "broad_expansion", "condiag_retry_v2_alpha"]
INSTANCE = "django__django-12125"

print("=== E1 detailed report summary ===")
for bl in BASELINES:
    p = Path(f"/home/swelite/condiag/logs/run_evaluation/{bl}/condiag-task6-alpha-e1-{bl}/{INSTANCE}/report.json")
    if not p.is_file():
        print(f"  {bl}: report NOT FOUND at {p}")
        continue
    d = json.loads(p.read_text())[INSTANCE]
    ts = d["tests_status"]
    f2p = ts["FAIL_TO_PASS"]
    p2p = ts["PASS_TO_PASS"]
    f2p_total = len(f2p["success"]) + len(f2p["failure"])
    p2p_total = len(p2p["success"]) + len(p2p["failure"])
    print(f"  {bl}:")
    print(f"    patch_applied:      {d['patch_successfully_applied']}")
    print(f"    resolved:           {d['resolved']}")
    print(f"    F2P pass/fail/total: {len(f2p['success'])}/{len(f2p['failure'])}/{f2p_total}")
    print(f"    P2P pass/regress/tot:{len(p2p['success'])}/{len(p2p['failure'])}/{p2p_total}")
    if f2p["failure"]:
        print(f"    F2P failures:       {f2p['failure']}")
    if p2p["failure"]:
        print(f"    P2P regressions:    {p2p['failure']}")
    print()
