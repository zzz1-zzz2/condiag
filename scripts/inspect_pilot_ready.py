#!/usr/bin/env python3
"""Extract mini-SWE traj stats for pilot_ready run."""
import json
from pathlib import Path

ROOT = Path("/mnt/d/condiag-artifacts/runs/pilot_ready_20260627_000516/miniswe/miniswe/Verified")
for traj in sorted(ROOT.glob("*/!exit_statuses*.traj.json")):
    inst = traj.parent.name
    d = json.load(open(traj))
    info = d.get("info", {})
    ms = info.get("model_stats", {})
    sub = info.get("submission", "") or ""
    adds = sum(1 for ln in sub.split("\n") if ln.startswith("+") and not ln.startswith("+++"))
    dels = sum(1 for ln in sub.split("\n") if ln.startswith("-") and not ln.startswith("---"))
    nfiles = sub.count("diff --git a/")
    print(f"=== {inst} ===")
    print(f"  exit_status  : {info.get('exit_status')}")
    print(f"  api_calls    : {ms.get('api_calls')}")
    print(f"  instance_cost: {ms.get('instance_cost')}")
    print(f"  patch bytes  : {len(sub)}")
    print(f"  additions    : {adds}")
    print(f"  deletions    : {dels}")
    print(f"  files changed: {nfiles}")
    print(f"  has diff     : {'diff --git' in sub}")
