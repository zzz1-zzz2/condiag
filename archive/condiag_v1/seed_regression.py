"""ConDiag v0 seed regression test runner.

Runs the four locked seed flows (RECONCILE / RESTRAIN / REHYDRATE / RETRIEVE)
end-to-end via the unified `manual-recovery` dispatch, then diffs each output
file against the corresponding `.locked_v0` baseline. Produces a CSV matrix
that makes regression visible at a glance.

Locked baselines:
    manual_retrieval_outputs/sympy__sympy-16597.locked_v0   (RECONCILE)
    manual_guard_outputs/sympy__sympy-13877.locked_v0        (RESTRAIN)
    manual_retrieval_outputs/astropy__astropy-13398.locked_v0 (REHYDRATE)
    manual_retrieval_outputs/django__django-11400.locked_v0  (RETRIEVE)

Outputs (under <out>/):
    <case>/current/                     # freshly produced artifacts
    seed_regression_matrix.csv          # one row per case
    seed_regression_report.json         # summary
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path
from typing import List

from . import manual_guard as mg
from . import manual_retrieval as mr
from .diagnosis_normalizer import normalize
from .loader import load_manual_diagnosis, load_taxonomy
from .schemas import (
    ConDiagLeakageError,
    ConDiagSchemaError,
    ConDiagTaxonomyError,
)


SEED_CASES = [
    "sympy__sympy-16597",
    "sympy__sympy-13877",
    "astropy__astropy-13398",
    "django__django-11400",
    "django__django-13195",
]

# Files that must match between current and locked_v0
CRITICAL_FILES_RETRIEVAL = [
    "executed_actions.json",
    "selected_evidence.json",
    "context_packet.md",
    "retrieved_candidates.jsonl",
]
CRITICAL_FILES_GUARD = [
    "scope_guard_actions.json",
    "edit_support_map.json",
    "patch_prune_report.json",
    "context_packet.md",
]
CRITICAL_FILES_NOOP = [
    "context_packet.md",
    "recovery_report.json",
]


def _locked_dir_for(root: Path, instance_id: str, dispatch: str) -> Path | None:
    """Locate the locked_v0 baseline directory for a given case + dispatch."""
    if dispatch == "guard":
        candidate = root / "manual_guard_outputs" / f"{instance_id}.locked_v0"
    elif dispatch == "noop":
        candidate = root / "manual_noop_outputs" / f"{instance_id}.locked_v0"
    else:
        candidate = root / "manual_retrieval_outputs" / f"{instance_id}.locked_v0"
    return candidate if candidate.is_dir() else None


def _dispatch_for_case(root: Path, instance_id: str) -> tuple[str, str]:
    """Return (dispatch, 5r_action) without running anything heavy."""
    taxonomy = load_taxonomy(root / "pathology_taxonomy.json")
    diag_path = root / "manual_diagnosis" / instance_id / "manual_diagnosis.json"
    md = load_manual_diagnosis(diag_path, taxonomy)
    nd = normalize(md, taxonomy)
    # Mirror cli._dispatch_recovery_action
    action_family = nd.action_family
    action_5r = nd.primary_5r_action or ""
    if action_family == "NOOP":
        return "noop", action_5r
    if action_family in ("ABSTAIN", "EVALUATION_ONLY"):
        return action_family.lower(), action_5r
    if action_5r == "RESTRAIN" or action_family == "GUARD":
        return "guard", action_5r
    return "retrieval", action_5r


def _file_diff(a: Path, b: Path) -> tuple[bool, str]:
    """Return (matches, summary). 'matches' is True iff contents are byte-identical."""
    if not a.exists() or not b.exists():
        return False, f"missing: a={a.exists()} b={b.exists()}"
    la = a.read_bytes()
    lb = b.read_bytes()
    if la == lb:
        return True, "identical"
    return False, f"diff bytes_a={len(la)} bytes_b={len(lb)}"


def _trigger_type_for(root: Path, instance_id: str) -> str:
    """Best-effort trigger_type read from dry-run output, blank if not available."""
    p = root / "dry_run_outputs" / instance_id / "trigger_result.json"
    if not p.is_file():
        return ""
    try:
        return json.loads(p.read_text()).get("trigger_type", "")
    except Exception:
        return ""


def _read_post_run_summary(out_dir: Path, dispatch: str) -> dict:
    """Pull num_candidates / num_selected / sections / guarantees from the freshly
    produced run output."""
    summary = {
        "num_executed_actions": "",
        "num_candidates": "",
        "num_selected_evidence": "",
        "num_control_candidates": "",
        "guarantees_ok": "",
        "context_packet_sections_ok": "",
    }
    import re
    if dispatch == "retrieval":
        # candidates come from executed_actions.json
        ea_p = out_dir / "executed_actions.json"
        if ea_p.is_file():
            ea = json.loads(ea_p.read_text())
            actions = ea.get("actions", []) or []
            summary["num_executed_actions"] = len(actions)
            cand_total = 0
            for a in actions:
                cand_total += int(a.get("candidate_count", 0))
            summary["num_candidates"] = cand_total
            # control candidates not applicable for retrieval dispatch
            summary["num_control_candidates"] = 0
        # selected comes from selected_evidence.json
        se_p = out_dir / "selected_evidence.json"
        if se_p.is_file():
            se = json.loads(se_p.read_text())
            ev = se.get("evidence") or se.get("selected_evidence") or []
            summary["num_selected_evidence"] = len(ev) if isinstance(ev, list) else ""
        # guarantees come from retrieval_report.json
        rp = out_dir / "retrieval_report.json"
        if rp.is_file():
            r = json.loads(rp.read_text())
            g = r.get("guarantees", {}) or {}
            summary["guarantees_ok"] = bool(
                g.get("used_repo_at_base_commit")
                and g.get("did_not_read_gold_check")
                and g.get("did_not_read_official_eval")
            )
        cp = out_dir / "context_packet.md"
        if cp.is_file():
            sections = re.findall(r"^## (.+)$", cp.read_text(encoding="utf-8"), re.MULTILINE)
            summary["context_packet_sections_ok"] = len(sections) >= 5
    elif dispatch == "guard":
        # candidates from scope_guard_actions.json (these are control actions)
        sa_p = out_dir / "scope_guard_actions.json"
        if sa_p.is_file():
            sa = json.loads(sa_p.read_text())
            actions = sa.get("actions", []) or []
            summary["num_executed_actions"] = len(actions)
            cand_total = 0
            for a in actions:
                cand_total += int(a.get("candidate_count", 0))
            summary["num_candidates"] = cand_total
            summary["num_control_candidates"] = cand_total
        # selected from edit_support_map.json (count files analyzed)
        es_p = out_dir / "edit_support_map.json"
        if es_p.is_file():
            es = json.loads(es_p.read_text())
            total = (
                len(es.get("supported", []) or [])
                + len(es.get("weak", []) or [])
                + len(es.get("unsupported", []) or [])
            )
            summary["num_selected_evidence"] = total
        rp = out_dir / "scope_guard_report.json"
        if rp.is_file():
            r = json.loads(rp.read_text())
            g = r.get("guarantees", {}) or {}
            summary["guarantees_ok"] = bool(
                g.get("used_repo_at_base_commit")
                and g.get("did_not_read_gold_check")
                and g.get("did_not_read_official_eval")
            )
        cp = out_dir / "context_packet.md"
        if cp.is_file():
            sections = re.findall(r"^## (.+)$", cp.read_text(encoding="utf-8"), re.MULTILINE)
            summary["context_packet_sections_ok"] = len(sections) >= 5
    elif dispatch in ("noop", "abstain", "evaluation_only"):
        # NOOP minimal packet — guarantees from recovery_report.json
        summary["num_executed_actions"] = 0
        summary["num_candidates"] = 0
        summary["num_control_candidates"] = 0
        summary["num_selected_evidence"] = 0
        rp = out_dir / "recovery_report.json"
        if rp.is_file():
            r = json.loads(rp.read_text())
            g = r.get("guarantees", {}) or {}
            summary["guarantees_ok"] = bool(
                g.get("did_not_read_gold_check")
                and g.get("did_not_read_official_eval")
            )
        cp = out_dir / "context_packet.md"
        if cp.is_file():
            # minimal packet has fewer sections (Diagnosis + Note + optional Retry)
            sections = re.findall(r"^## (.+)$", cp.read_text(encoding="utf-8"), re.MULTILINE)
            summary["context_packet_sections_ok"] = len(sections) >= 2
    return summary


def run(
    root: Path,
    cases: List[str] | None,
    out: Path,
) -> dict:
    """Run the seed regression suite.

    Returns the report dict (also written to seed_regression_report.json).
    """
    root = Path(root).resolve()
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    cases = cases or SEED_CASES

    started_at = time.time()
    rows: list[dict] = []
    per_case: list[dict] = []

    for case in cases:
        row = {
            "instance": case,
            "pathology": "",
            "5r_action": "",
            "action_family": "",
            "trigger_type": "",
            "dispatch": "",
            "ok": False,
            "num_executed_actions": "",
            "num_candidates": "",
            "num_selected_evidence": "",
            "num_control_candidates": "",
            "guarantees_ok": "",
            "context_packet_sections_ok": "",
            "locked_baseline_exists": False,
            "locked_baseline_match": "",
            "locked_baseline_diff_summary": "",
            "duration_seconds": "",
        }
        try:
            # Get pathology / action_family from manual_diagnosis directly (lightweight, no run)
            taxonomy = load_taxonomy(root / "pathology_taxonomy.json")
            diag_path = root / "manual_diagnosis" / case / "manual_diagnosis.json"
            md = load_manual_diagnosis(diag_path, taxonomy)
            from .diagnosis_normalizer import normalize as _normalize
            nd = _normalize(md, taxonomy)
            row["pathology"] = nd.pathology
            row["action_family"] = nd.action_family

            dispatch, action_5r = _dispatch_for_case(root, case)
            row["5r_action"] = action_5r
            row["dispatch"] = dispatch
            row["trigger_type"] = _trigger_type_for(root, case)

            case_out = out / case / "current"
            case_out.mkdir(parents=True, exist_ok=True)

            t0 = time.time()
            if dispatch == "retrieval":
                # Locate repo_base from the standard workspace path
                repo_path = Path(f"/home/swelite/condiag/workspaces/{case}/repo_base")
                if not repo_path.is_dir():
                    raise FileNotFoundError(f"repo_base missing: {repo_path}")
                mr.run(case, root, repo_path, case_out)
            elif dispatch == "guard":
                # Locate repo_base from the standard workspace path
                repo_path = Path(f"/home/swelite/condiag/workspaces/{case}/repo_base")
                if not repo_path.is_dir():
                    raise FileNotFoundError(f"repo_base missing: {repo_path}")
                mg.run(case, root, repo_path, case_out)
            elif dispatch in ("noop", "abstain", "evaluation_only"):
                # minimal packet — no repo needed
                from .cli import (
                    _write_minimal_packet as _wmp,
                    _write_top_level_recovery_report as _wtl,
                )
                sub_report = _wmp(case_out, case, dispatch, nd)
                _wtl(case_out, case, dispatch, nd, sub_report, None)
            else:
                raise RuntimeError(f"unsupported dispatch '{dispatch}' for case {case}")
            row["duration_seconds"] = round(time.time() - t0, 3)

            # Summarise the fresh run
            summary = _read_post_run_summary(case_out, dispatch)
            row.update(summary)

            # Compare to locked_v0
            locked = _locked_dir_for(root, case, dispatch)
            row["locked_baseline_exists"] = locked is not None
            if locked is None:
                row["locked_baseline_match"] = False
                row["locked_baseline_diff_summary"] = f"no .locked_v0 under {root}"
            else:
                if dispatch == "guard":
                    crit = CRITICAL_FILES_GUARD
                elif dispatch in ("noop", "abstain", "evaluation_only"):
                    crit = CRITICAL_FILES_NOOP
                else:
                    crit = CRITICAL_FILES_RETRIEVAL
                diffs = []
                all_match = True
                for fname in crit:
                    a = case_out / fname
                    b = locked / fname
                    ok, note = _file_diff(a, b)
                    if not ok:
                        all_match = False
                        diffs.append(f"{fname}: {note}")
                row["locked_baseline_match"] = all_match
                row["locked_baseline_diff_summary"] = "; ".join(diffs) if diffs else "all critical files identical"

            row["ok"] = bool(
                summary.get("guarantees_ok")
                and summary.get("context_packet_sections_ok")
                and row["locked_baseline_match"] is True
            )
        except (ConDiagSchemaError, ConDiagTaxonomyError, ConDiagLeakageError) as e:
            row["ok"] = False
            row["locked_baseline_diff_summary"] = f"{type(e).__name__}: {e}"
        except Exception as e:
            row["ok"] = False
            row["locked_baseline_diff_summary"] = f"{type(e).__name__}: {e}"

        rows.append(row)
        per_case.append({"instance": case, **row})

    # Write CSV
    csv_path = out / "seed_regression_matrix.csv"
    fieldnames = list(rows[0].keys()) if rows else []
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow(r)

    ended_at = time.time()
    report = {
        "schema_version": "condiag.seed_regression_summary.v0",
        "started_at_unix": started_at,
        "duration_seconds": round(ended_at - started_at, 3),
        "cases_total": len(rows),
        "cases_ok": sum(1 for r in rows if r["ok"]),
        "cases_locked_match": sum(1 for r in rows if r["locked_baseline_match"] is True),
        "rows": rows,
        "csv_path": str(csv_path),
    }
    # Primary summary file (user-requested name)
    (out / "seed_regression_summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Backward-compat alias
    (out / "seed_regression_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report
