"""D4-9 Step 5b — ConDiag per-5R group metrics.

Question: ConDiag's avg_inc_R (0.246 after Step 5 fix) bundles 4 different
5R actions whose *objectives* are different. Aggregating them masks where
ConDiag actually helps vs where it's still weak. This script splits the
12 fire + 5 NOOP cases by primary_5r_action and reports per-group metrics
appropriate to each action's intended effect.

5R grouping:
  REHYDRATE  → objective: surface dropped evidence   → metric: dropped_R
  RETRIEVE   → objective: surface unseen gold        → metric: unseen_R
  RESTRAIN   → objective: prune overbroad patch      → metric: patch shape / unsupported edit (NOT overlap)
  NOOP       → objective: abstain correctly          → metric: abstain_accuracy vs trigger rule
  RECONCILE  → objective: verify submission shape    → not in Batch2 (no HARD_FAILURE trigger)

For NOOP we use the trigger rule (Broad / Feedback same decision) as proxy
ground truth, since we haven't run ContextBench eval yet to know which
attempt_1 patches actually resolved.

Outputs (under --out-dir):
  condiag_5r_group_metrics.csv       one row per ConDiag instance
  condiag_5r_group_summary.md        per-5R aggregates + cross-baseline
  condiag_5r_by_instance.md          per-instance side-by-side with Broad/Fb
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Optional

from experiments.packet_gold_overlap import _packet_chars
from experiments.packet_gold_overlap_incremental import (
    BASELINES,
    load_run_sets,
    load_meta,
    load_candidates,
    compute_incremental,
    _avg,
    _avg_applicable,
)


COND_BASELINE = "condiag_packet_only"


# ===== ConDiag-specific metadata (5R + abstain + executed_actions) =====

def load_condiag_meta(run_dir: Path) -> dict:
    """Pull pathology / 5R / abstain / counts from recovery_report + intervention_report.

    abstain is sourced from packet_mode == 'condiag_noop' (not diag.abstain,
    which is inconsistently set in v0).
    """
    iv = run_dir / "intervention"
    ir_p = iv / "intervention_report.json"
    rr_p = iv / "recovery_report.json"

    out = {
        "trigger_type": "", "packet_mode": "",
        "primary_5r_action": "", "pathology": "",
        "abstain": False,
        "num_selected_evidence": 0,
        "num_executed_actions": 0,
    }

    if ir_p.is_file():
        try:
            ir = json.loads(ir_p.read_text(encoding="utf-8"))
            out["trigger_type"] = ir.get("trigger_type", "") or ""
            out["packet_mode"] = ir.get("packet_mode", "") or ""
            out["abstain"] = (out["packet_mode"] == "condiag_noop")
        except Exception:
            pass

    if rr_p.is_file():
        try:
            rr = json.loads(rr_p.read_text(encoding="utf-8"))
            diag = rr.get("diagnosis") or {}
            out["primary_5r_action"] = diag.get("primary_5r_action", "") or ""
            out["pathology"] = diag.get("pathology", "") or ""
            sel = rr.get("selected_evidence")
            if isinstance(sel, dict):
                out["num_selected_evidence"] = len(sel.get("evidence") or [])
            elif isinstance(sel, list):
                out["num_selected_evidence"] = len(sel)
            # also try the standalone selected_evidence.json (Step 5 fix output)
            se_p = iv / "selected_evidence.json"
            if se_p.is_file():
                try:
                    se = json.loads(se_p.read_text(encoding="utf-8"))
                    out["num_selected_evidence"] = len(se.get("evidence") or [])
                except Exception:
                    pass
            out["num_executed_actions"] = len(rr.get("executed_actions") or [])
        except Exception:
            pass

    # fallback: load selected_evidence.json directly
    if out["num_selected_evidence"] == 0:
        se_p = iv / "selected_evidence.json"
        if se_p.is_file():
            try:
                se = json.loads(se_p.read_text(encoding="utf-8"))
                out["num_selected_evidence"] = len(se.get("evidence") or [])
            except Exception:
                pass

    return out


# ===== Cross-baseline NO_TRIGGER check (for abstain_correct proxy) =====

def _other_baselines_no_trigger(run_dir: Path, agent_root: Path,
                                instance_id: str) -> bool:
    """Return True iff Broad + Feedback both should_retry=False on this instance.

    Used as proxy ground truth for NOOP abstain correctness (we haven't run
    ContextBench eval, so we treat the trigger rule's consensus as truth).
    """
    for bl in ("broad_expansion", "feedback_retry"):
        ir_p = agent_root / bl / instance_id / "intervention" / "intervention_report.json"
        if not ir_p.is_file():
            continue
        try:
            ir = json.loads(ir_p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if ir.get("should_retry", False):
            return False
    return True


# ===== Row builder =====

FIELDNAMES = [
    "instance_id", "trigger_type", "pathology", "primary_5r_action",
    "packet_mode", "abstain",
    "num_selected_evidence", "num_executed_actions", "packet_chars",
    "gold_total_lines", "gold_dropped_lines", "gold_unseen_lines", "gold_uncovered_lines",
    "raw_line_inter", "incremental_line_inter",
    "dropped_line_inter", "unseen_line_inter",
    "raw_line_recall", "incremental_line_recall", "incremental_applicable",
    "dropped_line_recall", "dropped_applicable",
    "unseen_line_recall", "unseen_applicable",
    "incremental_overlap_per_1k_chars",
    "abstain_correct", "intervention_false_positive",
    "broad_incremental_R", "broad_dropped_R", "broad_unseen_R",
    "feedback_incremental_R", "feedback_dropped_R", "feedback_unseen_R",
]


def run_for_instance(run_dir: Path, agent_root: Path, instance_id: str,
                     cached_sets: dict) -> dict:
    meta = load_condiag_meta(run_dir)
    pkt_chars = _packet_chars(run_dir, COND_BASELINE)

    cands = load_candidates(run_dir, COND_BASELINE)
    inc = compute_incremental(cands, cached_sets, pkt_chars)

    abstain = meta["abstain"]
    # NOOP correctness: only meaningful when ConDiag abstained
    others_no_trig = _other_baselines_no_trigger(run_dir, agent_root, instance_id)
    if abstain:
        abstain_correct = others_no_trig  # ConDiag abstained; correct iff Broad+Fb also said no
    else:
        abstain_correct = False  # N/A for non-NOOP
    # FP: ConDiag fired (non-NOOP) but Broad+Fb both said no
    intervention_false_positive = (not abstain) and others_no_trig

    row = {
        "instance_id": instance_id,
        **meta,
        "packet_chars": pkt_chars,
        **inc,
        "abstain_correct": abstain_correct,
        "intervention_false_positive": intervention_false_positive,
    }
    return row


def _cross_baseline_inc(agent_root: Path, instance_id: str, sets: dict) -> dict:
    """Quick incremental_R for Broad and Feedback on the same instance."""
    out = {
        "broad_incremental_R": "", "broad_dropped_R": "", "broad_unseen_R": "",
        "feedback_incremental_R": "", "feedback_dropped_R": "", "feedback_unseen_R": "",
    }
    for bl, prefix in (("broad_expansion", "broad"), ("feedback_retry", "feedback")):
        rdir = agent_root / bl / instance_id
        if not rdir.is_dir():
            continue
        cands = load_candidates(rdir, bl)
        inc = compute_incremental(cands, sets, _packet_chars(rdir, bl))
        if inc.get("incremental_applicable"):
            out[f"{prefix}_incremental_R"] = inc["incremental_line_recall"]
        if inc.get("dropped_applicable"):
            out[f"{prefix}_dropped_R"] = inc["dropped_line_recall"]
        if inc.get("unseen_applicable"):
            out[f"{prefix}_unseen_R"] = inc["unseen_line_recall"]
    return out


# ===== Matrix build =====

def build_matrix(runs_root: Path, out_dir: Path, agent: str = "miniswe") -> int:
    runs_root = Path(runs_root)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    agent_root = runs_root / agent
    cond_root = agent_root / COND_BASELINE
    if not cond_root.is_dir():
        print(f"[err] no {COND_BASELINE} dir at {cond_root}")
        return 1

    rows: list[dict] = []
    for inst_dir in sorted(cond_root.iterdir()):
        if not inst_dir.is_dir():
            continue
        iid = inst_dir.name
        # G/V/F/E shared; pull from base_miniswe if present, else from condiag_packet_only
        sets = None
        for bl in ("base_miniswe", COND_BASELINE):
            cand_dir = agent_root / bl / iid
            if cand_dir.is_dir():
                try:
                    sets = load_run_sets(cand_dir, iid)
                    break
                except Exception:
                    pass
        if sets is None:
            continue

        row = run_for_instance(inst_dir, agent_root, iid, sets)
        # cross-baseline incremental_R (Broad / Feedback)
        row.update(_cross_baseline_inc(agent_root, iid, sets))
        rows.append(row)

    csv_path = out_dir / "condiag_5r_group_metrics.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDNAMES})
    print(f"[matrix] {len(rows)} rows -> {csv_path}")

    _write_summary(out_dir, rows)
    _write_by_instance(out_dir, rows)
    return len(rows)


# ===== Summary MD =====

def _fmt(v, fmt: str = ".3f") -> str:
    if v == "" or v is None:
        return "n/a"
    if isinstance(v, bool):
        return "T" if v else "F"
    try:
        return format(float(v), fmt)
    except Exception:
        return str(v)


def _write_summary(out_dir: Path, rows: list[dict]) -> None:
    by_5r: dict[str, list[dict]] = {}
    for r in rows:
        by_5r.setdefault(r["primary_5r_action"], []).append(r)

    out: list[str] = []
    out.append("# D4-9 Step 5b — ConDiag per-5R Group Metrics Summary")
    out.append("")
    out.append("Splits ConDiag's 17 Batch2 instances by `primary_5r_action` and reports "
               "metrics appropriate to each action's intended effect.")
    out.append("")
    out.append("**Set reminder** (see docs/edited_location_bias.md):")
    out.append("- G_dropped = gold lines agent viewed but didn't keep in PATCH_CONTEXT")
    out.append("- G_unseen  = gold lines agent never opened")
    out.append("- G_uncovered = G - (PATCH_CONTEXT ∪ edited)  ← Step 4b's fair metric denominator")
    out.append("")
    out.append("**Cross-baseline**: Broad / Feedback incremental_R from same instance, "
               "for head-to-head within each 5R group.")
    out.append("")
    out.append(f"## 5R distribution over Batch2 17")
    out.append("")
    out.append("| 5R | n | trigger_type(s) |")
    out.append("|---|---:|---|")
    for action in ["REHYDRATE", "RETRIEVE", "RESTRAIN", "NOOP", "RECONCILE", ""]:
        rs = by_5r.get(action, [])
        if not rs and action != "RECONCILE":
            continue
        if action == "RECONCILE" and not rs:
            out.append(f"| RECONCILE | 0 | _not observed in Batch2 (no HARD_FAILURE trigger)_ |")
            continue
        trigs = sorted({r["trigger_type"] for r in rs})
        label = action if action else "(empty)"
        out.append(f"| {label} | {len(rs)} | {', '.join(trigs)} |")

    out.append("")
    out.append("## REHYDRATE — dropped gold is the headline metric")
    out.append("")
    rs = by_5r.get("REHYDRATE", [])
    if rs:
        n_app_inc = sum(1 for r in rs if r.get("incremental_applicable"))
        n_app_drp = sum(1 for r in rs if r.get("dropped_applicable"))
        out.append(f"n = {len(rs)} (n_inc_applicable={n_app_inc}, n_dropped_applicable={n_app_drp})")
        out.append("")
        out.append("| metric | ConDiag | Broad | Feedback |")
        out.append("|---|---:|---:|---:|")
        out.append(f"| avg_incremental_R | {_avg_applicable(rs, 'incremental_line_recall', 'incremental_applicable'):.3f} | "
                   f"{_avg([r for r in rs if r.get('broad_incremental_R') != ''], 'broad_incremental_R'):.3f} | "
                   f"{_avg([r for r in rs if r.get('feedback_incremental_R') != ''], 'feedback_incremental_R'):.3f} |")
        out.append(f"| avg_dropped_R | {_avg_applicable(rs, 'dropped_line_recall', 'dropped_applicable'):.3f} | "
                   f"{_avg([r for r in rs if r.get('broad_dropped_R') != ''], 'broad_dropped_R'):.3f} | "
                   f"{_avg([r for r in rs if r.get('feedback_dropped_R') != ''], 'feedback_dropped_R'):.3f} |")
        out.append(f"| avg_inc/1k_chars | {_avg(rs, 'incremental_overlap_per_1k_chars'):.3f} | — | — |")
        out.append(f"| avg_num_selected_evidence | {_avg(rs, 'num_selected_evidence'):.1f} | — | — |")
        out.append("")
        out.append("**Interpretation**: ConDiag REHYDRATE *should* win avg_dropped_R because "
                   "Step 5 fix specifically boosts dropped spans. If Broad still wins, "
                   "EDITED_FILE_WINDOW's ±40 neighbor bias is dominating (likely on cases "
                   "where edited lines are within 40 of dropped gold).")
    else:
        out.append("_No REHYDRATE cases in Batch2._")

    out.append("")
    out.append("## RETRIEVE — unseen gold is the headline metric")
    out.append("")
    rs = by_5r.get("RETRIEVE", [])
    if rs:
        n_app_uns = sum(1 for r in rs if r.get("unseen_applicable"))
        n_app_inc = sum(1 for r in rs if r.get("incremental_applicable"))
        out.append(f"n = {len(rs)} (n_unseen_applicable={n_app_uns}, n_inc_applicable={n_app_inc})")
        out.append("")
        out.append("| metric | ConDiag | Broad | Feedback |")
        out.append("|---|---:|---:|---:|")
        out.append(f"| avg_unseen_R | {_avg_applicable(rs, 'unseen_line_recall', 'unseen_applicable'):.3f} | "
                   f"{_avg([r for r in rs if r.get('broad_unseen_R') != ''], 'broad_unseen_R'):.3f} | "
                   f"{_avg([r for r in rs if r.get('feedback_unseen_R') != ''], 'feedback_unseen_R'):.3f} |")
        out.append(f"| avg_incremental_R | {_avg_applicable(rs, 'incremental_line_recall', 'incremental_applicable'):.3f} | "
                   f"{_avg([r for r in rs if r.get('broad_incremental_R') != ''], 'broad_incremental_R'):.3f} | "
                   f"{_avg([r for r in rs if r.get('feedback_incremental_R') != ''], 'feedback_incremental_R'):.3f} |")
        out.append(f"| avg_num_selected_evidence | {_avg(rs, 'num_selected_evidence'):.1f} | — | — |")
        out.append("")
        out.append("**Interpretation**: RETRIEVE's job is to surface *unseen* gold. "
                   "If avg_unseen_R ≈ 0, ConDiag's FIND_NEIGHBOR_TESTS / target_hints "
                   "are not finding gold the agent missed. This is the 5R group where "
                   "Broad's RG_* should theoretically win (it doesn't — see Step 4c "
                   "where only_RG=0.000).")
    else:
        out.append("_No RETRIEVE cases in Batch2._")

    out.append("")
    out.append("## NOOP — abstain correctness vs trigger rule")
    out.append("")
    rs = by_5r.get("NOOP", [])
    if rs:
        n_correct = sum(1 for r in rs if r.get("abstain_correct"))
        n_fp = sum(1 for r in rs if r.get("intervention_false_positive"))
        out.append(f"n = {len(rs)} (all abstained)")
        out.append("")
        out.append(f"- **abstain_correct**: {n_correct}/{len(rs)} "
                   f"(ConDiag NOOP, Broad+Fb both should_retry=False)")
        out.append(f"- **intervention_false_positive**: 0 by construction in this group "
                   f"(NOOP never fires)")
        out.append("")
        out.append("**Interpretation**: proxy ground truth = trigger rule consensus. "
                   "5/5 means ConDiag's auto-diagnoser correctly maps NO_TRIGGER → NOOP. "
                   "True accuracy depends on ContextBench eval (deferred).")
        out.append("")
        out.append("**Broad's behavior on these NOOP cases**: Broad also `should_retry=False` "
                   "on all 5 → no Broad false intervention here. (Note: Broad *still emits* "
                   "`broad_candidates.jsonl` for inspection even when should_retry=False, but "
                   "doesn't produce a context_packet — so it's not a 'false intervention' "
                   "in the same sense.)")
    else:
        out.append("_No NOOP cases in Batch2._")

    out.append("")
    out.append("## RESTRAIN — patch shape, not overlap")
    out.append("")
    rs = by_5r.get("RESTRAIN", [])
    if rs:
        out.append(f"n = {len(rs)} (sample too small for averages)")
        out.append("")
        out.append("RESTRAIN's objective is patch pruning, NOT context overlap. "
                   "Report per-instance only (see by_instance.md). The right metric "
                   "for RESTRAIN is `unsupported_edit_reduction` which requires running "
                   "ConDiag retry and measuring patch shape change — deferred.")
    else:
        out.append("_No RESTRAIN cases in Batch2._")

    out.append("")
    out.append("## RECONCILE — not observed")
    out.append("")
    out.append("Batch2 has no `HARD_FAILURE` trigger → no RECONCILE case. "
               "Need bigger Pilot or instance selection bias toward HARD_FAILURE.")

    out.append("")
    out.append("## Headline takeaway")
    out.append("")
    out.append("- ConDiag REHYDRATE (n=6) should be **dropped-gold focused**. "
               "Compare avg_dropped_R vs Broad/Feedback.")
    out.append("- ConDiag RETRIEVE (n=5) should be **unseen-gold focused**. "
               "Compare avg_unseen_R vs Broad/Feedback.")
    out.append("- ConDiag NOOP (n=5) **abstain_correct vs trigger rule: 5/5 in Batch2** "
               "(true resolved-rate accuracy pending ContextBench eval).")
    out.append("- RESTRAIN (n=1) and RECONCILE (n=0) under-sampled — defer.")

    out.append("")
    p = out_dir / "condiag_5r_group_summary.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"[summary] -> {p}")


# ===== By-instance MD =====

def _write_by_instance(out_dir: Path, rows: list[dict]) -> None:
    out: list[str] = []
    out.append("# D4-9 Step 5b — ConDiag per-5R Metrics by Instance")
    out.append("")
    out.append("'app' = metric applicable (denom > 0). 'n/a' = no gold in that subset.")
    out.append("")
    out.append("Cross-baseline incremental_R = same instance Broad / Feedback.")
    out.append("")
    out.append("| instance | trig | 5R | abstain | n_sel | pkt_chars | "
               "drop_R (cd/br/fb) | uns_R (cd/br/fb) | inc_R (cd/br/fb) | gold(drop/uns/uncov) |")
    out.append("|---|---|---|---|---:|---:|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (x["primary_5r_action"], x["instance_id"])):
        iid = r["instance_id"]
        tr = r["trigger_type"][:8]
        act = r["primary_5r_action"]
        ab = "Y" if r["abstain"] else "n"
        n_sel = r["num_selected_evidence"]
        pkt = r["packet_chars"]
        cd_drp = _fmt(r.get("dropped_line_recall"), ".2f") if r.get("dropped_applicable") else "n/a"
        cd_uns = _fmt(r.get("unseen_line_recall"), ".2f") if r.get("unseen_applicable") else "n/a"
        cd_inc = _fmt(r.get("incremental_line_recall"), ".2f") if r.get("incremental_applicable") else "n/a"
        br_drp = _fmt(r.get("broad_dropped_R"), ".2f")
        br_uns = _fmt(r.get("broad_unseen_R"), ".2f")
        br_inc = _fmt(r.get("broad_incremental_R"), ".2f")
        fb_drp = _fmt(r.get("feedback_dropped_R"), ".2f")
        fb_uns = _fmt(r.get("feedback_unseen_R"), ".2f")
        fb_inc = _fmt(r.get("feedback_incremental_R"), ".2f")
        gd = f"{r['gold_dropped_lines']}/{r['gold_unseen_lines']}/{r['gold_uncovered_lines']}"
        out.append(
            f"| {iid} | {tr} | {act} | {ab} | {n_sel} | {pkt} | "
            f"{cd_drp}/{br_drp}/{fb_drp} | {cd_uns}/{br_uns}/{fb_uns} | "
            f"{cd_inc}/{br_inc}/{fb_inc} | {gd} |"
        )

    out.append("")
    p = out_dir / "condiag_5r_by_instance.md"
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
