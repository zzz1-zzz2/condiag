"""ConDiag Manual-ConDiag Guard orchestrator (RESTRAIN / Scope Guard flow).

Pipeline:
    resolve repo@base_commit
  -> load case + manual_diagnosis + leakage_guard
  -> normalize diagnosis
  -> patch_scope_analyzer  (parses patch.diff + runtime shape)
  -> edit_support_checker  (per-file verdict, repo-rooted target_hint scan)
  -> scope_guard_executor  (SCOPE_CONSTRAIN / PATCH_PRUNE_CANDIDATES / etc.)
  -> patch_prune_suggester (consumed by executor)
  -> build context_packet.md (RESTRAIN template)
  -> write outputs

Outputs (under <out>/<instance>/):
    repo_resolution.json
    patch_scope_report.json
    edit_support_map.json
    patch_prune_report.json
    scope_guard_actions.json
    scope_guard_candidates.jsonl
    context_packet.md
    scope_guard_report.json

Does NOT call any LLM. Reads only repo@base_commit + runtime_signals +
manual_diagnosis (no gold_check, no official_eval).
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import patch_scope_analyzer as psa
from . import edit_support_checker as esc
from . import scope_guard_executor as sge
from . import repo_resolver
from .context_packet_builder import build_context_packet_md_guard
from .diagnosis_normalizer import normalize
from .leakage_guard import check_manual_diagnosis, check_runtime_signals
from .loader import load_case_bundle, load_manual_diagnosis, load_taxonomy
from .schemas import (
    ConDiagLeakageError,
    ConDiagSchemaError,
    ConDiagTaxonomyError,
)


def run(
    instance_id: str,
    root: Path,
    repo_path: Path,
    out_dir: Path,
) -> dict:
    """Run Manual-ConDiag Guard for one case."""
    root = Path(root).resolve()
    repo_path = Path(repo_path).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    started_at = time.time()
    report = {
        "instance_id": instance_id,
        "ok": False,
        "started_at_unix": started_at,
        "root": str(root),
        "repo_path": str(repo_path),
        "out_dir": str(out_dir),
        "stages": [],
        "errors": [],
        "guarantees": {
            "used_repo_at_base_commit": False,
            "did_not_read_gold_check": True,
            "did_not_read_official_eval": True,
        },
    }

    def _stage(name, ok, detail=None):
        report["stages"].append({"stage": name, "ok": bool(ok), "detail": detail or {}})

    # 1. Load taxonomy
    taxonomy_path = root / "pathology_taxonomy.json"
    taxonomy = load_taxonomy(taxonomy_path)
    _stage("load_taxonomy", True, {"schema_version": taxonomy.schema_version})

    # 2. Load case + manual diagnosis
    case_dir = root / "case_bundles" / instance_id
    rs, _paths = load_case_bundle(case_dir)
    md = load_manual_diagnosis(
        root / "manual_diagnosis" / instance_id / "manual_diagnosis.json",
        taxonomy,
    )
    _stage("load_case", True, {
        "runtime_signals_schema": rs.schema_version,
        "manual_diagnosis_mode": md.mode,
        "pathology": md.diagnosis.get("pathology"),
    })

    # 3. Leakage guard
    rs_leak = check_runtime_signals(rs, taxonomy)
    md_leak = check_manual_diagnosis(md, taxonomy)
    rs_leak.raise_if_leak()
    md_leak.raise_if_leak()
    _stage("leakage_guard", True, {
        "runtime_signals_ok": rs_leak.ok,
        "manual_diagnosis_ok": md_leak.ok,
    })

    # 4. Resolve repo
    task = json.load(open(case_dir / "task.json"))
    base_commit = task.get("base_commit", "")
    resolution = repo_resolver.resolve(repo_path, instance_id, base_commit)
    repo_resolver.write_resolution(out_dir, resolution)
    if not resolution.ok:
        report["errors"].append(f"repo_resolution_failed: {resolution.error}")
        _stage("resolve_repo", False, {"error": resolution.error})
        report["ok"] = False
        _write_report(out_dir, report, started_at)
        return report
    report["guarantees"]["used_repo_at_base_commit"] = True
    _stage("resolve_repo", True, {
        "head": resolution.head_commit_actual[:12],
        "base_commit": base_commit[:12],
        "source": resolution.source,
    })

    # 5. Normalize diagnosis
    nd = normalize(md, taxonomy)
    _stage("normalize_diagnosis", True, {
        "pathology": nd.pathology,
        "action_family": nd.action_family,
        "5r_action": nd.primary_5r_action,
    })

    # 6. Patch scope analysis
    patch_text = (case_dir / "patch.diff").read_text(encoding="utf-8", errors="ignore")
    patch_scope = psa.analyze(instance_id, patch_text, rs)
    psa.write_report(out_dir, patch_scope)
    _stage("patch_scope_analysis", True, {
        "changed_files_count": patch_scope.changed_files_count,
        "hunks_count": len(patch_scope.hunks),
        "repeated_edit_patterns_count": len(patch_scope.repeated_edit_patterns),
        "scope_anomaly_score": patch_scope.scope_anomaly_score,
        "scope_anomaly_level": patch_scope.scope_anomaly_level,
    })

    # 7. Edit support check (needs repo_root for target_hint scan)
    support_map = esc.build_support_map(
        instance_id, rs, md,
        issue_path=case_dir / "issue_statement.txt",
        patch_scope_report=patch_scope.to_dict(),
        repo_root=Path(resolution.repo_path),
    )
    esc.write_map(out_dir, support_map)
    _stage("edit_support_check", True, {
        "supported": len(support_map.supported),
        "weak": len(support_map.weak),
        "unsupported": len(support_map.unsupported),
    })

    # 8. Execute scope_guard actions (delegates to patch_prune_suggester)
    guard_results, prune_report_dict = sge.execute_plan(
        md.retrieval_plan, rs, md,
        support_map=support_map.to_dict(),
        patch_scope_report=patch_scope.to_dict(),
        instance_id=instance_id,
    )
    _write_guard_actions(out_dir, guard_results)
    _write_guard_candidates(out_dir, guard_results)
    if prune_report_dict:
        (out_dir / "patch_prune_report.json").write_text(
            json.dumps(prune_report_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    n_done = sum(1 for r in guard_results if r.status == "done")
    n_skipped = sum(1 for r in guard_results if r.status == "skipped")
    n_no_cand = sum(1 for r in guard_results if r.status == "no_candidates")
    n_cands = sum(len(r.candidates) for r in guard_results)
    _stage("scope_guard_execute", True, {
        "action_count": len(guard_results),
        "candidates_total": n_cands,
        "done": n_done, "skipped": n_skipped, "no_candidates": n_no_cand,
    })

    # 9. Build context packet (RESTRAIN template)
    packet_md = build_context_packet_md_guard(
        Path(resolution.repo_path), nd, md, rs,
        support_map=support_map.to_dict(),
        patch_scope_report=patch_scope.to_dict(),
        guard_results=[r.to_dict() for r in guard_results],
        prune_report=prune_report_dict,
    )
    (out_dir / "context_packet.md").write_text(packet_md, encoding="utf-8")
    _stage("build_context_packet", True, {
        "bytes": len(packet_md.encode("utf-8")),
        "lines": packet_md.count("\n") + 1,
    })

    # 10. Guarantees
    report["guarantees"]["did_not_read_gold_check"] = True
    report["guarantees"]["did_not_read_official_eval"] = True

    report["ok"] = True
    _write_report(out_dir, report, started_at)
    return report


# ===== writers =====

def _write_guard_actions(out_dir: Path, results) -> None:
    payload = {
        "actions": [r.to_dict() for r in results],
        "summary": {
            "action_count": len(results),
            "done": sum(1 for r in results if r.status == "done"),
            "skipped": sum(1 for r in results if r.status == "skipped"),
            "no_candidates": sum(1 for r in results if r.status == "no_candidates"),
            "candidates_total": sum(len(r.candidates) for r in results),
        },
    }
    (out_dir / "scope_guard_actions.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_guard_candidates(out_dir: Path, results) -> None:
    p = out_dir / "scope_guard_candidates.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in results:
            for c in r.candidates:
                d = c.to_dict()
                d["action_status"] = r.status
                f.write(json.dumps(d, ensure_ascii=False) + "\n")


def _write_report(out_dir: Path, report: dict, started_at: float) -> None:
    ended_at = time.time()
    report["ended_at_unix"] = ended_at
    report["duration_seconds"] = round(ended_at - started_at, 3)
    (out_dir / "scope_guard_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
