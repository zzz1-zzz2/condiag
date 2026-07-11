"""ConDiag Manual-ConDiag Retrieval orchestrator.

Pipeline:
    resolve repo@base_commit
  → build repository index
  → execute retrieval_plan from manual_diagnosis
  → select top-k evidence under budget
  → build context_packet.md
  → write 7 output files

Outputs (under <out>/<instance>/):
    repo_resolution.json
    repository_index_summary.json
    executed_actions.json
    retrieved_candidates.jsonl
    selected_evidence.json
    context_packet.md
    retrieval_report.json

Does NOT call any LLM. Reads only repo@base_commit + runtime_signals +
manual_diagnosis (no gold_check, no official_eval).
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import List

from . import repository_index as ri
from . import repo_resolver
from .evidence_selector import select as select_evidence
from .context_packet_builder import build_context_packet_md
from .diagnosis_normalizer import normalize
from .leakage_guard import check_manual_diagnosis, check_runtime_signals
from .loader import load_case_bundle, load_manual_diagnosis, load_taxonomy
from .retrieval_executor import execute_plan
from .schemas import (
    ConDiagLeakageError,
    ConDiagSchemaError,
    ConDiagTaxonomyError,
    ManualDiagnosis,
    NormalizedDiagnosis,
    PathologyTaxonomy,
    RuntimeSignals,
)


def run(
    instance_id: str,
    root: Path,
    repo_path: Path,
    out_dir: Path,
) -> dict:
    """Run Manual-ConDiag Retrieval for one case.

    Returns the retrieval_report dict (also written to retrieval_report.json).
    Raises on hard errors; surfaces soft issues in the report.
    """
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

    # 2. Load case bundle + manual diagnosis
    case_dir = root / "case_bundles" / instance_id
    rs, paths = load_case_bundle(case_dir)
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

    # 5. Build index
    idx = ri.build_index(Path(resolution.repo_path))
    ri.write_summary(out_dir, idx)
    _stage("build_index", True, idx.index_summary)

    # 6. Normalize diagnosis
    nd = normalize(md, taxonomy)
    _stage("normalize_diagnosis", True, {
        "pathology": nd.pathology,
        "action_family": nd.action_family,
        "5r_action": nd.primary_5r_action,
    })

    # 7. Execute retrieval plan
    action_results = execute_plan(md.retrieval_plan, idx, rs, md)
    _write_executed_actions(out_dir, action_results)
    _write_retrieved_candidates(out_dir, action_results)
    n_candidates = sum(len(r.candidates) for r in action_results)
    n_done = sum(1 for r in action_results if r.status == "done")
    n_skipped = sum(1 for r in action_results if r.status == "skipped")
    n_no_cand = sum(1 for r in action_results if r.status == "no_candidates")
    _stage("execute_plan", True, {
        "action_count": len(action_results),
        "candidates_total": n_candidates,
        "done": n_done, "skipped": n_skipped, "no_candidates": n_no_cand,
    })

    # 8. Select evidence
    selected = select_evidence(
        action_results=action_results,
        retry_intent=nd.retry_intent,
        instance_id=instance_id,
    )
    _write_selected_evidence(out_dir, selected)
    _stage("select_evidence", True, selected.get("selection_summary", {}))

    # 9. Build context packet
    packet_md = build_context_packet_md(Path(resolution.repo_path), nd, md, rs, selected)
    (out_dir / "context_packet.md").write_text(packet_md, encoding="utf-8")
    _stage("build_context_packet", True, {
        "bytes": len(packet_md.encode("utf-8")),
        "lines": packet_md.count("\n") + 1,
    })

    # 10. Guarantees
    report["guarantees"]["did_not_read_gold_check"] = True   # enforced by leakage_guard
    report["guarantees"]["did_not_read_official_eval"] = True  # we never read it

    report["ok"] = True
    _write_report(out_dir, report, started_at)
    return report


# ===== writers =====

def _write_executed_actions(out_dir: Path, results) -> None:
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
    (out_dir / "executed_actions.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_retrieved_candidates(out_dir: Path, results) -> None:
    """One JSON object per line for streaming consumption."""
    p = out_dir / "retrieved_candidates.jsonl"
    with p.open("w", encoding="utf-8") as f:
        for r in results:
            for c in r.candidates:
                d = c.to_dict()
                d["action_status"] = r.status
                f.write(json.dumps(d, ensure_ascii=False) + "\n")


def _write_selected_evidence(out_dir: Path, selected: dict) -> None:
    (out_dir / "selected_evidence.json").write_text(
        json.dumps(selected, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _write_report(out_dir: Path, report: dict, started_at: float) -> None:
    ended_at = time.time()
    report["ended_at_unix"] = ended_at
    report["duration_seconds"] = round(ended_at - started_at, 3)
    (out_dir / "retrieval_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
