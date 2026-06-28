#!/usr/bin/env python3
"""Pick Pilot 15 instances from ContextBench Verified.

Sampling policy (frozen 2026-06-26 with user):
- Difficulty proxy: patch_line_count + file_count (no native difficulty in parquet)
- Python only (ConDiag v0 scope; parquet has 266/500 python)
- Strata: 5 medium + 10 large (medium=80-200 lines & 2-4 files, large=>200 lines or >4 files)
- Exclude already-run instances (27320d49 = scikit-learn__scikit-learn-25232)
- Deterministic: fixed random seed for reproducibility
- Output CSV: original_inst_id, instance_id, repo, patch_lines, n_files, stratum, source
"""
import csv
import json
import random
from pathlib import Path
import pyarrow.dataset as ds

PARQUET = "/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet"
OUT_CSV = "/mnt/d/condiag-artifacts/datasets/pilot_15.csv"
# scikit-learn__scikit-learn-25232: already used as smoke (27320d49 three-way).
# sphinx-doc__sphinx-9461: Docker Hub does not ship this image
#   (swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-doc-9461) — see
#   project_condiag_docker_mirror.md坑 I. Excluded so the resample picks a
#   medium instance whose image actually exists.
EXCLUDE_ORIGINAL_IDS = {"scikit-learn__scikit-learn-25232", "sphinx-doc__sphinx-9461"}
SEED = 20260626

random.seed(SEED)


def classify(patch: str) -> str:
    plines = max(0, patch.count("\n") - patch.count("\n@@"))
    nfiles = patch.count("diff --git a/")
    if plines < 30 and nfiles <= 1:
        return "tiny"
    if plines < 80 and nfiles <= 2:
        return "small"
    if plines < 200 and nfiles <= 4:
        return "medium"
    return "large"


def main():
    rows = ds.dataset(PARQUET, format="parquet").to_table().to_pylist()
    print(f"total rows: {len(rows)}")

    by_stratum = {"tiny": [], "small": [], "medium": [], "large": []}
    skipped_non_python = 0
    for r in rows:
        if r.get("language") != "python":
            skipped_non_python += 1
            continue
        oid = r.get("original_inst_id", "")
        if oid in EXCLUDE_ORIGINAL_IDS:
            print(f"  excluded (already run): {oid}")
            continue
        s = classify(r.get("patch", ""))
        plines = max(0, r["patch"].count("\n") - r["patch"].count("\n@@"))
        nfiles = r["patch"].count("diff --git a/")
        by_stratum[s].append({
            "original_inst_id": oid,
            "instance_id": r["instance_id"],
            "repo": r.get("repo", ""),
            "patch_lines": plines,
            "n_files": nfiles,
            "stratum": s,
            "source": r.get("source", ""),
        })

    print(f"skipped non-python: {skipped_non_python}")
    print("\nstratum sizes (after exclusion + python-only):")
    for k, v in by_stratum.items():
        print(f"  {k}: {len(v)}")

    n_medium = 5
    n_large = 10
    if len(by_stratum["medium"]) < n_medium:
        raise RuntimeError(f"not enough medium: {len(by_stratum['medium'])}")
    if len(by_stratum["large"]) < n_large:
        raise RuntimeError(f"not enough large: {len(by_stratum['large'])}")

    picked = (
        random.sample(by_stratum["medium"], n_medium)
        + random.sample(by_stratum["large"], n_large)
    )

    # Sort by repo then original_inst_id for readability
    picked.sort(key=lambda r: (r["repo"], r["original_inst_id"]))

    Path(OUT_CSV).parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(picked[0].keys()))
        w.writeheader()
        w.writerows(picked)

    print(f"\nwrote {len(picked)} instances to {OUT_CSV}")
    print(f"\n=== picked instances ===")
    for r in picked:
        print(f"  [{r['stratum']:6s}] {r['original_inst_id']:50s}  ({r['repo']}, {r['patch_lines']} lines / {r['n_files']} files)")


if __name__ == "__main__":
    main()
