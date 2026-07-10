#!/usr/bin/env python3
"""Step 4: Migrate legacy artifacts to new instance-centric layout.

Reads instances_v1.jsonl manifest, copies data from legacy locations
to /mnt/d/condiag-artifacts/condiag/instances/<id>/attempt_1/.
Validates checksums after copy. Does NOT delete originals.
"""
from __future__ import annotations

import json, shutil, hashlib, sys
from pathlib import Path

_PROJECT_ROOT = Path("/home/swelite/condiag")
sys.path.insert(0, str(_PROJECT_ROOT / "experiments"))
from experiment_settings import (
    ARTIFACT_ROOT, MANIFESTS_DIR, INSTANCES_DIR, LEGACY_DIR, LEGACY_EVAL_PREDICTIONS,
    LEGACY_FAILURE_WITNESS, LEGACY_CASE_BUNDLES, LEGACY_PILOT50,
    FN_TRAJECTORY, FN_PATCH, FN_OFFICIAL_EVAL, FN_TEST_OUTPUT,
    FN_CONTEXTBENCH_METRICS, FN_FAILURE_WITNESS,
)

INSTANCES_DIR.mkdir(parents=True, exist_ok=True)

# =====================================================================
# Load manifest
# =====================================================================

manifest = {}
manifest_path = MANIFESTS_DIR / "instances_v1.jsonl"
with open(manifest_path) as f:
    for line in f:
        entry = json.loads(line)
        manifest[entry["instance_id"]] = entry
print(f"Loaded {len(manifest)} instances from manifest", flush=True)

# =====================================================================
# Hash validation
# =====================================================================

def sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return "ERROR"

# =====================================================================
# Migration log
# =====================================================================

migration_log = []  # list of {instance_id, file_type, source, dest, status, hash_match}

def log(instance_id, file_type, source, dest, status, hash_match=""):
    migration_log.append({
        "instance_id": instance_id,
        "file_type": file_type,
        "source": str(source) if source else "",
        "dest": str(dest) if dest else "",
        "status": status,
        "hash_match": hash_match,
    })

def copy_validated(src, dst, instance_id, file_type):
    """Copy file, verify hash."""
    if not src.exists():
        log(instance_id, file_type, src, dst, "SKIP_source_missing")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    src_hash = sha256(src)
    shutil.copy2(str(src), str(dst))
    dst_hash = sha256(dst)
    match = src_hash == dst_hash
    status = "OK" if match else "HASH_MISMATCH"
    log(instance_id, file_type, src, dst, status, f"src={src_hash[:12]} dst={dst_hash[:12]}")
    if not match:
        print(f"  [HASH] {instance_id} {file_type}: source/dest mismatch!", flush=True)
    return match

# =====================================================================
# 1. ContextBench metrics (from results_all.jsonl)
# =====================================================================

def migrate_contextbench():
    """Copy ContextBench metrics for each instance from the aggregated JSONL."""
    src_path = LEGACY_EVAL_PREDICTIONS / "contextbench_results" / "results_all.jsonl"
    if not src_path.exists():
        print("  [SKIP] contextbench results not found", flush=True)
        return
    entries = {}
    with open(src_path) as f:
        for line in f:
            d = json.loads(line)
            iid = d.get("instance_id")
            if iid:
                entries[iid] = d

    count = 0
    for iid, entry in manifest.items():
        if iid not in entries:
            continue
        dst = INSTANCES_DIR / iid / "attempt_1" / FN_CONTEXTBENCH_METRICS
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w") as f:
            json.dump(entries[iid], f, indent=1)
        log(iid, "contextbench_metrics", src_path, dst, "OK")
        count += 1
    print(f"  ContextBench metrics: {count} instances", flush=True)

# =====================================================================
# 2. Official eval results
# =====================================================================

def migrate_official_eval_verified_new32():
    """new32 has per-instance test details in the big JSON."""
    src_path = LEGACY_EVAL_PREDICTIONS / "swebench_verified_new32" / "miniswe_verified_new32_results.json"
    if not src_path.exists():
        return 0
    with open(src_path) as f:
        data = json.load(f)
    count = 0
    for iid, r in data.get("results", {}).items():
        if iid not in manifest:
            continue
        dst = INSTANCES_DIR / iid / "attempt_1" / FN_OFFICIAL_EVAL
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w") as f:
            json.dump(r, f, indent=1)
        log(iid, "official_eval", src_path, dst, "OK")
        count += 1
    return count

def migrate_official_eval_pro():
    """Pro format: {n_total, n_resolved, results: {iid: {...}}}."""
    src_json = LEGACY_EVAL_PREDICTIONS / "swebench_pro_official" / "pro_eval_results.json"
    if not src_json.exists():
        return 0
    with open(src_json) as f:
        data = json.load(f)
    count = 0
    for iid, r in data.get("results", {}).items():
        if iid not in manifest:
            continue
        dst = INSTANCES_DIR / iid / "attempt_1" / FN_OFFICIAL_EVAL
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w") as f:
            json.dump(r, f, indent=1)
        log(iid, "official_eval", src_json, dst, "OK")
        count += 1
    return count

def migrate_official_eval_direct(src_json):
    """Direct format: {iid: {...}} — one key per instance."""
    if not src_json.exists():
        return 0
    with open(src_json) as f:
        data = json.load(f)
    count = 0
    for iid, r in data.items():
        if iid not in manifest:
            continue
        dst = INSTANCES_DIR / iid / "attempt_1" / FN_OFFICIAL_EVAL
        dst.parent.mkdir(parents=True, exist_ok=True)
        with open(dst, "w") as f:
            json.dump(r, f, indent=1)
        log(iid, "official_eval", src_json, dst, "OK")
        count += 1
    return count

def migrate_test_outputs():
    """Copy individual test output logs where available."""
    # Verified 20 missing
    root = LEGACY_EVAL_PREDICTIONS / "swebench_verified_official"
    count = 0
    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                tl = child / "test_output.log"
                if tl.exists():
                    iid = child.name
                    if iid in manifest:
                        dst = INSTANCES_DIR / iid / "attempt_1" / FN_TEST_OUTPUT
                        copy_validated(tl, dst, iid, "test_output")

    # Pro: swebench_pro_official/<instance_id>/.../test_output.txt
    root = LEGACY_EVAL_PREDICTIONS / "swebench_pro_official"
    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                for tl in child.rglob("test_output.txt"):
                    iid = child.name
                    if iid in manifest:
                        dst = INSTANCES_DIR / iid / "attempt_1" / FN_TEST_OUTPUT
                        copy_validated(tl, dst, iid, "test_output")
                        break  # only first found
    return count

# =====================================================================
# 3. Trajectories
# =====================================================================

def migrate_trajectories():
    """Copy raw_trajectory.json from all known legacy locations."""
    count = 0
    processed = set()
    roots = [
        LEGACY_CASE_BUNDLES,
        LEGACY_PILOT50 / "case_bundles",
    ]
    for root in roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            if child.is_dir():
                iid = child.name
                if iid in processed or iid not in manifest:
                    continue
                tj = child / "raw_trajectory.json"
                if tj.exists():
                    dst = INSTANCES_DIR / iid / "attempt_1" / FN_TRAJECTORY
                    ok = copy_validated(tj, dst, iid, "trajectory")
                    if ok:
                        count += 1
                        processed.add(iid)
    return count

# =====================================================================
# 4. Failure witnesses
# =====================================================================

def migrate_failure_witnesses():
    count = 0
    root = LEGACY_FAILURE_WITNESS
    if not root.exists():
        return 0
    for child in root.iterdir():
        if child.is_dir():
            iid = child.name
            if iid not in manifest:
                continue
            fw = child / "failure_witness.json"
            if fw.exists():
                dst = INSTANCES_DIR / iid / "attempt_1" / FN_FAILURE_WITNESS
                ok = copy_validated(fw, dst, iid, "failure_witness")
                if ok:
                    count += 1
    return count

# =====================================================================
# Run all migrations
# =====================================================================

print("=== Migrating data to new layout ===\n", flush=True)

print("[1/6] ContextBench metrics...", flush=True)
migrate_contextbench()

print("[2/6] Official eval (Verified new32)...", flush=True)
c1 = migrate_official_eval_verified_new32()
print(f"  -> {c1} instances", flush=True)

print("[3/6] Official eval (Verified 20)...", flush=True)
c2 = migrate_official_eval_direct(
    LEGACY_EVAL_PREDICTIONS / "swebench_verified_official" / "verified_eval_results.json",
)
print(f"  -> {c2} instances", flush=True)

print("[4/6] Official eval (Pro, Multi, Poly)...", flush=True)
c3 = migrate_official_eval_pro()
c4 = migrate_official_eval_direct(
    LEGACY_EVAL_PREDICTIONS / "swebench_multi_official" / "multi_eval_results.json",
)
c5 = migrate_official_eval_direct(
    LEGACY_EVAL_PREDICTIONS / "swebench_poly_official" / "poly_eval_results.json",
)
print(f"  -> Pro={c3} Multi={c4} Poly={c5}", flush=True)

print("[5/6] Test output logs...", flush=True)
migrate_test_outputs()

print("[6/6] Trajectories + Failure Witnesses...", flush=True)
c_traj = migrate_trajectories()
c_fw = migrate_failure_witnesses()
print(f"  -> trajectories={c_traj} failure_witnesses={c_fw}", flush=True)

# =====================================================================
# Summary
# =====================================================================

n_attempt_dirs = sum(1 for p in INSTANCES_DIR.iterdir() if (p / "attempt_1").exists())
n_traj = sum(1 for p in INSTANCES_DIR.iterdir() if (p / "attempt_1" / FN_TRAJECTORY).exists())
n_eval = sum(1 for p in INSTANCES_DIR.iterdir() if (p / "attempt_1" / FN_OFFICIAL_EVAL).exists())
n_cb = sum(1 for p in INSTANCES_DIR.iterdir() if (p / "attempt_1" / FN_CONTEXTBENCH_METRICS).exists())
n_fw = sum(1 for p in INSTANCES_DIR.iterdir() if (p / "attempt_1" / FN_FAILURE_WITNESS).exists())

print(f"\n=== Migration Summary ===", flush=True)
print(f"  Instance dirs with attempt_1/: {n_attempt_dirs}", flush=True)
print(f"  trajectories:               {n_traj}", flush=True)
print(f"  official_eval:               {n_eval}", flush=True)
print(f"  contextbench_metrics:        {n_cb}", flush=True)
print(f"  failure_witness:             {n_fw}", flush=True)

# Write migration log
log_path = MANIFESTS_DIR / "migration_log_v1.json"
with open(log_path, "w") as f:
    json.dump(migration_log, f, indent=1)
print(f"\nMigration log: {log_path}", flush=True)

# Validation: compare counts with manifest
print(f"\n=== Validation (manifest expects) ===", flush=True)
print(f"  has_trajectory: {sum(1 for e in manifest.values() if e['has_trajectory'])}", flush=True)
print(f"  has_failure_witness: {sum(1 for e in manifest.values() if e['has_failure_witness'])}", flush=True)
print(f"  has_official_eval: {sum(1 for e in manifest.values() if e['has_official_eval'])}", flush=True)
print(f"  has_contextbench_metrics: 99", flush=True)
