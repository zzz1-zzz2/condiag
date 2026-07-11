"""D4-9 Step 4c — Broad Expansion source ablation.

Question: Broad Expansion won the incremental_overlap metric vs ConDiag.
But Broad has THREE candidate sources bundled together:

    EDITED_FILE_WINDOW       — ±40 around attempt_1's edited lines
    VIEWED_SPAN_CARRYOVER    — widest viewed span per file (cap 120 lines)
    RG_ISSUE_KEYWORD_SEARCH  — ripgrep on issue-title keywords

If Broad wins only because of EDITED_FILE_WINDOW (still partly biased toward
attempt_1's edit sites), the "Broad is generic retrieval strength" claim is
weak. If RG_* alone carries Broad, generic retrieval really is strong.

Ablation modes (per instance, reusing the same Broad_all candidates):

    Broad_all                       — original bundle
    Broad_only_EDITED_FILE_WINDOW   — keep EDITED_FILE_WINDOW only
    Broad_only_VIEWED_SPAN_CARRYOVER — keep VIEWED_SPAN_CARRYOVER only
    Broad_only_RG                   — keep RG_* only
    Broad_without_EDITED_FILE_WINDOW — drop EDITED_FILE_WINDOW

Metrics per (instance, ablation_mode):
    incremental_line_recall   over G_uncovered  (the fair metric from Step 4b)
    dropped_line_recall       over G_dropped
    unseen_line_recall        over G_unseen
    raw_line_recall           over G  (for sanity, biased)
    num_candidates            (note: packet_chars is Broad_all's actual packet;
                               ablation modes have fewer candidates but no
                               actual packet, so we report efficiency as
                               inc_inter / num_cand instead of per_1k_chars)

Outputs (under --out-dir):
    broad_source_ablation_matrix.csv     one row per (instance, mode)
    broad_source_ablation_summary.md     per-mode aggregates + win-rate
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Optional

from experiments.manifest_builder import get_gold_patch
from experiments.packet_gold_overlap import (
    parse_gold_patch,
    extract_broad,
    _expand_cand_lines,
    _cand_files,
)
from experiments.packet_gold_overlap_incremental import (
    load_run_sets,
    _safe_recall_flagged,
)


# Ablation modes -> predicate over candidate dict
def _is_edited(c: dict) -> bool:
    return c.get("source", "") == "EDITED_FILE_WINDOW"

def _is_viewed(c: dict) -> bool:
    return c.get("source", "") == "VIEWED_SPAN_CARRYOVER"

def _is_rg(c: dict) -> bool:
    return c.get("source", "").startswith("RG_")


ABLATION_MODES: list[tuple[str, callable]] = [
    ("Broad_all",                        lambda c: True),
    ("Broad_only_EDITED_FILE_WINDOW",    _is_edited),
    ("Broad_only_VIEWED_SPAN_CARRYOVER", _is_viewed),
    ("Broad_only_RG",                    _is_rg),
    ("Broad_without_EDITED_FILE_WINDOW", lambda c: not _is_edited(c)),
]


# ===== candidate filtering + metric computation =====

def _compute_for_candidates(cands: list[dict], sets: dict) -> dict:
    """Same shape as compute_incremental but per filtered candidate set."""
    G = sets["G"]
    P = _expand_cand_lines(cands)
    P_files = _cand_files(cands)

    raw_inter = len(P & G)
    inc_inter = len(P & sets["G_uncovered"])
    dropped_inter = len(P & sets["G_dropped"])
    unseen_inter = len(P & sets["G_unseen"])

    raw_recall, _ = _safe_recall_flagged(raw_inter, len(G))
    inc_recall, inc_app = _safe_recall_flagged(inc_inter, len(sets["G_uncovered"]))
    dropped_recall, dropped_app = _safe_recall_flagged(dropped_inter, len(sets["G_dropped"]))
    unseen_recall, unseen_app = _safe_recall_flagged(unseen_inter, len(sets["G_unseen"]))

    eff = round(inc_inter / len(cands), 4) if cands else 0.0

    return {
        "num_candidates": len(cands),
        "cand_files": len(P_files),
        "cand_lines": len(P),
        "raw_line_inter": raw_inter,
        "incremental_line_inter": inc_inter,
        "dropped_line_inter": dropped_inter,
        "unseen_line_inter": unseen_inter,
        "raw_line_recall": raw_recall,
        "incremental_line_recall": inc_recall,
        "incremental_applicable": inc_app,
        "dropped_line_recall": dropped_recall,
        "dropped_applicable": dropped_app,
        "unseen_line_recall": unseen_recall,
        "unseen_applicable": unseen_app,
        "inc_inter_per_cand": eff,
    }


def run_for_dir(run_dir: Path, instance_id: str, sets: dict) -> list[dict]:
    """Produce 5 rows (one per ablation mode) for a single Broad instance."""
    iv = run_dir / "intervention"
    all_cands = extract_broad(iv / "broad_candidates.jsonl")
    rows: list[dict] = []
    for mode, pred in ABLATION_MODES:
        filtered = [c for c in all_cands if pred(c)]
        m = _compute_for_candidates(filtered, sets)
        m["instance_id"] = instance_id
        m["mode"] = mode
        rows.append(m)
    return rows


# ===== matrix build =====

FIELDNAMES = [
    "instance_id", "mode",
    "num_candidates", "cand_files", "cand_lines",
    "raw_line_inter", "incremental_line_inter",
    "dropped_line_inter", "unseen_line_inter",
    "raw_line_recall", "incremental_line_recall", "incremental_applicable",
    "dropped_line_recall", "dropped_applicable",
    "unseen_line_recall", "unseen_applicable",
    "inc_inter_per_cand",
]


def build_matrix(runs_root: Path, out_dir: Path, agent: str = "miniswe") -> int:
    runs_root = Path(runs_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bdir = runs_root / agent / "broad_expansion"
    if not bdir.is_dir():
        print(f"[err] no broad_expansion dir at {bdir}")
        return 1

    rows: list[dict] = []
    for inst_dir in sorted(bdir.iterdir()):
        if not inst_dir.is_dir():
            continue
        iid = inst_dir.name
        # G/V/F/E are baseline-independent; pull from base_miniswe if present
        # (Broad's attempt_1 is identical anyway, but base_miniswe is canonical)
        sets = None
        for bl in ["base_miniswe", "broad_expansion"]:
            cand_dir = runs_root / agent / bl / iid
            if cand_dir.is_dir():
                try:
                    sets = load_run_sets(cand_dir, iid)
                    break
                except Exception:
                    pass
        if sets is None:
            continue
        rows.extend(run_for_dir(inst_dir, iid, sets))

    csv_path = out_dir / "broad_source_ablation_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"[matrix] {len(rows)} rows -> {csv_path}")

    _write_summary(out_dir, rows)
    _write_by_instance(out_dir, rows)
    return len(rows)


# ===== summary MD =====

def _avg(rs: list[dict], key: str) -> float:
    return sum(float(r.get(key) or 0) for r in rs) / len(rs) if rs else 0.0


def _avg_applicable(rs: list[dict], val_key: str, flag_key: str) -> float:
    rs2 = [r for r in rs if r.get(flag_key)]
    if not rs2:
        return 0.0
    return sum(float(r.get(val_key) or 0) for r in rs2) / len(rs2)


def _mode_label(mode: str) -> str:
    return mode.replace("Broad_", "").replace("_", " ")


def _write_summary(out_dir: Path, rows: list[dict]) -> None:
    by_mode: dict[str, list[dict]] = {m: [] for m, _ in ABLATION_MODES}
    for r in rows:
        by_mode.setdefault(r["mode"], []).append(r)

    out: list[str] = []
    out.append("# D4-9 Step 4c — Broad Expansion Source Ablation Summary")
    out.append("")
    out.append("Splits Broad_all's candidates by their `source` field, "
               "recomputes incremental metrics on each subset.")
    out.append("")
    out.append("Set definitions (see `docs/edited_location_bias.md`):")
    out.append("- **G_uncovered = G - (F ∪ E)** — gold not effectively covered by attempt_1")
    out.append("- **G_dropped = G ∩ V - F** — gold seen but not in PATCH_CONTEXT")
    out.append("- **G_unseen = G - V** — gold never opened by agent")
    out.append("")
    out.append("Applicable rows only (denominator > 0). N/A instances excluded from averages.")
    out.append("")
    out.append("## Per-mode aggregates (Line-level)")
    out.append("")
    out.append("| mode | n | avg_cand | avg_files | "
               "n_inc_app | avg_inc_R | n_drp_app | avg_drp_R | "
               "n_uns_app | avg_uns_R | avg_raw_R | inc/cand |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for mode, _ in ABLATION_MODES:
        rs = by_mode.get(mode, [])
        if not rs:
            continue
        n_inc = sum(1 for r in rs if r.get("incremental_applicable"))
        n_drp = sum(1 for r in rs if r.get("dropped_applicable"))
        n_uns = sum(1 for r in rs if r.get("unseen_applicable"))
        out.append(
            f"| {mode} | {len(rs)} | {_avg(rs, 'num_candidates'):.1f} | "
            f"{_avg(rs, 'cand_files'):.1f} | "
            f"{n_inc} | {_avg_applicable(rs, 'incremental_line_recall', 'incremental_applicable'):.3f} | "
            f"{n_drp} | {_avg_applicable(rs, 'dropped_line_recall', 'dropped_applicable'):.3f} | "
            f"{n_uns} | {_avg_applicable(rs, 'unseen_line_recall', 'unseen_applicable'):.3f} | "
            f"{_avg(rs, 'raw_line_recall'):.3f} | "
            f"{_avg(rs, 'inc_inter_per_cand'):.3f} |"
        )

    out.append("")
    out.append("## Win-rate — head-to-head on incremental_line_recall (applicable instances)")
    out.append("")
    by_inst: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_inst.setdefault(r["instance_id"], {})[r["mode"]] = r
    modes = [m for m, _ in ABLATION_MODES]
    wins = {m: 0 for m in modes}
    ties = 0
    n_compared = 0
    for iid, per in by_inst.items():
        if not all(per.get(m, {}).get("incremental_applicable") for m in modes):
            continue
        n_compared += 1
        scores = {m: per[m]["incremental_line_recall"] for m in modes}
        best = max(scores.values())
        winners = [m for m, v in scores.items() if v == best]
        if len(winners) > 1:
            ties += 1
        else:
            wins[winners[0]] += 1
    out.append(f"Compared on {n_compared} instances where incremental metric is applicable for all 5 modes.")
    out.append("")
    out.append("| mode | wins |")
    out.append("|---|---:|")
    for m in modes:
        out.append(f"| {m} | {wins[m]} |")
    out.append(f"| (tie) | {ties} |")

    out.append("")
    out.append("## Pairwise — does EDITED_FILE_WINDOW drive Broad's win?")
    out.append("")
    out.append("If Broad_only_EDITED wins or ties Broad_all on incremental_R, "
               "the win is mostly edit-window bias resurfacing through a different "
               "door. If Broad_only_RG or Broad_without_EDITED wins, generic "
               "retrieval is the actual driver.")
    out.append("")
    out.append("| instance | Broad_all inc_R | only_EDITED | only_VIEWED | only_RG | w/o EDITED | best_mode |")
    out.append("|---|---:|---:|---:|---:|---:|---|")
    for iid in sorted(by_inst.keys()):
        per = by_inst[iid]
        scores: dict[str, float] = {}
        cells: list[str] = []
        for m in modes:
            r = per.get(m)
            if not r or not r.get("incremental_applicable"):
                cells.append("n/a")
                continue
            v = r["incremental_line_recall"]
            scores[m] = v
            cells.append(f"{v:.2f}")
        if scores:
            best = max(scores.values())
            winners = [m for m, v in scores.items() if v == best]
            best_label = "tie" if len(winners) > 1 else winners[0]
        else:
            best_label = "(no app)"
        out.append(f"| {iid} | " + " | ".join(cells) + f" | {best_label} |")

    out.append("")
    out.append("## Interpretation guidance")
    out.append("")
    out.append("- **avg_inc_R** is the headline. If Broad_all ≫ Broad_only_RG, "
               "EDITED_FILE_WINDOW + VIEWED_SPAN_CARRYOVER are doing real work "
               "(they carry attempt_1 locality, which is *expected* to overlap gold).")
    out.append("- **inc/cand** (incremental hits per candidate) measures *efficiency*. "
               "Higher = the source is more targeted. RG_* with high inc/cand + low "
               "avg_cand is the most efficient retriever.")
    out.append("- **Win-rate** over 17 instances is noisy. Use it for direction only.")
    out.append("- **Best mode per instance** in the pairwise table shows *which source* "
               "carried Broad's win on each instance — useful for finding ConDiag's "
               "weak spots (where RG_* alone beats ConDiag's REHYDRATE).")

    out.append("")
    p = out_dir / "broad_source_ablation_summary.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[summary] -> {p}")


# ===== by-instance MD =====

def _write_by_instance(out_dir: Path, rows: list[dict]) -> None:
    by_inst: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_inst.setdefault(r["instance_id"], {})[r["mode"]] = r

    out: list[str] = []
    out.append("# D4-9 Step 4c — Broad Source Ablation by Instance")
    out.append("")
    out.append("'app' = metric applicable (denom > 0). 'n/a' = no gold in that subset.")
    out.append("")
    out.append("| instance | mode | cand | files | lines | raw_R | inc_R | drp_R | uns_R | inc/cand |")
    out.append("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for iid in sorted(by_inst.keys()):
        per = by_inst[iid]
        for mode, _ in ABLATION_MODES:
            r = per.get(mode)
            if not r:
                continue
            inc_r = f"{r['incremental_line_recall']:.2f}" if r.get("incremental_applicable") else "n/a"
            drp_r = f"{r['dropped_line_recall']:.2f}" if r.get("dropped_applicable") else "n/a"
            uns_r = f"{r['unseen_line_recall']:.2f}" if r.get("unseen_applicable") else "n/a"
            out.append(
                f"| {iid} | {mode} | {r['num_candidates']} | {r['cand_files']} | "
                f"{r['cand_lines']} | {r['raw_line_recall']:.2f} | {inc_r} | "
                f"{drp_r} | {uns_r} | {r['inc_inter_per_cand']:.3f} |"
            )
        # gold breakdown (baseline-independent; show once per instance)
        r0 = next(iter(per.values()))
        # need the G_* sizes; we have to re-derive from the original sets — but we
        # don't have them here. Fall back to a single-line note using cand counts only.
        out.append(f"| _{iid}_ | _gold_ | | | | | | | | |")

    out.append("")
    p = out_dir / "broad_source_ablation_by_instance.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[by-inst] -> {p}")


# ===== CLI =====

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--runs-root", default="/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs",
    )
    p.add_argument(
        "--out-dir", default="/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4",
    )
    p.add_argument("--agent", default="miniswe")
    args = p.parse_args(argv)

    n = build_matrix(Path(args.runs_root), Path(args.out_dir), agent=args.agent)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
