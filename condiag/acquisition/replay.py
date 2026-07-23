"""Offline Shadow Replay — re-run P1-3C + P1-3D without model or harness.

Usage:
  python -m condiag.acquisition.replay \\
    --bundle /path/to/failure_feature_bundle.json \\
    --repo-root /path/to/repo \\
    --output /path/to/replay_output \\
    --run-id astropy-13398-replay

Exits with:
  0 — success (even when zero actionable contracts or zero hits)
  2 — invalid input or bundle
  3 — pipeline exception
  4 — invariant violation (repo modified, out-of-bounds, budget exceeded)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("condiag.acquisition.replay")


# ── ReplaySummary ───────────────────────────────────────────────────


@dataclass
class ReplaySummary:
    run_id: str = ""
    instance_id: str = ""
    bundle_sha256: str = ""
    repo_head_sha: str = ""
    repo_modified: bool = False

    n_clusters: int = 0
    n_hypotheses: int = 0
    n_actionable_contracts: int = 0
    n_router_actions: int = 0
    n_router_found: int = 0
    n_router_not_found: int = 0
    n_router_unsupported: int = 0
    n_router_invalid: int = 0
    n_router_error: int = 0
    n_hits: int = 0

    total_actionable_budget: int = 0
    budget_violations: list[str] = field(default_factory=list)
    out_of_bounds_files: list[str] = field(default_factory=list)

    gold_accessed: bool = False
    repo_modified_by_replay: bool = False

    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "instance_id": self.instance_id,
            "bundle_sha256": self.bundle_sha256,
            "repo_head_sha": self.repo_head_sha,
            "repo_modified": self.repo_modified,
            "n_clusters": self.n_clusters,
            "n_hypotheses": self.n_hypotheses,
            "n_actionable_contracts": self.n_actionable_contracts,
            "n_router_actions": self.n_router_actions,
            "n_router_found": self.n_router_found,
            "n_router_not_found": self.n_router_not_found,
            "n_router_unsupported": self.n_router_unsupported,
            "n_router_invalid": self.n_router_invalid,
            "n_router_error": self.n_router_error,
            "n_hits": self.n_hits,
            "total_actionable_budget": self.total_actionable_budget,
            "budget_violations": list(self.budget_violations),
            "out_of_bounds_files": list(self.out_of_bounds_files),
            "gold_accessed": self.gold_accessed,
            "repo_modified_by_replay": self.repo_modified_by_replay,
            "errors": list(self.errors),
        }


# ── Helpers ─────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _git_head(repo_root: Path) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
            cwd=repo_root,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _git_is_clean(repo_root: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
            cwd=repo_root,
        )
        return r.returncode == 0 and not r.stdout.strip()
    except Exception:
        return False


# ── Pipeline ────────────────────────────────────────────────────────


def run_replay(
    *,
    bundle_path: Path,
    repo_root: Path,
    output_dir: Path,
    run_id: str,
    max_total_actions: int = 3,
    max_total_budget: int = 8,
    max_files_examined: int = 200,
) -> ReplaySummary:
    """Full offline Shadow pipeline.

    Returns ReplaySummary and writes Shadow artifacts to output_dir/.
    Raises on invariant violations.
    """
    # ── Input validation ──
    if not bundle_path.exists():
        raise FileNotFoundError(f"bundle not found: {bundle_path}")
    if not repo_root.is_dir():
        raise NotADirectoryError(f"repo_root not a directory: {repo_root}")

    # ── Capture pre-replay repo state ──
    bundle_raw = bundle_path.read_bytes()
    bundle_sha = hashlib.sha256(bundle_raw).hexdigest()[:16]
    repo_head = _git_head(repo_root)
    repo_was_clean = _git_is_clean(repo_root)

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── Load bundle ──
    from condiag.diagnosis.signals.schema import RuntimeFailureFeatureBundle
    bundle = RuntimeFailureFeatureBundle.model_validate_json(bundle_raw)

    # ── P1-3A/B: Cluster → Diagnose ──
    from condiag.diagnosis.failure_event import reasoner_v2_cluster
    from condiag.diagnosis.alignment import reasoner_v2_diagnose

    clusters = reasoner_v2_cluster(bundle)
    diagnoses = reasoner_v2_diagnose(
        clusters, bundle.patch, bundle.trajectory,
    )

    # ── P1-3C: Hypothesis → Evidence → Contract ──
    from condiag.diagnosis.hypothesis import from_subtyped_diagnosis
    from condiag.diagnosis.search_contract import (
        PlanBudget,
        build_evidence_ledger,
        build_search_plan,
        write_shadow_artifacts,
    )

    hypotheses = [
        from_subtyped_diagnosis(d, c.cluster_id, c.test_names)
        for c, d in zip(clusters, diagnoses)
    ]

    ledger = build_evidence_ledger(clusters, diagnoses, bundle=bundle)

    plan_budget = PlanBudget(
        max_total_actions=max_total_actions,
        max_total_budget=max_total_budget,
    )
    contracts = build_search_plan(hypotheses, budget=plan_budget, ledger=ledger)

    # Write P1-3C Shadow artifacts
    write_shadow_artifacts(
        output_dir / "p1_3c_shadow",
        contracts=contracts,
        hypotheses=hypotheses,
        ledger=ledger,
        validation_report={
            "run_id": run_id,
            "instance_id": bundle.instance.instance_id if bundle.instance else "",
            "schema_version": "1",
        },
    )

    # ── P1-3D: Router ──
    from condiag.acquisition.router import AcquisitionRouter
    from condiag.acquisition.artifact_writer import (
        write_acquisition_artifacts,
    )

    # Collect viewed files and failed test names
    viewed = list(getattr(bundle.trajectory, "viewed_files", []) or [])
    failed_names: list[str] = []
    for c in clusters:
        failed_names.extend(c.test_names)

    router = AcquisitionRouter(
        repo_root,
        r1_viewed_files=viewed,
        failed_test_names=failed_names,
    )

    results = []
    for contract in contracts:
        if contract.status.value != "ACTIONABLE":
            continue
        results.extend(router.dispatch_contract(contract))

    # Write P1-3D Shadow artifacts
    write_acquisition_artifacts(
        output_dir / "p1_3d_shadow",
        results,
        repo_root,
        run_id=run_id,
    )

    # ── Verify repo was not modified ──
    repo_modified_now = not _git_is_clean(repo_root)
    if repo_was_clean and repo_modified_now:
        raise RuntimeError(
            "Replay modified the repo! This is a bug."
        )

    # ── Build summary ──
    from condiag.acquisition.schema import AcquisitionStatus

    n_found = sum(1 for r in results if r.status == AcquisitionStatus.FOUND)
    n_not_found = sum(1 for r in results if r.status == AcquisitionStatus.NOT_FOUND)
    n_unsupported = sum(1 for r in results if r.status == AcquisitionStatus.UNSUPPORTED)
    n_invalid = sum(1 for r in results if r.status == AcquisitionStatus.INVALID_TARGET)
    n_error = sum(1 for r in results if r.status == AcquisitionStatus.ERROR)
    n_hits = sum(len(r.hits) for r in results)
    total_budget = sum(r.budget_limit for r in results)

    # Post-replay provenance check
    from condiag.acquisition.artifact_writer import validate_results
    val_rep = validate_results(results, repo_root)

    _summary = ReplaySummary(
        run_id=run_id,
        instance_id=bundle.instance.instance_id if bundle.instance else "",
        bundle_sha256=bundle_sha,
        repo_head_sha=repo_head,
        repo_modified=not repo_was_clean,
        n_clusters=len(clusters),
        n_hypotheses=len(hypotheses),
        n_actionable_contracts=sum(
            1 for c in contracts if c.status.value == "ACTIONABLE"
        ),
        n_router_actions=len(results),
        n_router_found=n_found,
        n_router_not_found=n_not_found,
        n_router_unsupported=n_unsupported,
        n_router_invalid=n_invalid,
        n_router_error=n_error,
        n_hits=n_hits,
        total_actionable_budget=total_budget,
        budget_violations=val_rep.budget_violations,
        out_of_bounds_files=val_rep.out_of_bounds_files,
        gold_accessed=False,
        repo_modified_by_replay=repo_modified_now,
    )

    # Write replay_manifest.json from summary
    _manifest = _summary.to_dict()
    _manifest["bundle_path"] = str(bundle_path.resolve())
    _manifest["repo_root"] = str(repo_root.resolve())
    _manifest["max_total_actions"] = max_total_actions
    _manifest["max_total_budget"] = max_total_budget
    _manifest["max_files_examined"] = max_files_examined
    _manifest_path = output_dir / "replay_manifest.json"
    _manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _manifest_path.write_text(json.dumps(_manifest, indent=2))

    return _summary


# ── CLI ────────────────────────────────────────────────────────────


def main() -> None:
    p = argparse.ArgumentParser(
        description="Offline Shadow Replay: bundle → cluster → diagnose → contract → router",
    )
    p.add_argument("--bundle", required=True, type=Path, help="Path to failure_feature_bundle.json")
    p.add_argument("--repo-root", required=True, type=Path, help="Path to repository checkout")
    p.add_argument("--output", required=True, type=Path, help="Output directory for artifacts")
    p.add_argument("--run-id", default="replay", help="Descriptive run label")
    p.add_argument("--max-total-actions", type=int, default=3, help="Plan-level cap (default 3)")
    p.add_argument("--max-total-budget", type=int, default=8, help="Plan-level budget cap (default 8)")
    p.add_argument("--max-files-examined", type=int, default=200, help="Per-action file scan limit (default 200)")
    args = p.parse_args()

    exit_code = 0
    try:
        summary = run_replay(
            bundle_path=args.bundle,
            repo_root=args.repo_root,
            output_dir=args.output,
            run_id=args.run_id,
            max_total_actions=args.max_total_actions,
            max_total_budget=args.max_total_budget,
            max_files_examined=args.max_files_examined,
        )
    except FileNotFoundError as e:
        logger.error("Input error: %s", e)
        sys.exit(2)
    except NotADirectoryError as e:
        logger.error("Input error: %s", e)
        sys.exit(2)
    except RuntimeError as e:
        if "modified the repo" in str(e):
            logger.error("Invariant violation: %s", e)
            sys.exit(4)
        logger.exception("Runtime error: %s", e)
        sys.exit(3)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        logger.exception("Pipeline error: %s", e)
        sys.exit(3)

    # Check invariants from summary
    if summary.out_of_bounds_files:
        logger.error(
            "Invariant violation: %d out-of-bounds files (%s)",
            len(summary.out_of_bounds_files),
            summary.out_of_bounds_files[:3],
        )
        exit_code = 4
    if summary.budget_violations:
        logger.error(
            "Invariant violation: %d budget violations (%s)",
            len(summary.budget_violations),
            summary.budget_violations[:3],
        )
        exit_code = 4
    if summary.repo_modified_by_replay:
        logger.error("Invariant violation: repo was modified")
        exit_code = 4

    logger.info("Replay manifest → %s/replay_manifest.json", args.output)
    logger.info(
        "Summary: %d clusters, %d hypotheses, %d actionable contracts, "
        "%d actions, %d hits, exit=%d",
        summary.n_clusters, summary.n_hypotheses,
        summary.n_actionable_contracts,
        summary.n_router_actions, summary.n_hits, exit_code,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
