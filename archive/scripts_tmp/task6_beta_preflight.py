"""Task 6-beta preflight: verify readiness for 2 packet-ready cases × 4 baselines.

Read-only. No agent launch, no eval, no artifact modification.

Checks per case (django__django-11820, django__django-13513):
  1. base_miniswe attempt_1 exists (patch.diff, raw_trajectory.json)
  2. failure_witness exists (failure_witness.json with has_failure_witness=true)
  3. context_packet v2-alpha exists (context_packet.md)
  4. intervention_report for v2-alpha exists (should_retry=true)
  5. api_navigation_hint exists
  6. broad_expansion intervention exists (for broad_expansion baseline)
  7. SWE-bench dataset entry exists for instance
  8. Docker image exists
  9. Plain/feedback retry artifacts already exist or need creation (Task 2/3 handlers)

Also checks:
  - New output root is clean (does not exist)
  - DEEPSEEK_API_KEY env (informational only, not required for preflight)
  - HF_HUB_OFFLINE / TRANSFORMERS_OFFLINE recommendations
"""
import hashlib
import json
import os
from pathlib import Path

# Paths
ARTIFACTS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0")
D4_9_RUNS = ARTIFACTS_ROOT / "d4_9_batch2_17x4" / "runs" / "miniswe"
BASE_MINISWE = D4_9_RUNS / "base_miniswe"
BROAD_EXPANSION_SRC = D4_9_RUNS / "broad_expansion"
CONTEXT_PACKET_V2_ALPHA = D4_9_RUNS / "context_packet_v2_alpha"
FAILURE_WITNESS_ROOT = ARTIFACTS_ROOT  # failure_witness/<iid>/failure_witness.json
API_NAVIGATION_ROOT = ARTIFACTS_ROOT  # api_navigation_hint/<iid>/...
NEW_OUTPUT_ROOT = ARTIFACTS_ROOT / "task6_beta_retry_smoke"

CASES = ["django__django-11820", "django__django-13513"]
BASELINES = ["plain_rerun", "feedback_retry", "broad_expansion", "condiag_retry_v2_alpha"]

print("=" * 80)
print("Task 6-beta preflight (read-only)")
print("=" * 80)

# Env
print()
print("=== Env ===")
print(f"  DEEPSEEK_API_KEY set: {'yes' if os.environ.get('DEEPSEEK_API_KEY') else 'NO (will need for agent launch)'}")
print(f"  HF_HUB_OFFLINE: {os.environ.get('HF_HUB_OFFLINE', 'unset')} (recommend =1 for runs)")
print(f"  TRANSFORMERS_OFFLINE: {os.environ.get('TRANSFORMERS_OFFLINE', 'unset')} (recommend =1 for runs)")

# New output root
print()
print("=== New output root ===")
print(f"  path: {NEW_OUTPUT_ROOT}")
print(f"  exists: {NEW_OUTPUT_ROOT.exists()} (should be False for clean run)")

# Per-case checks
for iid in CASES:
    print()
    print(f"=== {iid} ===")

    # 1. base_miniswe attempt_1
    a1 = BASE_MINISWE / iid / "attempt_1"
    patch = a1 / "patch.diff"
    traj = a1 / "raw_trajectory.json"
    print(f"  [1] base_miniswe attempt_1:")
    print(f"      patch.diff: {'OK ' + str(patch.stat().st_size) + ' bytes' if patch.is_file() else 'MISSING'}")
    print(f"      raw_trajectory.json: {'OK ' + str(traj.stat().st_size) + ' bytes' if traj.is_file() else 'MISSING'}")

    # 2. failure witness
    fw_paths = [
        FAILURE_WITNESS_ROOT / "failure_witness" / iid / "failure_witness.json",
        ARTIFACTS_ROOT / "failure_witness" / iid / "failure_witness.json",
    ]
    fw_found = None
    for p in fw_paths:
        if p.is_file():
            fw_found = p
            break
    print(f"  [2] failure_witness:")
    if fw_found:
        try:
            fw = json.loads(fw_found.read_text())
            print(f"      path: {fw_found}")
            print(f"      has_failure_witness: {fw.get('has_failure_witness')}")
            print(f"      mode: {fw.get('mode')}")
            print(f"      failure_type: {fw.get('failure_type', 'n/a')}")
        except Exception as e:
            print(f"      parse error: {e}")
    else:
        print(f"      MISSING (searched: {fw_paths})")

    # 3. context_packet v2-alpha
    cp = CONTEXT_PACKET_V2_ALPHA / iid / "intervention" / "context_packet.md"
    print(f"  [3] context_packet v2-alpha:")
    if cp.is_file():
        data = cp.read_bytes()
        md5 = hashlib.md5(data).hexdigest()
        print(f"      path: {cp}")
        print(f"      bytes: {len(data)}, md5: {md5}")
    else:
        print(f"      MISSING at {cp}")

    # 4. intervention_report v2-alpha
    ir = CONTEXT_PACKET_V2_ALPHA / iid / "intervention" / "intervention_report.json"
    print(f"  [4] intervention_report v2-alpha:")
    if ir.is_file():
        try:
            irpt = json.loads(ir.read_text())
            print(f"      path: {ir}")
            print(f"      should_retry: {irpt.get('should_retry')}")
            print(f"      trigger_type: {irpt.get('trigger_type')}")
            print(f"      baseline: {irpt.get('baseline')}")
        except Exception as e:
            print(f"      parse error: {e}")
    else:
        print(f"      MISSING at {ir}")

    # 5. api_navigation_hint
    api_paths = [
        ARTIFACTS_ROOT / "api_navigation_hint" / iid / "api_navigation.json",
        ARTIFACTS_ROOT / "api_navigation" / iid / "api_navigation.json",
        CONTEXT_PACKET_V2_ALPHA / iid / "intervention" / "api_navigation.json",
    ]
    api_found = None
    for p in api_paths:
        if p.is_file():
            api_found = p
            break
    print(f"  [5] api_navigation_hint:")
    if api_found:
        print(f"      path: {api_found}")
        try:
            api = json.loads(api_found.read_text())
            hints = api.get("hints", []) if isinstance(api, dict) else []
            print(f"      hints count: {len(hints)}")
            if hints:
                first = hints[0] if isinstance(hints, list) else None
                if isinstance(first, dict):
                    print(f"      first hint source: {first.get('hint_source')}")
                    print(f"      first hint target: {first.get('target_symbol', first.get('target_file', 'n/a'))}")
        except Exception as e:
            print(f"      parse error: {e}")
    else:
        print(f"      MISSING (searched: {api_paths})")

    # 6. broad_expansion intervention
    be_intervention = BROAD_EXPANSION_SRC / iid / "intervention"
    print(f"  [6] broad_expansion intervention:")
    if be_intervention.is_dir():
        files = sorted([f.name for f in be_intervention.iterdir()])
        print(f"      dir: {be_intervention}")
        print(f"      files: {files}")
        be_ir = be_intervention / "intervention_report.json"
        if be_ir.is_file():
            try:
                be_irpt = json.loads(be_ir.read_text())
                print(f"      should_retry: {be_irpt.get('should_retry')}")
                print(f"      trigger_type: {be_irpt.get('trigger_type')}")
            except Exception as e:
                print(f"      parse error: {e}")
    else:
        print(f"      MISSING at {be_intervention}")

    # 7. plain_rerun + feedback_retry intervention artifacts?
    # These are produced by baseline_handlers.py per-baseline; check if pre-existing
    for bl in ["plain_rerun", "feedback_retry"]:
        bl_intervention = D4_9_RUNS / bl / iid / "intervention"
        if bl_intervention.is_dir():
            print(f"  [7-{bl}] intervention dir EXISTS at {bl_intervention} (will reuse or regenerate?)")
        else:
            print(f"  [7-{bl}] intervention dir DOES NOT EXIST at {bl_intervention} (need to run baseline_handlers.py)")

# 8. Docker images
print()
print("=== Docker images ===")
import subprocess
r = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}"],
                   capture_output=True, text=True)
imgs = r.stdout.strip().split("\n")
for iid in CASES:
    # image name pattern: swebench/sweb.eval.x86_64.django_1776_django-11820:latest
    short = iid.split("__")[-1]  # django-11820
    matches = [m for m in imgs if short in m]
    print(f"  {iid}:")
    if matches:
        for m in matches:
            print(f"      {m}")
    else:
        print(f"      NO IMAGE FOUND (will need to build)")

# 9. SWE-bench dataset entries
print()
print("=== SWE-bench dataset (cached) ===")
try:
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    for iid in CASES:
        rows = [r for r in ds if r["instance_id"] == iid]
        if rows:
            r = rows[0]
            print(f"  {iid}: FOUND  base_commit={r['base_commit'][:10]}...  test_patch bytes={len(r['test_patch'])}")
        else:
            print(f"  {iid}: NOT IN Verified split")
except Exception as e:
    print(f"  dataset load error: {e}")

print()
print("=" * 80)
print("Preflight complete. Review above for any MISSING / NO IMAGE / NOT IN Verified entries.")
print("=" * 80)
