"""D4-8 / D4-9 compare matrix builder.

Walks a runs/<agent>/<baseline>/<instance>/ tree and emits a CSV row per
(instance, baseline) pair with the fields needed for packet-level compare.

Fields (per user spec, expanded for D4-8.5):
    instance_id
    baseline
    trigger_type
    should_retry
    packet_mode             (feedback_only / broad_rg / broad_no_repo /
                             condiag_retrieval / condiag_noop /
                             condiag_diagnostic_only_no_repo / skipped_no_retry)
    intervention_mode
    has_context_packet
    packet_chars
    num_candidates          (broad: rg hits / spans; condiag: selected evidence count)
    num_selected_evidence   (condiag_packet_only only)
    num_broad_candidates    (broad_expansion only)
    has_selected_evidence
    has_broad_candidates
    has_recovery_report
    rg_executed             (broad_expansion only; True if any rg query ran)
    repo_ready              (yes/no/n-a, from manifest row intervention_report.repo_base)
    final_source            (always attempt_1 in v0 packet_only)
    validator_status
    leakage_status          ("clean" | "leakage_hits:N" | "n/a")
    pathology               (condiag_packet_only only)
    primary_5r_action       (condiag_packet_only only)

Run:
    python3 -m experiments.summarize_pilot50 \
        --runs-root /mnt/d/condiag-artifacts/condiag/v0/d4_8_smoke/runs \
        --out /mnt/d/condiag-artifacts/condiag/v0/d4_8_smoke/d4_8_smoke_matrix.csv
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Optional


BASELINES = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]


def _safe_read_json(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _leakage_status(validate: dict) -> str:
    if not validate:
        return "n/a"
    hits = validate.get("leakage_hits") or []
    if hits:
        return f"leakage_hits:{len(hits)}"
    return "clean"


def summarize_run(run_dir: Path, baseline: str) -> dict:
    """Build a single matrix row from one run directory."""
    run_dir = Path(run_dir)
    row = {
        "instance_id": run_dir.name,
        "baseline": baseline,
        "trigger_type": "",
        "should_retry": "",
        "packet_mode": "",
        "intervention_mode": "",
        "has_context_packet": False,
        "packet_chars": 0,
        "num_candidates": 0,
        "num_selected_evidence": 0,
        "num_broad_candidates": 0,
        "has_selected_evidence": False,
        "has_broad_candidates": False,
        "has_recovery_report": False,
        "rg_executed": False,
        "rg_queries_count": 0,
        "rg_hits_count": 0,
        "candidate_source_distribution": "",
        "actions_done": 0,
        "actions_skipped": 0,
        "actions_candidates_total": 0,
        "executed_actions_summary": "",
        "repo_ready": "n-a",
        "repo_status": "",
        "runtime_gap_status": "",
        "context_primary_type": "",
        "cost_total_tokens": 0,
        "final_source": "",
        "validator_status": "",
        "leakage_status": "n/a",
        "pathology": "",
        "primary_5r_action": "",
    }

    # run_report.json (always present)
    rr = _safe_read_json(run_dir / "run_report.json") or {}
    validate = rr.get("validator_details") or {}
    row["validator_status"] = rr.get("validator_status") or ""
    row["leakage_status"] = _leakage_status(validate)
    row["final_source"] = rr.get("final_source") or ""

    # cost.json (always present)
    cost = _safe_read_json(run_dir / "cost.json") or {}
    usage = cost.get("usage") or {}
    # token fields vary by agent; sum the common ones
    row["cost_total_tokens"] = (
        int(usage.get("total_tokens") or 0)
        or (int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0))
    )

    # intervention/ (absent for base_miniswe)
    ireport_path = run_dir / "intervention" / "intervention_report.json"
    ireport = _safe_read_json(ireport_path) or {}
    row["trigger_type"] = ireport.get("trigger_type", "")
    row["should_retry"] = ireport.get("should_retry", "")
    row["intervention_mode"] = ireport.get("mode", "")
    row["packet_mode"] = ireport.get("packet_mode", "")
    row["rg_executed"] = bool(ireport.get("rg_executed", False))
    row["runtime_gap_status"] = ireport.get("runtime_gap_status", "")
    repo_base = ireport.get("repo_base") or ""
    if repo_base:
        row["repo_ready"] = "yes" if Path(repo_base).is_dir() else "no"

    pkt_path = run_dir / "intervention" / "context_packet.md"
    if pkt_path.is_file():
        row["has_context_packet"] = True
        row["packet_chars"] = pkt_path.stat().st_size

    # baseline-specific artifacts
    se_path = run_dir / "intervention" / "selected_evidence.json"
    if se_path.is_file():
        se = _safe_read_json(se_path) or {}
        row["has_selected_evidence"] = True
        n_se = len(se.get("evidence") or [])
        row["num_selected_evidence"] = n_se
        if baseline == "condiag_packet_only":
            row["num_candidates"] = n_se

    bc_path = run_dir / "intervention" / "broad_candidates.jsonl"
    if bc_path.is_file():
        row["has_broad_candidates"] = True
        lines = [l for l in bc_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        n_bc = len(lines)
        row["num_broad_candidates"] = n_bc
        if baseline == "broad_expansion":
            row["num_candidates"] = n_bc

    # Broad-expansion specific: expansion_report.json
    exp_path = run_dir / "intervention" / "expansion_report.json"
    if exp_path.is_file():
        exp = _safe_read_json(exp_path) or {}
        row["rg_queries_count"] = int(exp.get("rg_queries_total") or 0)
        row["rg_hits_count"] = int(exp.get("rg_hits_total") or 0)
        by_source = exp.get("by_source") or {}
        if by_source:
            # short representation: "SRC=n,SRC=n"
            row["candidate_source_distribution"] = ",".join(
                f"{k}={v}" for k, v in by_source.items()
            )

    # ConDiag-specific: recovery_report.json + executed_actions.json
    rec_path = run_dir / "intervention" / "recovery_report.json"
    if rec_path.is_file():
        row["has_recovery_report"] = True
        rec = _safe_read_json(rec_path) or {}
        diag = rec.get("diagnosis") or {}
        row["pathology"] = diag.get("pathology", "")
        row["primary_5r_action"] = diag.get("primary_5r_action", "")
        row["repo_status"] = rec.get("repo_status", "")
        # primary_missing_context_type lives in the diagnosis block in v0
        row["context_primary_type"] = (
            rec.get("primary_missing_context_type")
            or diag.get("primary_missing_context_type", "")
        )

    ea_path = run_dir / "intervention" / "executed_actions.json"
    if ea_path.is_file():
        ea = _safe_read_json(ea_path) or {}
        ea_summary = ea.get("summary") or {}
        row["actions_done"] = int(ea_summary.get("done") or 0)
        row["actions_skipped"] = int(ea_summary.get("skipped") or 0)
        row["actions_candidates_total"] = int(ea_summary.get("candidates_total") or 0)
        row["executed_actions_summary"] = (
            f"done={row['actions_done']},skipped={row['actions_skipped']},"
            f"cands={row['actions_candidates_total']}"
        )
        if not row["repo_status"]:
            row["repo_status"] = ea.get("repo_status", "")

    return row


def build_matrix(runs_root: Path, out_csv: Path, baselines: Optional[list[str]] = None) -> int:
    runs_root = Path(runs_root)
    baselines = baselines or BASELINES

    # Discover agent dirs (usually just "miniswe")
    agent_dirs = [d for d in runs_root.iterdir() if d.is_dir()]
    rows: list[dict] = []
    for agent_dir in agent_dirs:
        for baseline in baselines:
            bdir = agent_dir / baseline
            if not bdir.is_dir():
                continue
            for inst_dir in sorted(bdir.iterdir()):
                if not inst_dir.is_dir():
                    continue
                rows.append(summarize_run(inst_dir, baseline))

    fieldnames = list(rows[0].keys()) if rows else []
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"[matrix] {len(rows)} rows -> {out_csv}")
    return len(rows)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--runs-root", required=True,
                   help="root containing <agent>/<baseline>/<instance>/ tree")
    p.add_argument("--out", required=True, help="output CSV path")
    p.add_argument("--baselines", default=",".join(BASELINES),
                   help="comma-separated baseline names to include")
    args = p.parse_args(argv)

    baselines = [b.strip() for b in args.baselines.split(",") if b.strip()]
    n = build_matrix(Path(args.runs_root), Path(args.out), baselines=baselines)
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
