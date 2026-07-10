#!/usr/bin/env python3
"""Consolidate instance artifacts into target layout."""
import json, shutil, sys
from dataclasses import asdict
from pathlib import Path

ARTIFACTS = Path("/mnt/d/condiag-artifacts/condiag")
RUNS = Path("/mnt/d/condiag-artifacts/runs")
INSTANCES = ARTIFACTS / "instances"
MANIFEST_PATH = ARTIFACTS / "manifests" / "instances_v1.jsonl"
EVAL_LOGS_ROOT = Path.home() / "condiag" / "logs" / "run_evaluation"
EVAL_LOG_SOURCES = [
    ARTIFACTS / "v0" / "eval_predictions" / "swebench_verified_official",
    ARTIFACTS / "v0" / "eval_predictions" / "swebench_multi_official",
    ARTIFACTS / "v0" / "eval_predictions" / "swebench_poly_official",
    EVAL_LOGS_ROOT / "miniswe_v4pro_eval",
    EVAL_LOGS_ROOT / "miniswe_verified_new32",
    EVAL_LOGS_ROOT / "missing_base_eval",
]
RUN_PRIORITY = [
    "condiag_batch5d_poly_16_20260709_205021",
    "condiag_batch5c_pro_16_20260709_125530",
    "condiag_batch5b_multi_16_20260709_121629",
    "condiag_batch5a_verified_11_20260708_161113",
    "pilot50_batch4_20260707_114055",
    "condiag_batch3_20260706_205758",
    "pilot50_batch2_20260628_114704",
    "pilot50_batch1_20260627_234801",
    "stage1_miniswe_verified_20260627_102536",
    "pilot50_sanity_20260627_224937",
    "pilot_ready_20260627_000516",
    "m0_miniswe_27320d49",
    "m1_miniswe_smoke_27320d49",
    "p0_miniswe_astropy_20260627_100054",
]

def load_manifest():
    instances = []
    with open(MANIFEST_PATH) as f:
        for line in f:
            line = line.strip()
            if line: instances.append(json.loads(line))
    return instances

def find_best_traj(instance_id):
    for run_name in RUN_PRIORITY:
        run_dir = RUNS / run_name
        if not run_dir.exists(): continue
        for traj_path in run_dir.rglob(f"{instance_id}.traj.json"):
            if traj_path.is_file(): return traj_path
    for run_dir in sorted(RUNS.iterdir()):
        if not run_dir.is_dir(): continue
        for traj_path in run_dir.rglob(f"{instance_id}.traj.json"):
            if traj_path.is_file(): return traj_path
    return None

def find_eval_log(instance_id):
    for source_dir in EVAL_LOG_SOURCES[:3]:
        inst_dir = source_dir / instance_id
        if inst_dir.is_dir():
            log_file = inst_dir / "test_output.log"
            if log_file.exists(): return log_file
    for source_dir in EVAL_LOG_SOURCES[3:]:
        for inst_dir in source_dir.rglob(instance_id):
            if inst_dir.is_dir():
                log_file = inst_dir / "test_output.txt"
                if log_file.exists(): return log_file
    return None

def copy_trajectory(instance_id, dry_run=False):
    target = INSTANCES / instance_id / "attempt_1" / "trajectory.json"
    if target.exists(): return True
    source = find_best_traj(instance_id)
    if source is None:
        print(f"  [MISS] {instance_id}: no trajectory in runs/")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if not dry_run: shutil.copy2(source, target)
    print(f"  [COPY] {instance_id}: -> trajectory.json")
    return True

def extract_patch(instance_id, dry_run=False):
    target = INSTANCES / instance_id / "attempt_1" / "patch.diff"
    if target.exists(): return True
    traj_path = INSTANCES / instance_id / "attempt_1" / "trajectory.json"
    if not traj_path.exists():
        source = find_best_traj(instance_id)
        if source is None: return False
        with open(source) as f: traj = json.load(f)
    else:
        with open(traj_path) as f: traj = json.load(f)
    sub = traj.get("info", {}).get("submission", "")
    if not sub or not isinstance(sub, str):
        print(f"  [NOPATCH] {instance_id}: no submission")
        return False
    if not dry_run: target.write_text(sub)
    print(f"  [PATCH] {instance_id}: patch.diff ({len(sub)}b)")
    return True

def build_witness(instance_id, dry_run=False):
    target = INSTANCES / instance_id / "attempt_1" / "failure_witness.json"
    if target.exists(): return True
    sys.path.insert(0, str(Path.home() / "condiag"))
    try:
        from experiments.failure_witness_builder import build_failure_witness as bfw
    except ImportError:
        print(f"  [SKIP] {instance_id}: no fw_builder")
        return False
    eval_log = find_eval_log(instance_id)
    if eval_log is None:
        to = INSTANCES / instance_id / "attempt_1" / "test_output.txt"
        if to.exists() and to.stat().st_size > 100: eval_log = to
    if eval_log is None:
        print(f"  [NOWITNESS] {instance_id}: no eval log")
        return False
    if not dry_run:
        witness = bfw(instance_id, eval_log_path=eval_log)
        from dataclasses import asdict
        target.write_text(json.dumps(asdict(witness), indent=2))
    print(f"  [WITNESS] {instance_id}: from {eval_log.name}")
    return True

def extract_agent_info(instance_id, dry_run=False):
    target = INSTANCES / instance_id / "attempt_1" / "agent_info.json"
    if target.exists(): return True
    traj_path = INSTANCES / instance_id / "attempt_1" / "trajectory.json"
    if not traj_path.exists():
        source = find_best_traj(instance_id)
        if source is None: return False
        with open(source) as f: traj = json.load(f)
    else:
        with open(traj_path) as f: traj = json.load(f)
    info = traj.get("info", {})
    ai = {"instance_id": instance_id,
          "exit_status": info.get("exit_status", "unknown"),
          "model_stats": info.get("model_stats", {}),
          "config": info.get("config", {}),
          "mini_version": info.get("mini_version", "")}
    if not dry_run: target.write_text(json.dumps(ai, indent=2))
    print(f"  [INFO] {instance_id}: agent_info.json")
    return True

def main():
    dry_run = "--dry-run" in sys.argv
    manifest = load_manifest()
    ids = [d["instance_id"] for d in manifest]
    resolved = sum(1 for d in manifest if d.get("resolved", False))
    print(f"Manifest: {len(ids)} instances ({resolved} resolved, {len(ids)-resolved} ff)")
    s = {"tc":0,"te":0,"tm":0,"pe":0,"pp":0,"pm":0,"we":0,"wb":0,"wm":0,"ai":0}
    for iid in ids:
        if (INSTANCES / iid / "attempt_1" / "trajectory.json").exists(): s["te"]+=1
        elif copy_trajectory(iid, dry_run): s["tc"]+=1
        else: s["tm"]+=1
        if (INSTANCES / iid / "attempt_1" / "patch.diff").exists(): s["pe"]+=1
        elif extract_patch(iid, dry_run): s["pp"]+=1
        else: s["pm"]+=1
        if (INSTANCES / iid / "attempt_1" / "failure_witness.json").exists(): s["we"]+=1
        elif build_witness(iid, dry_run): s["wb"]+=1
        else: s["wm"]+=1
        if not (INSTANCES / iid / "attempt_1" / "agent_info.json").exists():
            if extract_agent_info(iid, dry_run): s["ai"]+=1
    n = len(ids)
    print(f"\nTrajectory: {s['tc']}+{s['te']}/{n} ({s['tm']} miss)")
    print(f"Patch:      {s['pp']}+{s['pe']}/{n} ({s['pm']} miss)")
    print(f"Witness:    {s['wb']}+{s['we']}/{n} ({s['wm']} miss)")
    print(f"AgentInfo:  {s['ai']} created")

if __name__ == "__main__": main()
