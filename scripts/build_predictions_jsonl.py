"""Build predictions JSONL from all batch traj.json files.

Reads `info.submission` from each traj, groups by batch+bench, outputs JSON.
Also emits a single master predictions_all.jsonl for cross-batch runs.

Usage:
    python build_predictions_jsonl.py --batch all
    python build_predictions_jsonl.py --batch condiag_batch3_20260706_205758
"""
import argparse
import glob
import json
import os
import sys
from collections import defaultdict


RUNS_ROOT = "/mnt/d/condiag-artifacts/runs"
OUT_ROOT = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions"


def detect_batch_label(batch_dir: str) -> str:
    """condiag_batch3_20260706_205758 → batch3
       condiag_batch5a_verified_11_20260708_161113 → batch5a
       condiag_batch5b_multi_16_20260709_121629 → batch5b
       condiag_batch5c_pro_16_20260709_125530 → batch5c
       condiag_batch5d_poly_16_20260708_192836 → batch5d
       pilot50_batch1_20260627_234801 → pilot50_batch1
       pilot50_batch2_20260628_114704 → pilot50_batch2
       pilot50_batch4_20260707_114055 → pilot50_batch4
    """
    name = os.path.basename(batch_dir)
    if name.startswith("pilot50_"):
        return name.replace("_2026", "_b_2026").replace("pilot50_batch1_b_", "pilot50_batch1_").split("_2026")[0] + "_" + name.split("_")[-2]
    if name.startswith("condiag_batch"):
        parts = name.split("_")
        return f"{parts[1]}_{parts[2]}"  # e.g. batch3, batch5a, batch5b, batch5c, batch5d
    return name


def detect_bench(batch_dir: str, traj_path: str) -> str:
    """Extract benchmark label from path."""
    parts = traj_path.split("/")
    for i, p in enumerate(parts):
        if p == "miniswe" and i + 1 < len(parts):
            sub = parts[i + 1]
            if sub in {"Verified", "Multi", "Pro", "Poly"}:
                return sub
    return "Unknown"


def collect_trajs(batch_dir: str) -> list[tuple[str, str, str]]:
    """Return list of (instance_id, traj_path, bench)."""
    out = []
    pat = f"{batch_dir}/miniswe/*/*/*.traj.json"
    for p in sorted(glob.glob(pat)):
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        iid = d.get("instance_id") or os.path.basename(p).replace(".traj.json", "")
        sub = d.get("info", {}).get("submission")
        if not sub or not sub.strip():
            continue  # skip empty submissions
        bench = detect_bench(batch_dir, p)
        out.append((iid, p, bench))
    return out


def build_predictions(batch_dir: str, out_dir: str):
    label = detect_batch_label(batch_dir)
    bench_dir = os.path.join(out_dir, label)
    os.makedirs(bench_dir, exist_ok=True)

    trajs = collect_trajs(batch_dir)
    if not trajs:
        return None

    by_bench: dict[str, list] = defaultdict(list)
    master = []

    for iid, traj_path, bench in trajs:
        with open(traj_path, "r", encoding="utf-8") as f:
            d = json.load(f)
        sub = d["info"]["submission"]

        # Convert Django name: instance_id in traj is e.g. "django__django-11820"
        # SWE-bench harness expects either format; we keep the traj's id as-is.
        pred = {
            "instance_id": iid,
            "model_name_or_path": f"miniswe_{label}",
            "model_patch": sub,
        }
        by_bench[bench].append(pred)
        master.append((bench, pred))

    # Per-bench predictions
    paths = {}
    for bench, preds in by_bench.items():
        path = os.path.join(bench_dir, f"predictions_{bench}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(preds, f, indent=2)
        paths[bench] = path

    # Master predictions_all.jsonl (mixed benches; harness auto-detects via instance_id)
    all_path = os.path.join(bench_dir, "predictions_all.jsonl")
    with open(all_path, "w", encoding="utf-8") as f:
        for bench, p in master:
            f.write(json.dumps(p) + "\n")

    return {
        "batch": label,
        "batch_dir": batch_dir,
        "n_total": len(trajs),
        "by_bench": {b: len(v) for b, v in by_bench.items()},
        "out_dir": bench_dir,
        "paths": paths,
        "all_jsonl": all_path,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", default="all", help="all | <batch_dir_name>")
    ap.add_argument("--out", default=OUT_ROOT)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    if args.batch == "all":
        targets = sorted(glob.glob(f"{RUNS_ROOT}/pilot50_batch*"))
        targets += sorted(glob.glob(f"{RUNS_ROOT}/condiag_batch*"))
    else:
        targets = [os.path.join(RUNS_ROOT, args.batch)]

    summary = []
    for bd in targets:
        # Skip empty/nohup dirs
        if "nohup" in bd:
            continue
        if not os.path.isdir(f"{bd}/miniswe"):
            continue
        r = build_predictions(bd, args.out)
        if r:
            summary.append(r)
            print(f"[OK] {r['batch']:25s} n={r['n_total']:3d} bench={r['by_bench']} → {r['out_dir']}")
        else:
            print(f"[--] {os.path.basename(bd):60s} no trajs")

    # Grand summary
    total = sum(s["n_total"] for s in summary)
    print(f"\n=== GRAND TOTAL: {total} predictions across {len(summary)} batches ===")
    for s in summary:
        print(f"  {s['batch']:25s} n={s['n_total']:3d} bench={s['by_bench']}")


if __name__ == "__main__":
    main()