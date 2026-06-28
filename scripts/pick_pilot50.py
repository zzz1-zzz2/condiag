"""Pick 50 ContextBench instances for Pilot50.

Composition (per user spec, 2026-06-27 EOD):
    - 10 local fresh    (use existing local images, ContextBench-annotated)
    - 15 Django error-code / system-check / validation (pull, score-based)
    - 10 Django config / database / router / backend   (pull, score-based)
    - 10 sympy                                          (pull, all sympy)
    -  5 NOOP control (ContextBench status=pass)        (pull, control sample)

All from contextbench_verified.parquet 500 (per project_condiag_contextbench_only.md).

Outputs:
    /mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_selected.csv
    /mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_selected.txt
    /mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_selected.md
    /mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_batch1.txt  (first 10 to run)

Usage:
    python3 scripts/pick_pilot50.py \\
        --parquet /home/swelite/condiag/ContextBench/data/contextbench_verified.parquet \\
        --selected-500 /home/swelite/condiag/ContextBench/data/selected_500_instances.csv \\
        --local-images /mnt/d/condiag-artifacts/docker/local_images.txt \\
        --out-dir /mnt/d/condiag-artifacts/condiag/v0/pilot50
"""
from __future__ import annotations

import argparse
import sys
from collections import OrderedDict
from pathlib import Path

import pandas as pd

# Reuse scoring from pick_relocalize_instances
sys.path.insert(0, str(Path(__file__).parent))
from pick_relocalize_instances import (  # noqa: E402
    SEED_CASES,
    score_one,
    load_local_images,
    match_local_image,
)


# Category-specific keyword signals (label must match those in KEYWORD_PATTERNS)
DJANGO_EC_KEYWORDS = {
    "framework error code (models.E###)",
    "bare error code (E### / W###)",
    "SystemCheckError",
    "system check",
    "check framework",
    "ValidationError",
    "validation",
}

DJANGO_OTHER_KEYWORDS = {
    "configuration",
    "settings",
    "ImproperlyConfigured",
    "database",
    "router",
    "backend",
    "collision",
    "exception",
    "traceback",
    "warning",
    "deprecation",
}


def select_pilot50(
    scored: list,
    local_images: list[str],
    pass_ids: set[str],
) -> "OrderedDict[str, list]":
    """Apply the 5-category selection. Returns ordered dict of category -> list."""
    # Tag local images first
    for s in scored:
        m = match_local_image(s.instance_id, local_images)
        if m:
            s.has_local_image = True
            s.matched_image = m

    # Sort all by final_score desc
    sorted_all = sorted(scored, key=lambda s: s.final_score, reverse=True)

    used: set[str] = set()
    cats: "OrderedDict[str, list]" = OrderedDict()

    # --- 1. local_fresh: 10 ---------------------------------------------
    # Use existing local images (10 available: 6 django + 1 sklearn + 1 astropy + 2 transformers)
    local_pool = [s for s in sorted_all if s.has_local_image and s.instance_id not in used]
    cats["local_fresh"] = local_pool[:10]
    used.update(s.instance_id for s in cats["local_fresh"])

    # --- 2. django_error_code: target 15, take all if fewer -----------
    # ContextBench 500 only has ~6 Django + strong EC keyword; that's a
    # dataset constraint, not a picker bug. Take whatever exists.
    def _has_kw(s, labels):
        return any(lbl in labels for lbl, _ in s.keyword_hits)

    django_ec_pool = [
        s for s in sorted_all
        if s.repo == "django/django"
        and s.instance_id not in used
        and _has_kw(s, DJANGO_EC_KEYWORDS)
    ]
    cats["django_error_code"] = django_ec_pool  # take all (target was 15)
    used.update(s.instance_id for s in cats["django_error_code"])

    # --- 3 + 4: split the EC deficit between django_other and sympy ---
    # Target composition after local(10) + EC(6 actual) + NOOP(5) = 21
    # Remaining 29 slots split: try 14 django_other + 15 sympy (close to 50/50)
    target_other_plus_sympy = 50 - len(cats["local_fresh"]) - len(cats["django_error_code"]) - 5  # NOOP=5
    half = target_other_plus_sympy // 2  # 14
    other_target = half
    sympy_target = target_other_plus_sympy - half  # 15

    django_other_pool = [
        s for s in sorted_all
        if s.repo == "django/django"
        and s.instance_id not in used
        and _has_kw(s, DJANGO_OTHER_KEYWORDS)
    ]
    cats["django_other"] = django_other_pool[:other_target]
    used.update(s.instance_id for s in cats["django_other"])

    sympy_pool = [
        s for s in sorted_all
        if s.repo == "sympy/sympy" and s.instance_id not in used
    ]
    cats["sympy"] = sympy_pool[:sympy_target]
    used.update(s.instance_id for s in cats["sympy"])

    # --- 5. noop_control: 5 --------------------------------------------
    # status=pass in selected_500_instances.csv (ContextBench already evaluated as pass)
    noop_pool = [
        s for s in sorted_all
        if s.instance_id in pass_ids and s.instance_id not in used
    ]
    # Prefer ones with low keyword_score (less likely to trigger false positive)
    noop_sorted = sorted(noop_pool, key=lambda s: (s.keyword_score, -s.final_score))
    cats["noop_control"] = noop_sorted[:5]
    used.update(s.instance_id for s in cats["noop_control"])

    return cats


def render_md(cats: "OrderedDict[str, list]") -> str:
    lines = [
        "# Pilot50 — ContextBench-only instance selection",
        "",
    ]
    # Dynamic composition line (categories may flex due to dataset constraints)
    parts = []
    for key, label in [
        ("local_fresh", "local fresh"),
        ("django_error_code", "Django EC"),
        ("django_other", "Django other"),
        ("sympy", "sympy"),
        ("noop_control", "NOOP control"),
    ]:
        parts.append(f"{len(cats[key])} {label}")
    lines.append("Composition: " + " + ".join(parts) + ".")
    lines.extend([
        "",
        "All from `contextbench_verified.parquet` 500.",
        "Note: Django EC capped at 6 (dataset constraint — only 6 Django instances in ContextBench verified 500 hit models.E### / SystemCheckError / ValidationError). Deficit reallocated to Django other + sympy.",
        "",
    ])
    cat_names = {
        "local_fresh": "1. Local fresh (existing image, no pull)",
        "django_error_code": "2. Django error-code / system-check / validation (pull)",
        "django_other": "3. Django config / database / router / backend (pull)",
        "sympy": "4. sympy (pull)",
        "noop_control": "5. NOOP control (ContextBench status=pass)",
    }
    for key, label in cat_names.items():
        items = cats[key]
        lines.append(f"## {label} ({len(items)})")
        lines.append("")
        lines.append("| # | Instance | Repo | Score | Local | Top keyword hits |")
        lines.append("|--:|----------|------|------:|:-----:|------------------|")
        for i, s in enumerate(items, 1):
            kw = ", ".join(lbl for lbl, _ in s.keyword_hits[:4]) or "—"
            local = "yes" if s.has_local_image else "no"
            lines.append(
                f"| {i} | `{s.instance_id}` | {s.repo} | {s.final_score} | {local} | {kw} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="Pick 50 ContextBench instances for Pilot50")
    ap.add_argument(
        "--parquet",
        default="/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet",
    )
    ap.add_argument(
        "--selected-500",
        default="/home/swelite/condiag/ContextBench/data/selected_500_instances.csv",
    )
    ap.add_argument(
        "--local-images",
        default="/mnt/d/condiag-artifacts/docker/local_images.txt",
    )
    ap.add_argument(
        "--out-dir",
        default="/mnt/d/condiag-artifacts/condiag/v0/pilot50",
    )
    args = ap.parse_args()

    # Load
    df = pd.read_parquet(args.parquet)
    print(f"[pilot50] loaded {len(df)} rows from parquet")

    sel = pd.read_csv(args.selected_500)
    pass_ids = set(sel[sel["status"] == "pass"]["original_inst_id"].astype(str))
    print(f"[pilot50] {len(pass_ids)} instances with status=pass in selected_500")

    local_images = load_local_images(Path(args.local_images))
    print(f"[pilot50] loaded {len(local_images)} local images")

    # Score
    scored = []
    for _, row in df.iterrows():
        s = score_one(row)
        if s is not None:
            scored.append(s)
    print(f"[pilot50] {len(scored)} python instances after exclusions")

    # Select
    cats = select_pilot50(scored, local_images, pass_ids)

    # Verify total
    total = sum(len(v) for v in cats.values())
    print(f"[pilot50] selected total: {total} (target 50)")
    for k, v in cats.items():
        local_n = sum(1 for s in v if s.has_local_image)
        print(f"  {k:20s} {len(v):3d}  (local: {local_n})")

    if total != 50:
        print(f"[WARN] expected 50, got {total}", file=sys.stderr)

    # Output
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # CSV
    rows = []
    cat_order = {k: i for i, k in enumerate(cats.keys())}
    for cat, items in cats.items():
        for rank, s in enumerate(items, 1):
            r = s.to_row()
            r["category"] = cat
            r["category_rank"] = rank
            r["category_order"] = cat_order[cat]
            rows.append(r)
    csv_path = out_dir / "pilot50_selected.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False, encoding="utf-8")
    print(f"[pilot50] -> {csv_path}")

    # TXT (just instance_ids in category order)
    txt_path = out_dir / "pilot50_selected.txt"
    all_ids = []
    for items in cats.values():
        all_ids.extend(s.instance_id for s in items)
    txt_path.write_text("\n".join(all_ids) + "\n", encoding="utf-8")
    print(f"[pilot50] -> {txt_path}")

    # MD
    md_path = out_dir / "pilot50_selected.md"
    md_path.write_text(render_md(cats), encoding="utf-8")
    print(f"[pilot50] -> {md_path}")

    # Batch 1 (first 10 to run):
    # 5 strong Django EC + 3 mid Django other + 2 sympy
    batch1 = (
        cats["django_error_code"][:5]
        + cats["django_other"][:3]
        + cats["sympy"][:2]
    )
    batch1_path = out_dir / "pilot50_batch1.txt"
    batch1_path.write_text(
        "\n".join(s.instance_id for s in batch1) + "\n", encoding="utf-8"
    )
    print(f"[pilot50] batch1 ({len(batch1)}) -> {batch1_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
