"""ConDiag CLI.

Usage:
    python -m condiag.cli dry-run-cases \\
        --root /mnt/d/condiag-artifacts/condiag/v0 \\
        --cases astropy__astropy-13398 django__django-11400 sympy__sympy-13877 sympy__sympy-16597 \\
        --out /mnt/d/condiag-artifacts/condiag/v0/dry_run_outputs

The dry-run does NOT call any LLM and does NOT run any retrieval. It only:
    load → validate schema → check leakage → classify trigger →
    normalize diagnosis → split action plan → write reports
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, List

from . import __version__
from .action_planner import build_plan
from .diagnosis_normalizer import normalize
from .leakage_guard import check_manual_diagnosis, check_runtime_signals
from .loader import load_case_bundle, load_manual_diagnosis, load_taxonomy
from .report import (
    _next_module,
    _runtime_evidence_strength,
    write_action_plan,
    write_case_matrix,
    write_context_packet_skeleton,
    write_leakage_report,
    write_load_report,
    write_normalized_diagnosis,
    write_summary,
    write_trigger_result,
)
from .schemas import (
    ConDiagLeakageError,
    ConDiagSchemaError,
    ConDiagTaxonomyError,
)
from .trigger import classify


def _log(msg: str) -> None:
    print(msg, flush=True)


def _resolve_case_dirs(root: Path, cases: List[str]) -> List[Path]:
    return [Path(root) / "case_bundles" / c for c in cases]


def _resolve_diagnosis_path(root: Path, instance_id: str) -> Path:
    return Path(root) / "manual_diagnosis" / instance_id / "manual_diagnosis.json"


def _run_one_case(
    case_dir: Path,
    diagnosis_path: Path,
    taxonomy,
    out_dir: Path,
) -> dict:
    """Returns a CSV-row-shaped dict for the aggregate matrix."""
    instance_id = case_dir.name
    row = {
        "instance_id": instance_id,
        "runtime_trigger": "NO_TRIGGER",
        "manual_pathology": "",
        "runtime_pathology_candidate": "",
        "action_family": "",
        "5r_action": "",
        "confidence_manual": "",
        "confidence_runtime": "",
        "runtime_evidence_strength": "low",
        "next_module": "",
        "mode": "",
        "ok": False,
    }
    notes: list[str] = []

    try:
        rs, paths = load_case_bundle(case_dir)
    except (FileNotFoundError, ConDiagSchemaError) as e:
        notes.append(f"load_case_bundle FAILED: {e}")
        write_load_report(out_dir, instance_id, None_obj(), None_obj(),
                          _empty_leak(), _empty_leak(), False, notes)
        row["ok"] = False
        return row

    # Manual diagnosis is optional but expected for dry-run.
    try:
        md = load_manual_diagnosis(diagnosis_path, taxonomy)
        paths.manual_diagnosis_f = diagnosis_path
        taxonomy_check_ok = True
    except (FileNotFoundError, ConDiagSchemaError, ConDiagTaxonomyError) as e:
        notes.append(f"load_manual_diagnosis FAILED: {e}")
        md = None_obj()
        taxonomy_check_ok = False

    # Leakage checks
    rs_leak = check_runtime_signals(rs, taxonomy)
    md_leak = check_manual_diagnosis(md, taxonomy) if md.schema_version else _empty_leak()
    rs_leak.raise_if_leak()
    if taxonomy_check_ok:
        md_leak.raise_if_leak()

    # Classify trigger
    tr = classify(rs, taxonomy)

    # Normalize + plan
    if taxonomy_check_ok:
        nd = normalize(md, taxonomy)
        ap = build_plan(md, nd, taxonomy)
    else:
        nd = None
        ap = None

    # Write per-case reports
    write_leakage_report(out_dir, instance_id, rs_leak, md_leak)
    write_load_report(out_dir, instance_id, rs, md, rs_leak, md_leak,
                      taxonomy_check_ok, notes)
    write_trigger_result(out_dir, tr)
    if nd:
        write_normalized_diagnosis(out_dir, nd)
    if ap:
        write_action_plan(out_dir, ap)
    if nd:
        write_context_packet_skeleton(out_dir, nd, tr, rs)

    # Fill CSV row
    row["runtime_trigger"] = tr.trigger_type
    row["manual_pathology"] = (nd.pathology if nd else "")
    if tr.inferred_pathology_candidates:
        row["runtime_pathology_candidate"] = tr.inferred_pathology_candidates[0]["pathology"]
    row["action_family"] = (nd.action_family if nd else tr.inferred_action_family)
    row["5r_action"] = (nd.primary_5r_action if nd else None) or ""
    row["confidence_manual"] = (nd.confidence if nd else "")
    row["confidence_runtime"] = tr.confidence_runtime
    row["runtime_evidence_strength"] = _runtime_evidence_strength(tr, rs)
    row["next_module"] = _next_module(nd) if nd else "—"
    row["mode"] = md.mode if md.mode else ""
    row["ok"] = bool(taxonomy_check_ok and rs_leak.ok and md_leak.ok)

    return row


def _empty_leak():
    from .leakage_guard import LeakageReport
    return LeakageReport()


def None_obj():
    """Empty objects for early-fail paths."""
    from .schemas import ManualDiagnosis, RuntimeSignals
    return ManualDiagnosis()


def cmd_dry_run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    taxonomy_path = Path(args.taxonomy or (root / "pathology_taxonomy.json"))

    started_at = time.time()

    _log(f"[OK] loading taxonomy from {taxonomy_path}")
    taxonomy = load_taxonomy(taxonomy_path)
    _log(f"[OK] taxonomy schema_version={taxonomy.schema_version}, "
         f"{len(taxonomy.pathologies)} pathologies")

    cases = args.cases
    if not cases:
        # default: all case_bundles present
        bundles_dir = root / "case_bundles"
        cases = sorted(d.name for d in bundles_dir.iterdir() if d.is_dir())
    _log(f"[OK] dry-run target cases: {cases}")

    case_dirs = _resolve_case_dirs(root, cases)
    rows: list[dict] = []
    for case_dir, case_id in zip(case_dirs, cases):
        diag_path = _resolve_diagnosis_path(root, case_id)
        try:
            row = _run_one_case(case_dir, diag_path, taxonomy, out_dir)
        except ConDiagLeakageError as e:
            _log(f"[LEAK] {case_id}: {e}")
            row = {
                "instance_id": case_id, "runtime_trigger": "",
                "manual_pathology": "", "runtime_pathology_candidate": "",
                "action_family": "", "5r_action": "",
                "confidence_manual": "", "confidence_runtime": "",
                "runtime_evidence_strength": "", "next_module": "",
                "mode": "", "ok": False,
            }
        except Exception as e:
            _log(f"[ERR] {case_id}: {type(e).__name__}: {e}")
            row = {
                "instance_id": case_id, "runtime_trigger": "",
                "manual_pathology": "", "runtime_pathology_candidate": "",
                "action_family": "", "5r_action": "",
                "confidence_manual": "", "confidence_runtime": "",
                "runtime_evidence_strength": "", "next_module": "",
                "mode": "", "ok": False,
            }
        rows.append(row)
        status = "OK" if row["ok"] else "FAIL"
        _log(f"  [{status}] {case_id}: trigger={row['runtime_trigger']} "
             f"pathology={row['manual_pathology']} 5R={row['5r_action']}")

    matrix_path = write_case_matrix(out_dir, rows)
    summary_path = write_summary(out_dir, started_at, time.time(), rows, root)
    ended_at = time.time()

    _log(f"[OK] wrote case matrix: {matrix_path}")
    _log(f"[OK] wrote summary: {summary_path}")
    _log(f"[OK] duration: {ended_at - started_at:.2f}s  cases: {len(rows)}  "
         f"ok: {sum(1 for r in rows if r['ok'])}")

    return 0 if all(r["ok"] for r in rows) else 1


def cmd_manual_retrieval(args: argparse.Namespace) -> int:
    from . import manual_retrieval as mr
    root = Path(args.root).resolve()
    repo_path = Path(args.repo).resolve()
    out_dir = Path(args.out).resolve()

    _log(f"[OK] manual-retrieval: case={args.case}")
    _log(f"     root={root}")
    _log(f"     repo={repo_path}")
    _log(f"     out ={out_dir}")

    try:
        report = mr.run(args.case, root, repo_path, out_dir)
    except (ConDiagSchemaError, ConDiagTaxonomyError, ConDiagLeakageError) as e:
        _log(f"[FAIL] {type(e).__name__}: {e}")
        return 2
    except Exception as e:
        _log(f"[ERR] {type(e).__name__}: {e}")
        return 1

    _log(f"[{'OK' if report['ok'] else 'FAIL'}] stages:")
    for s in report["stages"]:
        status = "OK" if s["ok"] else "FAIL"
        _log(f"  [{status}] {s['stage']}: {s['detail']}")
    _log(f"[OK] guarantees: {report['guarantees']}")
    _log(f"[OK] duration: {report.get('duration_seconds', 0)}s")
    return 0 if report["ok"] else 1


def cmd_manual_guard(args: argparse.Namespace) -> int:
    from . import manual_guard as mg
    root = Path(args.root).resolve()
    repo_path = Path(args.repo).resolve()
    out_dir = Path(args.out).resolve()

    _log(f"[OK] manual-guard: case={args.case}")
    _log(f"     root={root}")
    _log(f"     repo={repo_path}")
    _log(f"     out ={out_dir}")

    try:
        report = mg.run(args.case, root, repo_path, out_dir)
    except (ConDiagSchemaError, ConDiagTaxonomyError, ConDiagLeakageError) as e:
        _log(f"[FAIL] {type(e).__name__}: {e}")
        return 2
    except Exception as e:
        _log(f"[ERR] {type(e).__name__}: {e}")
        return 1

    _log(f"[{'OK' if report['ok'] else 'FAIL'}] stages:")
    for s in report["stages"]:
        status = "OK" if s["ok"] else "FAIL"
        _log(f"  [{status}] {s['stage']}: {s['detail']}")
    _log(f"[OK] guarantees: {report['guarantees']}")
    _log(f"[OK] duration: {report.get('duration_seconds', 0)}s")
    return 0 if report["ok"] else 1


# =====================================================================
# manual-recovery: unified entry that dispatches based on action_family / 5r_action
# =====================================================================

# 5R actions routed to the retrieval executor
_RETRIEVAL_5R = {"RECONCILE", "REHYDRATE", "RETRIEVE", "RELOCALIZE"}
# 5R actions routed to the scope-guard executor
_GUARD_5R = {"RESTRAIN"}


def _dispatch_recovery_action(action_family: str, action_5r: str | None) -> str:
    """Return one of: 'retrieval', 'guard', 'noop', 'abstain', 'evaluation_only'."""
    if action_family == "NOOP":
        return "noop"
    if action_family == "ABSTAIN":
        return "abstain"
    if action_family == "EVALUATION_ONLY":
        return "evaluation_only"
    if action_5r in _GUARD_5R:
        return "guard"
    if action_5r in _RETRIEVAL_5R or action_family == "RECOVERY":
        return "retrieval"
    if action_family == "GUARD":
        return "guard"
    # Unknown / unmapped → abstain as a safe default
    return "abstain"


def _write_minimal_packet(
    out_dir: Path,
    instance_id: str,
    dispatch: str,
    nd,
) -> dict:
    """For NOOP / ABSTAIN / EVALUATION_ONLY — write a minimal context packet.

    These cases do not trigger retrieval or guard executors. The packet records
    the diagnosis and explains why no further recovery action is taken.

    Returns a subflow_report-shaped dict so the caller (_write_top_level_recovery_report)
    can compose the unified recovery_report.json uniformly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "instance_id": instance_id,
        "dispatch": dispatch,
        "ok": True,
        "guarantees": {
            "used_repo_at_base_commit": False,  # not used; nothing retrieved
            "did_not_read_gold_check": True,
            "did_not_read_official_eval": True,
        },
        "duration_seconds": 0,
    }

    why = {
        "noop": "Diagnosis indicates no recovery action is needed.",
        "abstain": "Diagnosis abstains — ConDiag v0 cannot reliably assign a recovery intent.",
        "evaluation_only": "Diagnosis is evaluation-only — context packet is for human review, not agent retry.",
    }[dispatch]

    lines = [
        "# ConDiag Context Packet (minimal)",
        "",
        "## Diagnosis",
        "",
        f"- **Instance**: `{instance_id}`",
        f"- **Pathology**: `{nd.pathology}`",
        f"- **Action family**: `{nd.action_family}`",
        f"- **5R action**: `{nd.primary_5r_action or '—'}`",
        f"- **Confidence**: {nd.confidence}",
        f"- **Dispatch decision**: `{dispatch}`",
        "",
        "## Note",
        "",
        why,
        "",
    ]
    if nd.context_packet_instruction:
        lines += [
            "## Retry Instruction",
            "",
            "```",
            nd.context_packet_instruction,
            "```",
            "",
        ]
    (out_dir / "context_packet.md").write_text("\n".join(lines), encoding="utf-8")
    return report


def _write_top_level_recovery_report(
    out_dir: Path,
    instance_id: str,
    dispatch: str,
    nd,
    subflow_report: dict,
    subflow_report_filename: str | None,
) -> None:
    """Write recovery_report.json as the top-level unified summary.

    Does NOT overwrite the subflow's own report file (retrieval_report.json or
    scope_guard_report.json) — those remain authoritative for subflow-specific
    fields. This file is the cross-flow uniform entry point that always exists
    regardless of dispatch.
    """
    artifacts = sorted(p.name for p in out_dir.iterdir() if p.is_file())
    top = {
        "schema_version": "condiag.recovery_report.v0",
        "instance_id": instance_id,
        "dispatch": dispatch,
        "pathology": nd.pathology,
        "action_family": nd.action_family,
        "5r_action": nd.primary_5r_action,
        "ok": bool(subflow_report.get("ok")),
        "guarantees": subflow_report.get("guarantees", {}),
        "duration_seconds": subflow_report.get("duration_seconds"),
        "subflow_report_file": subflow_report_filename,
        "artifact_files": artifacts,
        "context_packet_file": "context_packet.md" if (out_dir / "context_packet.md").is_file() else None,
    }
    (out_dir / "recovery_report.json").write_text(
        json.dumps(top, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def cmd_manual_recovery(args: argparse.Namespace) -> int:
    """Unified entry: dispatch to retrieval / guard / noop / abstain based on diagnosis."""
    from . import manual_retrieval as mr
    from . import manual_guard as mg
    root = Path(args.root).resolve()
    repo_path = Path(args.repo).resolve() if args.repo else None
    out_dir = Path(args.out).resolve()

    taxonomy_path = Path(args.taxonomy or (root / "pathology_taxonomy.json"))
    taxonomy = load_taxonomy(taxonomy_path)
    case_dir = Path(root) / "case_bundles" / args.case
    diag_path = _resolve_diagnosis_path(root, args.case)

    try:
        md = load_manual_diagnosis(diag_path, taxonomy)
    except (FileNotFoundError, ConDiagSchemaError, ConDiagTaxonomyError) as e:
        _log(f"[FAIL] load manual_diagnosis: {e}")
        return 2

    nd = normalize(md, taxonomy)

    dispatch = _dispatch_recovery_action(nd.action_family, nd.primary_5r_action)
    _log(f"[OK] manual-recovery: case={args.case}")
    _log(f"     pathology={nd.pathology}  action_family={nd.action_family}  5R={nd.primary_5r_action}")
    _log(f"     dispatch -> {dispatch}")

    try:
        if dispatch == "retrieval":
            if repo_path is None:
                _log("[FAIL] --repo required for retrieval dispatch")
                return 2
            report = mr.run(args.case, root, repo_path, out_dir)
            _write_top_level_recovery_report(out_dir, args.case, dispatch, nd, report, "retrieval_report.json")
        elif dispatch == "guard":
            if repo_path is None:
                _log("[FAIL] --repo required for guard dispatch")
                return 2
            report = mg.run(args.case, root, repo_path, out_dir)
            _write_top_level_recovery_report(out_dir, args.case, dispatch, nd, report, "scope_guard_report.json")
        else:
            # noop / abstain / evaluation_only — minimal packet, no repo needed
            report = _write_minimal_packet(out_dir, args.case, dispatch, nd)
            _write_top_level_recovery_report(out_dir, args.case, dispatch, nd, report, None)
            _log(f"[OK] wrote minimal packet ({dispatch}) -> {out_dir}")
            _log(f"[OK] report: {report}")
            return 0
    except (ConDiagSchemaError, ConDiagTaxonomyError, ConDiagLeakageError) as e:
        _log(f"[FAIL] {type(e).__name__}: {e}")
        return 2
    except Exception as e:
        _log(f"[ERR] {type(e).__name__}: {e}")
        return 1

    _log(f"[{'OK' if report['ok'] else 'FAIL'}] stages:")
    for s in report.get("stages", []):
        status = "OK" if s["ok"] else "FAIL"
        _log(f"  [{status}] {s['stage']}: {s['detail']}")
    _log(f"[OK] guarantees: {report['guarantees']}")
    _log(f"[OK] duration: {report.get('duration_seconds', 0)}s")
    _log(f"[OK] top-level recovery_report.json written (dispatch={dispatch})")
    return 0 if report["ok"] else 1


def cmd_run_seed_regression(args: argparse.Namespace) -> int:
    """Run the four locked seed flows end-to-end and diff against .locked_v0."""
    from . import seed_regression as sr
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    cases = args.cases or list(sr.SEED_CASES)

    _log(f"[OK] run-seed-regression: cases={cases}")
    _log(f"     root={root}")
    _log(f"     out ={out}")

    report = sr.run(root, cases, out)
    _log(f"[OK] duration: {report['duration_seconds']}s")
    _log(f"[OK] cases ok: {report['cases_ok']}/{report['cases_total']}")
    _log(f"[OK] locked_baseline match: {report['cases_locked_match']}/{report['cases_total']}")
    _log(f"[OK] csv: {report['csv_path']}")
    for row in report["rows"]:
        status = "OK" if row["ok"] else "FAIL"
        locked = row["locked_baseline_match"]
        _log(f"  [{status}] {row['instance']:35s} 5R={row['5r_action']:10s} "
             f"locked={locked}  {row['locked_baseline_diff_summary'][:80]}")
    return 0 if report["cases_ok"] == report["cases_total"] else 1


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="condiag.cli", description="ConDiag v0 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    dr = sub.add_parser("dry-run-cases", help="Run ConDiag trigger + plan dry-run for case bundles")
    dr.add_argument("--root", required=True, help="ConDiag v0 artifact root (contains case_bundles/, manual_diagnosis/, pathology_taxonomy.json)")
    dr.add_argument("--cases", nargs="*", default=None, help="Instance IDs to dry-run; defaults to all under case_bundles/")
    dr.add_argument("--out", required=True, help="Output directory for dry-run reports")
    dr.add_argument("--taxonomy", default=None, help="Override path to pathology_taxonomy.json")
    dr.set_defaults(func=cmd_dry_run)

    mr = sub.add_parser("manual-retrieval", help="Run Manual-ConDiag Retrieval for one case")
    mr.add_argument("--root", required=True, help="ConDiag v0 artifact root")
    mr.add_argument("--case", required=True, help="Instance ID, e.g. sympy__sympy-16597")
    mr.add_argument("--repo", required=True, help="Path to clean repo at base_commit (verified)")
    mr.add_argument("--out", required=True, help="Output directory for retrieval artifacts")
    mr.set_defaults(func=cmd_manual_retrieval)

    mg = sub.add_parser("manual-guard", help="Run Manual-ConDiag Guard (RESTRAIN / Scope Guard) for one case")
    mg.add_argument("--root", required=True, help="ConDiag v0 artifact root")
    mg.add_argument("--case", required=True, help="Instance ID, e.g. sympy__sympy-13877")
    mg.add_argument("--repo", required=True, help="Path to clean repo at base_commit (verified)")
    mg.add_argument("--out", required=True, help="Output directory for guard artifacts")
    mg.set_defaults(func=cmd_manual_guard)

    mrc = sub.add_parser(
        "manual-recovery",
        help="Unified entry — dispatch to retrieval / guard / noop / abstain based on diagnosis",
    )
    mrc.add_argument("--root", required=True, help="ConDiag v0 artifact root")
    mrc.add_argument("--case", required=True, help="Instance ID, e.g. sympy__sympy-16597")
    mrc.add_argument("--repo", default=None, help="Path to clean repo at base_commit (required for retrieval/guard dispatch; skipped for noop/abstain)")
    mrc.add_argument("--out", required=True, help="Output directory for recovery artifacts")
    mrc.add_argument("--taxonomy", default=None, help="Override path to pathology_taxonomy.json")
    mrc.set_defaults(func=cmd_manual_recovery)

    rsr = sub.add_parser(
        "run-seed-regression",
        help="Run the four locked seed flows end-to-end and diff against .locked_v0 baselines",
    )
    rsr.add_argument("--root", required=True, help="ConDiag v0 artifact root")
    rsr.add_argument("--cases", nargs="*", default=None, help="Instance IDs; defaults to SEED_CASES")
    rsr.add_argument("--out", required=True, help="Output directory for regression artifacts + matrix CSV")
    rsr.set_defaults(func=cmd_run_seed_regression)

    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
