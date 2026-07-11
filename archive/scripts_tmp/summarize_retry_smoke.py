"""Summarize retry smoke results."""
import json
from pathlib import Path
from experiments.manifest_builder import get_gold_patch
from experiments.packet_gold_overlap import parse_gold_patch

runs = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
baselines = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]
instances = ["django__django-13513", "sympy__sympy-19954", "django__django-11099"]

print("Retry Smoke Summary (3 cases x 4 baselines)")
print("=" * 90)
print(f"{'instance':<35s} {'baseline':<25s} {'a2_patch_chars':>12s} {'gold_inter':>10s} {'gold_recall':>10s}")
print("-" * 90)

for iid in instances:
    gold = parse_gold_patch(get_gold_patch(iid) or "")
    G = {(f, l) for f, ls in gold.items() for l in ls}
    for bl in baselines:
        a2 = runs / bl / iid / "attempt_2" / "patch.diff"
        a1 = runs / bl / iid / "attempt_1" / "patch.diff"
        a2_chars = a2.stat().st_size if a2.is_file() else 0
        a1_chars = a1.stat().st_size if a1.is_file() else 0
        inter = 0
        if a2.is_file() and a2_chars > 50:
            patch_text = a2.read_text()
            pg = parse_gold_patch(patch_text) if "diff --git" in patch_text or "@@" in patch_text else {}
            P = {(f, l) for f, ls in pg.items() for l in ls}
            inter = len(P & G)
        rec = round(inter/len(G), 3) if G else 0
        delta = a2_chars - a1_chars
        print(f"{iid:<35s} {bl:<25s} {a2_chars:>12d} {inter:>10d} {rec:>10.3f}")

print()
print("Note: gold_recall = |attempt_2_patch_lines ∩ gold_lines| / |gold_lines|")
print("This is ONLY gold-overlap, NOT resolved_rate (need docker eval).")
