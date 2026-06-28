"""D4-9 Step 4b — Incremental packet-vs-gold overlap.

Question: does ConDiag's packet recover gold context that attempt_1
*failed to effectively cover* — i.e. dropped, unseen, or uncovered gold?

This addresses the Edited-Location Bias in raw gold-overlap:
  raw_overlap rewards baselines that expand around attempt_1's edited files
  (Broad's EDITED_FILE_WINDOW, Feedback's edited_files list) because those
  files often trivially overlap gold. ConDiag's REHYDRATE excludes
  PATCH_CONTEXT by design and is unfairly penalized.

Set definitions (lines are (file, line) pairs):
  G = gold lines added/modified in gold patch
  V = lines viewed by agent in attempt_1 (runtime_signals.viewed_spans)
  F = lines in attempt_1 final PATCH_CONTEXT
      (runtime_signals.final_patch_context_files)
  E = lines edited in attempt_1 (runtime_signals.edited_spans_per_file)
  P = lines in intervention packet candidates

Derived subsets of G:
  G_seen     = G ∩ V              (agent opened these files/spans)
  G_final    = G ∩ F              (gold lines inside PATCH_CONTEXT)
  G_edited   = G ∩ E              (gold lines actually edited)
  G_dropped  = G ∩ V - F          (seen but not preserved in PATCH_CONTEXT)
  G_unseen   = G - V              (never opened by agent)
  G_uncovered = G - (F ∪ E)       (gold not effectively covered by attempt_1)

Metrics per (instance, baseline):
  raw_line_recall        = |P ∩ G| / |G|
  incremental_line_recall = |P ∩ G_uncovered| / |G_uncovered|   (skip if denom=0)
  dropped_line_recall    = |P ∩ G_dropped| / |G_dropped|        (skip if denom=0)
  unseen_line_recall     = |P ∩ G_unseen| / |G_unseen|          (skip if denom=0)
  incremental_overlap_per_1k_chars
                         = |P ∩ G_uncovered| / (packet_chars/1000)

Outputs (under --out-dir):
  packet_incremental_overlap_matrix.csv
  packet_incremental_overlap_summary.md
  packet_incremental_overlap_by_instance.md
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Optional

from experiments.manifest_builder import get_gold_patch
from experiments.packet_gold_overlap import (
    parse_gold_patch,
    extract_broad,
    extract_condiag,
    extract_feedback,
    _expand_cand_lines,
    _cand_files,
    _packet_chars,
    _f1,
)


BASELINES = ["feedback_retry", "broad_expansion", "condiag_packet_only"]


# ===== span parsing helpers =====

def _line_set_from_span_pairs(pairs: list[list[int]]) -> set[int]:
    """[[s, e], ...] -> set of all integer lines in those inclusive ranges."""
    out: set[int] = set()
    for p in pairs:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            continue
        s, e = int(p[0]), int(p[1])
        if s > 0 and e >= s:
            out.update(range(s, e + 1))
    return out


def _line_set_from_entries(entries: list[dict]) -> dict[str, set[int]]:
    """[{'file': f, 'lines': 'X-Y'}, ...] -> {file: set(lines)}."""
    out: dict[str, set[int]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        f = e.get("file") or ""
        if not f:
            continue
        ln = e.get("lines") or ""
        s, e2 = 0, 0
        if isinstance(ln, str) and "-" in ln:
            try:
                parts = ln.split("-")
                s, e2 = int(parts[0]), int(parts[1])
            except (ValueError, IndexError):
                continue
        elif isinstance(ln, (list, tuple)) and len(ln) >= 2:
            s, e2 = int(ln[0]), int(ln[1])
        elif isinstance(ln, int):
            s = e2 = int(ln)
        else:
            continue
        if s > 0 and e2 >= s:
            out.setdefault(f, set()).update(range(s, e2 + 1))
    return out


def _span_dict_to_lines(d: dict[str, list]) -> dict[str, set[int]]:
    """{file: [[s,e], ...]} -> {file: set(lines)}."""
    out: dict[str, set[int]] = {}
    for f, pairs in (d or {}).items():
        ls = _line_set_from_span_pairs(pairs)
        if ls:
            out[f] = ls
    return out


def _to_line_pairs_set(d: dict[str, set[int]]) -> set[tuple[str, int]]:
    return {(f, l) for f, ls in d.items() for l in ls}


# ===== run-level set loader =====

def load_run_sets(run_dir: Path, instance_id: str) -> dict:
    """Load G/V/F/E sets for a run (drawn from attempt_1, identical across baselines).

    Returns dict with keys:
      G, V, F, E                as set[(file, line)]
      G_seen, G_final, G_edited, G_dropped, G_unseen, G_uncovered   subsets of G
    """
    rs_path = run_dir / "attempt_1" / "runtime_signals.json"
    rs = {}
    if rs_path.is_file():
        try:
            rs = json.loads(rs_path.read_text(encoding="utf-8"))
        except Exception:
            rs = {}

    G = _to_line_pairs_set(parse_gold_patch(get_gold_patch(instance_id)))
    V = _to_line_pairs_set(_span_dict_to_lines(rs.get("viewed_spans") or {}))
    F = _to_line_pairs_set(_line_set_from_entries(rs.get("final_patch_context_files") or []))
    E = _to_line_pairs_set(_span_dict_to_lines(rs.get("edited_spans_per_file") or {}))

    G_seen = G & V
    G_final = G & F
    G_edited = G & E
    G_dropped = G_seen - F
    G_unseen = G - V
    G_uncovered = G - (F | E)

    return {
        "G": G, "V": V, "F": F, "E": E,
        "G_seen": G_seen, "G_final": G_final, "G_edited": G_edited,
        "G_dropped": G_dropped, "G_unseen": G_unseen, "G_uncovered": G_uncovered,
    }


# ===== per-baseline candidate loader (also pulls trigger/5R for grouping) =====

def load_meta(run_dir: Path, baseline: str) -> dict:
    """Pull trigger_type / packet_mode / primary_5r_action from intervention_report."""
    iv = run_dir / "intervention" / "intervention_report.json"
    out = {"trigger_type": "", "packet_mode": "", "primary_5r_action": ""}
    if not iv.is_file():
        return out
    try:
        d = json.loads(iv.read_text(encoding="utf-8"))
    except Exception:
        return out
    out["trigger_type"] = d.get("trigger_type", "") or ""
    out["packet_mode"] = d.get("packet_mode", "") or ""
    # ConDiag recovery_report carries the 5R action
    rr = run_dir / "intervention" / "recovery_report.json"
    if rr.is_file():
        try:
            rd = json.loads(rr.read_text(encoding="utf-8"))
            diag = rd.get("diagnosis") or {}
            out["primary_5r_action"] = diag.get("primary_5r_action", "") or ""
        except Exception:
            pass
    return out


def load_candidates(run_dir: Path, baseline: str) -> list[dict]:
    iv = run_dir / "intervention"
    if baseline == "feedback_retry":
        return extract_feedback(iv / "context_packet.md")
    if baseline == "broad_expansion":
        return extract_broad(iv / "broad_candidates.jsonl")
    if baseline == "condiag_packet_only":
        return extract_condiag(iv / "selected_evidence.json")
    return []


# ===== metric computation =====

def _safe_recall(inter: int, denom: int) -> float:
    return round(inter / denom, 4) if denom > 0 else 0.0


def _safe_recall_flagged(inter: int, denom: int) -> tuple[float, bool]:
    """Returns (recall, applicable). When denom=0, metric is N/A — not 0."""
    if denom == 0:
        return 0.0, False
    return round(inter / denom, 4), True


def compute_incremental(cands: list[dict], sets: dict, packet_chars: int) -> dict:
    G = sets["G"]
    P = _expand_cand_lines(cands)
    P_files = _cand_files(cands)

    raw_inter = len(P & G)
    inc_inter = len(P & sets["G_uncovered"])
    dropped_inter = len(P & sets["G_dropped"])
    unseen_inter = len(P & sets["G_unseen"])

    raw_recall, _ = _safe_recall_flagged(raw_inter, len(G))
    inc_recall, inc_applicable = _safe_recall_flagged(inc_inter, len(sets["G_uncovered"]))
    dropped_recall, dropped_applicable = _safe_recall_flagged(dropped_inter, len(sets["G_dropped"]))
    unseen_recall, unseen_applicable = _safe_recall_flagged(unseen_inter, len(sets["G_unseen"]))

    inc_per_1k = (
        round(inc_inter / (packet_chars / 1000.0), 4) if packet_chars > 0 else 0.0
    )

    return {
        "gold_total_lines": len(G),
        "gold_seen_lines": len(sets["G_seen"]),
        "gold_final_lines": len(sets["G_final"]),
        "gold_edited_lines": len(sets["G_edited"]),
        "gold_dropped_lines": len(sets["G_dropped"]),
        "gold_unseen_lines": len(sets["G_unseen"]),
        "gold_uncovered_lines": len(sets["G_uncovered"]),
        "raw_line_inter": raw_inter,
        "incremental_line_inter": inc_inter,
        "dropped_line_inter": dropped_inter,
        "unseen_line_inter": unseen_inter,
        "raw_line_recall": raw_recall,
        "incremental_line_recall": inc_recall,
        "incremental_applicable": inc_applicable,
        "dropped_line_recall": dropped_recall,
        "dropped_applicable": dropped_applicable,
        "unseen_line_recall": unseen_recall,
        "unseen_applicable": unseen_applicable,
        "incremental_overlap_per_1k_chars": inc_per_1k,
    }


# ===== row builder =====

FIELDNAMES = [
    "instance_id", "baseline", "trigger_type", "packet_mode", "primary_5r_action",
    "num_candidates", "packet_chars",
    "gold_total_lines", "gold_seen_lines", "gold_final_lines", "gold_edited_lines",
    "gold_dropped_lines", "gold_unseen_lines", "gold_uncovered_lines",
    "raw_line_inter", "incremental_line_inter",
    "dropped_line_inter", "unseen_line_inter",
    "raw_line_recall", "incremental_line_recall", "incremental_applicable",
    "dropped_line_recall", "dropped_applicable",
    "unseen_line_recall", "unseen_applicable",
    "incremental_overlap_per_1k_chars",
]


def run_for_dir(run_dir: Path, baseline: str, instance_id: str,
                cached_sets: Optional[dict] = None) -> dict:
    sets = cached_sets or load_run_sets(run_dir, instance_id)
    cands = load_candidates(run_dir, baseline)
    meta = load_meta(run_dir, baseline)
    pkt_chars = _packet_chars(run_dir, baseline)

    row = {
        "instance_id": instance_id,
        "baseline": baseline,
        **meta,
        "num_candidates": len(cands),
        "packet_chars": pkt_chars,
    }
    row.update(compute_incremental(cands, sets, pkt_chars))
    return row


# ===== matrix build =====

def build_matrix(runs_root: Path, out_dir: Path,
                 baselines: Optional[list[str]] = None,
                 agent: str = "miniswe") -> int:
    baselines = baselines or BASELINES
    runs_root = Path(runs_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    # G/V/F/E are baseline-independent; load once per instance.
    inst_dirs = sorted(
        [d for d in (runs_root / agent / baselines[0]).iterdir() if d.is_dir()]
    ) if (runs_root / agent / baselines[0]).is_dir() else []

    for inst_dir in inst_dirs:
        iid = inst_dir.name
        # Use base_miniswe run for sets (any baseline's attempt_1 is identical)
        # fall back to first baseline that exists
        sets = None
        for bl in ["base_miniswe", *baselines]:
            cand_dir = runs_root / agent / bl / iid
            if cand_dir.is_dir():
                try:
                    sets = load_run_sets(cand_dir, iid)
                    break
                except Exception:
                    pass
        if sets is None:
            continue
        for bl in baselines:
            rdir = runs_root / agent / bl / iid
            if not rdir.is_dir():
                continue
            rows.append(run_for_dir(rdir, bl, iid, cached_sets=sets))

    csv_path = out_dir / "packet_incremental_overlap_matrix.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"[matrix] {len(rows)} rows -> {csv_path}")

    _write_summary(out_dir, rows, baselines)
    _write_by_instance(out_dir, rows, baselines)
    return len(rows)


# ===== summary MD =====

def _avg(rs: list[dict], key: str) -> float:
    return sum(float(r.get(key) or 0) for r in rs) / len(rs) if rs else 0.0


def _avg_applicable(rs: list[dict], val_key: str, flag_key: str) -> float:
    """Average over rows where the metric is applicable (flag=True)."""
    rs2 = [r for r in rs if r.get(flag_key)]
    if not rs2:
        return 0.0
    return sum(float(r.get(val_key) or 0) for r in rs2) / len(rs2)


def _write_summary(out_dir: Path, rows: list[dict], baselines: list[str]) -> None:
    by_bl: dict[str, list[dict]] = {bl: [] for bl in baselines}
    for r in rows:
        by_bl.setdefault(r["baseline"], []).append(r)

    out: list[str] = []
    out.append("# D4-9 Step 4b — Incremental Packet-vs-Gold Overlap Summary")
    out.append("")
    out.append("Addresses Edited-Location Bias (see docs/edited_location_bias.md).")
    out.append("")
    out.append("Set definitions (line-level):")
    out.append("- **G** = lines added/modified in gold patch")
    out.append("- **V** = lines agent viewed in attempt_1 (runtime_signals.viewed_spans)")
    out.append("- **F** = lines in attempt_1 final PATCH_CONTEXT")
    out.append("- **E** = lines agent edited in attempt_1 (runtime_signals.edited_spans_per_file)")
    out.append("- **G_uncovered = G - (F ∪ E)** = gold NOT effectively covered by attempt_1")
    out.append("- **G_dropped = G ∩ V - F** = gold seen but not preserved in PATCH_CONTEXT")
    out.append("- **G_unseen = G - V** = gold never opened by agent")
    out.append("")
    out.append("Applicable rows only (denominator > 0). N/A instances excluded from averages.")
    out.append("")

    # Headline: incremental recall (the metric that should be fair)
    out.append("## Headline — incremental_line_recall (the fair metric)")
    out.append("")
    out.append("| baseline | n_rows | n_applicable | avg_incremental_R | "
               "avg_dropped_R | avg_unseen_R | avg_inc/1k_chars |")
    out.append("|---|---:|---:|---:|---:|---:|---:|")
    for bl in baselines:
        rs = by_bl.get(bl, [])
        if not rs:
            continue
        n_app = sum(1 for r in rs if r.get("incremental_applicable"))
        out.append(
            f"| {bl} | {len(rs)} | {n_app} | "
            f"{_avg_applicable(rs, 'incremental_line_recall', 'incremental_applicable'):.3f} | "
            f"{_avg_applicable(rs, 'dropped_line_recall', 'dropped_applicable'):.3f} | "
            f"{_avg_applicable(rs, 'unseen_line_recall', 'unseen_applicable'):.3f} | "
            f"{_avg(rs, 'incremental_overlap_per_1k_chars'):.2f} |"
        )

    out.append("")
    out.append("## Sanity — raw_line_recall (biased; for comparison with Step 4a)")
    out.append("")
    out.append("| baseline | n | avg_raw_R | avg_raw_inter |")
    out.append("|---|---:|---:|---:|")
    for bl in baselines:
        rs = by_bl.get(bl, [])
        if not rs:
            continue
        out.append(
            f"| {bl} | {len(rs)} | {_avg(rs, 'raw_line_recall'):.3f} | "
            f"{_avg(rs, 'raw_line_inter'):.2f} |"
        )

    out.append("")
    out.append("## Win rate — head-to-head on incremental_line_recall (applicable instances only)")
    out.append("")
    by_inst: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_inst.setdefault(r["instance_id"], {})[r["baseline"]] = r
    wins = {bl: 0 for bl in baselines}
    ties = 0
    n_compared = 0
    for iid, per in by_inst.items():
        # only compare on instances where ALL baselines are applicable
        if not all(per.get(bl, {}).get("incremental_applicable") for bl in baselines):
            continue
        n_compared += 1
        scores = {bl: per[bl]["incremental_line_recall"] for bl in baselines}
        best = max(scores.values())
        winners = [bl for bl, v in scores.items() if v == best]
        if len(winners) > 1:
            ties += 1
        else:
            wins[winners[0]] += 1
    out.append(f"Compared on {n_compared} instances where incremental metric is applicable for all baselines.")
    out.append("")
    out.append("| baseline | wins |")
    out.append("|---|---:|")
    for bl in baselines:
        out.append(f"| {bl} | {wins[bl]} |")
    out.append(f"| (tie) | {ties} |")

    out.append("")
    out.append("## Gold subset sizes (avg across 17 instances, baseline-independent)")
    out.append("")
    out.append("| metric | avg |")
    out.append("|---|---:|")
    rs = rows[: len(rows) // len(baselines)] if rows else []
    if rs:
        # use just the first baseline's rows since sets are shared
        first_bl_rows = by_bl.get(baselines[0], [])
        out.append(f"| gold_total_lines | {_avg(first_bl_rows, 'gold_total_lines'):.1f} |")
        out.append(f"| gold_final_lines (in PATCH_CONTEXT) | {_avg(first_bl_rows, 'gold_final_lines'):.1f} |")
        out.append(f"| gold_edited_lines | {_avg(first_bl_rows, 'gold_edited_lines'):.1f} |")
        out.append(f"| gold_dropped_lines (seen but dropped) | {_avg(first_bl_rows, 'gold_dropped_lines'):.1f} |")
        out.append(f"| gold_unseen_lines (never viewed) | {_avg(first_bl_rows, 'gold_unseen_lines'):.1f} |")
        out.append(f"| gold_uncovered_lines (G - F∪E) | {_avg(first_bl_rows, 'gold_uncovered_lines'):.1f} |")
        n_inc = sum(1 for r in first_bl_rows if r.get("incremental_applicable"))
        out.append(f"| instances where incremental applicable | {n_inc}/{len(first_bl_rows)} |")

    out.append("")
    summary_path = out_dir / "packet_incremental_overlap_summary.md"
    summary_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[summary] -> {summary_path}")


# ===== by-instance MD =====

def _write_by_instance(out_dir: Path, rows: list[dict], baselines: list[str]) -> None:
    by_inst: dict[str, dict[str, dict]] = {}
    for r in rows:
        by_inst.setdefault(r["instance_id"], {})[r["baseline"]] = r

    out: list[str] = []
    out.append("# D4-9 Step 4b — Incremental Overlap by Instance")
    out.append("")
    out.append("Per-instance side-by-side. 'app' = metric applicable (denom > 0).")
    out.append("")
    out.append("| instance | baseline | trig | pkt_mode | 5R | cand | pkt_chars | "
               "raw_R | inc_R | drp_R | uns_R | inc/1k |")
    out.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for iid in sorted(by_inst.keys()):
        per = by_inst[iid]
        for bl in baselines:
            r = per.get(bl)
            if not r:
                continue
            inc_r = f"{r['incremental_line_recall']:.2f}" if r.get("incremental_applicable") else "n/a"
            drp_r = f"{r['dropped_line_recall']:.2f}" if r.get("dropped_applicable") else "n/a"
            uns_r = f"{r['unseen_line_recall']:.2f}" if r.get("unseen_applicable") else "n/a"
            out.append(
                f"| {iid} | {bl} | {r.get('trigger_type', '')[:8]} | "
                f"{r.get('packet_mode', '')[:20]} | {r.get('primary_5r_action', '')[:10]} | "
                f"{r['num_candidates']} | {r['packet_chars']} | "
                f"{r['raw_line_recall']:.2f} | {inc_r} | {drp_r} | {uns_r} | "
                f"{r['incremental_overlap_per_1k_chars']:.2f} |"
            )
        # gold breakdown (baseline-independent; show once per instance)
        rs0 = next(iter(per.values()))
        out.append(
            f"| _{iid}_ | _gold_ | | | | | | "
            f"total={rs0['gold_total_lines']} drop={rs0['gold_dropped_lines']} "
            f"unseen={rs0['gold_unseen_lines']} uncov={rs0['gold_uncovered_lines']} | | | | | |"
        )

    out.append("")
    p = out_dir / "packet_incremental_overlap_by_instance.md"
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
    p.add_argument("--baselines", default=",".join(BASELINES))
    args = p.parse_args(argv)

    baselines = [b.strip() for b in args.baselines.split(",") if b.strip()]
    n = build_matrix(Path(args.runs_root), Path(args.out_dir),
                     baselines=baselines, agent=args.agent)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
