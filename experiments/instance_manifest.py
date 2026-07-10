#!/usr/bin/env python3
"""Build instance manifest from all legacy data sources."""
from __future__ import annotations

import json, csv, sys
from pathlib import Path
from collections import OrderedDict

# Append project root so we can import experiment_settings
_PROJECT_ROOT = Path("/home/swelite/condiag")
sys.path.insert(0, str(_PROJECT_ROOT / "experiments"))
from experiment_settings import (
    ARTIFACT_ROOT, LEGACY_DIR, MANIFESTS_DIR,
    LEGACY_EVAL_PREDICTIONS, LEGACY_FAILURE_WITNESS,
    LEGACY_CASE_BUNDLES, LEGACY_PILOT50,
    POOL_SOLVED, POOL_FIRST_FAILED, POOL_TIMEOUT, POOL_PENDING,
    BENCHMARK_VERIFIED, BENCHMARK_PRO, BENCHMARK_MULTI, BENCHMARK_POLY,
    ALL_BENCHMARKS,
)

MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

# =====================================================================
# Helper: load JSON with error handling
# =====================================================================

def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  [WARN] Failed to load {path}: {e}", flush=True)
        return None

# =====================================================================
# Source 1: ContextBench results (99 instances)
# =====================================================================

def load_contextbench():
    path = LEGACY_EVAL_PREDICTIONS / "contextbench_results" / "results_all.jsonl"
    if not path.exists():
        print("  [WARN] contextbench results not found", flush=True)
        return {}
    results = {}
    with open(path) as f:
        for line in f:
            d = json.loads(line)
            iid = d.get("instance_id")
            if iid:
                results[iid] = d
    return results

# =====================================================================
# Source 2: Official eval results (multiple formats)
# =====================================================================

def load_official_verified_new32():
    """new32 has: {n_total, n_resolved, results: {iid: {resolved, ...}}}"""
    path = LEGACY_EVAL_PREDICTIONS / "swebench_verified_new32" / "miniswe_verified_new32_results.json"
    data = load_json(path)
    if not data:
        return {}
    results = {}
    for iid, r in data.get("results", {}).items():
        results[iid] = {"resolved": r.get("resolved"), "source": "new32"}
        if "tests_status" in r:
            results[iid]["tests_status"] = r["tests_status"]
    return results

def load_official_verified_20():
    """20 missing: {iid: {resolved, f2p_passed, ...}}"""
    path = LEGACY_EVAL_PREDICTIONS / "swebench_verified_official" / "verified_eval_results.json"
    data = load_json(path)
    if not data:
        return {}
    results = {}
    for iid, r in data.items():
        results[iid] = {"resolved": r.get("resolved"), "source": "v20"}
        if "apply_error" in r or "patch_apply" in r:
            results[iid]["patch_apply_fail"] = not r.get("patch_apply", True)
    return results

def load_official_pro():
    """Pro: {n_total, n_resolved, results: {iid: {resolved, ...}}}"""
    path = LEGACY_EVAL_PREDICTIONS / "swebench_pro_official" / "pro_eval_results.json"
    data = load_json(path)
    if not data:
        return {}
    results = {}
    for iid, r in data.get("results", {}).items():
        results[iid] = {"resolved": r.get("resolved"), "source": "pro"}
    return results

def load_official_multi():
    """Multi: {iid: {resolved, passed, failed, error?}}"""
    path = LEGACY_EVAL_PREDICTIONS / "swebench_multi_official" / "multi_eval_results.json"
    data = load_json(path)
    if not data:
        return {}
    results = {}
    for iid, r in data.items():
        if "error" in r:
            results[iid] = {"resolved": None, "error": r["error"], "source": "multi"}
        else:
            results[iid] = {"resolved": r.get("resolved"), "source": "multi"}
    return results

def load_official_poly():
    """Poly: {iid: {resolved, f2p_passed, ...}}"""
    path = LEGACY_EVAL_PREDICTIONS / "swebench_poly_official" / "poly_eval_results.json"
    data = load_json(path)
    if not data:
        return {}
    results = {}
    for iid, r in data.items():
        results[iid] = {"resolved": r.get("resolved"), "source": "poly"}
    return results

# =====================================================================
# Source 3: Trajectories
# =====================================================================

def find_trajectories():
    """Scan for raw_trajectory.json in all known locations."""
    traj_map = {}  # instance_id -> [paths]
    search_roots = [
        LEGACY_CASE_BUNDLES,
        LEGACY_PILOT50 / "case_bundles",
    ]
    for root in search_roots:
        if not root.exists():
            continue
        for child in root.iterdir():
            if child.is_dir():
                tj = child / "raw_trajectory.json"
                if tj.exists():
                    iid = child.name
                    traj_map.setdefault(iid, []).append(str(tj))
    return traj_map

# =====================================================================
# Source 4: Failure witnesses
# =====================================================================

def find_failure_witnesses():
    fw_map = {}
    root = LEGACY_FAILURE_WITNESS
    if root.exists():
        for child in root.iterdir():
            if child.is_dir():
                fw = child / "failure_witness.json"
                if fw.exists():
                    fw_map[child.name] = str(fw)
    return fw_map

# =====================================================================
# Source 5: Benchmarks assignment (map instance_id -> benchmark)
# =====================================================================

def assign_benchmark(instance_id: str) -> str:
    if instance_id.startswith("instance_"):
        if "element-web" in instance_id:
            return BENCHMARK_PRO  # Pro-style
        return BENCHMARK_PRO
    if instance_id.startswith("alibaba__") or instance_id.startswith("facebook__") or instance_id.startswith("grpc__") or \
       instance_id.startswith("BurntSushi__") or instance_id.startswith("cli__") or instance_id.startswith("jqlang__") or \
       instance_id.startswith("axios__") or instance_id.startswith("catchorg__") or instance_id.startswith("clap-rs__") or \
       instance_id.startswith("darkreader__") or instance_id.startswith("elastic__") or instance_id.startswith("expressjs__") or \
       instance_id.startswith("fmtlib__") or instance_id.startswith("nlohmann__") or instance_id.startswith("ponylang__"):
        return BENCHMARK_MULTI
    if instance_id.startswith("Significant-Gravitas__") or instance_id.startswith("huggingface__") or \
       instance_id.startswith("keras-team__") or instance_id.startswith("langchain-ai__") or \
       instance_id.startswith("microsoft__") or instance_id.startswith("mui__") or \
       instance_id.startswith("prettier__") or instance_id.startswith("serverless__") or \
       instance_id.startswith("sveltejs__") or instance_id.startswith("yt-dlp__"):
        return BENCHMARK_POLY
    # Default: Verified (all django, sympy, astropy, scikit-learn, plus element-web/mui-34337)
    repo = instance_id.split("__")[0] if "__" in instance_id else ""
    if repo in ("django", "sympy", "astropy", "scikit-learn"):
        return BENCHMARK_VERIFIED
    if "mui__material-ui-34337" in instance_id:
        return BENCHMARK_MULTI  # Multi-style
    return BENCHMARK_VERIFIED  # fallback

# =====================================================================
# Main
# =====================================================================

print("Loading data sources...", flush=True)

cb_data = load_contextbench()
print(f"  ContextBench: {len(cb_data)} instances", flush=True)

official_new32 = load_official_verified_new32()
official_v20 = load_official_verified_20()
official_pro = load_official_pro()
official_multi = load_official_multi()
official_poly = load_official_poly()

# Merge official eval data (v20 takes precedence over new32 for overlapping instance IDs)
official_all = {}
official_all.update(official_new32)
# Only merge v20 for instances not in new32 (they're disjoint sets)
official_all.update(official_v20)  # v20 has no overlap with new32
official_all.update({k: v for k, v in official_pro.items() if k not in official_all})
official_all.update({k: v for k, v in official_multi.items() if k not in official_all})
official_all.update({k: v for k, v in official_poly.items() if k not in official_all})
print(f"  Official eval merged: {len(official_all)}", flush=True)

traj_map = find_trajectories()
print(f"  Trajectories: {len(traj_map)} instances", flush=True)

fw_map = find_failure_witnesses()
print(f"  Failure witnesses: {len(fw_map)}", flush=True)

# Determine the 99-instance list from ContextBench (truth)
instance_ids = sorted(cb_data.keys())
print(f"  Total from ContextBench: {len(instance_ids)}", flush=True)

# Build manifest
manifest = OrderedDict()
for iid in instance_ids:
    meta = cb_data.get(iid, {})
    off = official_all.get(iid, {})

    resolved = off.get("resolved")
    error = off.get("error")

    # Assign pool
    if resolved is True:
        pool = POOL_SOLVED
    elif resolved is False:
        pool = POOL_FIRST_FAILED
    elif error and "timeout" in str(error):
        pool = POOL_TIMEOUT
    elif error:
        pool = POOL_TIMEOUT  # treat other errors as timeout/pending
    else:
        pool = POOL_PENDING

    benchmark = assign_benchmark(iid)
    repo = iid.split("__")[0] if "__" in iid else iid
    lang = "python"
    if benchmark == BENCHMARK_MULTI:
        lang = "multi"
    elif benchmark == BENCHMARK_POLY:
        lang = "python"

    entry = OrderedDict([
        ("instance_id", iid),
        ("benchmark", benchmark),
        ("repo", repo),
        ("language", lang),
        ("pool", pool),
        ("resolved", resolved),
        ("attempt1_status", "resolved" if resolved else "failed" if resolved is False else "unknown"),
        ("has_trajectory", iid in traj_map),
        ("has_failure_witness", iid in fw_map),
        ("has_contextbench_metrics", True),
        ("has_official_eval", iid in official_all),
        ("patch_apply_fail", off.get("patch_apply_fail", False)),
        ("legacy_trajectory_paths", traj_map.get(iid, [])),
        ("legacy_failure_witness_path", fw_map.get(iid, "")),
        ("legacy_official_eval_source", off.get("source", "")),
        ("notes", ""),
    ])
    manifest[iid] = entry

# Stats
pools = {}
for e in manifest.values():
    p = e["pool"]
    pools[p] = pools.get(p, 0) + 1
print(f"\nPool distribution: {pools}", flush=True)
print(f"  resolved: {sum(1 for e in manifest.values() if e['resolved'] is True)}", flush=True)
print(f"  first_failed: {sum(1 for e in manifest.values() if e['resolved'] is False)}", flush=True)
print(f"  has_trajectory: {sum(1 for e in manifest.values() if e['has_trajectory'])}", flush=True)
print(f"  has_failure_witness: {sum(1 for e in manifest.values() if e['has_failure_witness'])}", flush=True)

# Write JSONL
jsonl_path = MANIFESTS_DIR / "instances_v1.jsonl"
with open(jsonl_path, "w") as f:
    for entry in manifest.values():
        f.write(json.dumps(entry) + "\n")
print(f"\nWrote {jsonl_path}", flush=True)

# Write CSV
csv_path = MANIFESTS_DIR / "instances_v1.csv"
with open(csv_path, "w", newline="") as f:
    w = csv.writer(f)
    headers = list(manifest[next(iter(manifest))].keys())
    w.writerow(headers)
    for entry in manifest.values():
        row = [str(entry.get(h, "")) for h in headers]
        w.writerow(row)
print(f"Wrote {csv_path}", flush=True)

# Print first-failed list
print(f"\nFirst-failed instances ({pools.get(POOL_FIRST_FAILED, 0)}):")
for iid, e in manifest.items():
    if e["pool"] == POOL_FIRST_FAILED:
        traj_mark = " T" if e["has_trajectory"] else ""
        fw_mark = " FW" if e["has_failure_witness"] else ""
        print(f"  {iid}{traj_mark}{fw_mark}")
