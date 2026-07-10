"""Canonicalize base attempt_1 experiment state.

Scans all discovered base_miniswe attempt_1 artifacts and official eval results,
merges them into a single canonical CSV with conflict resolution.
This is the Task 0 hard gate: all downstream scripts MUST read ONLY this file.

Usage:
    python3 -m experiments.canonicalize_base_eval --dry-run
    python3 -m experiments.canonicalize_base_eval --out /path/to/matrix.csv --summary /path/to/summary.md
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

METHOD_VERSION = "v0"
PLAN_VERSION = "plan_v1.0_post_validation"

CSV_FIELDS = [
    "instance_id",
    "batch_id",
    "traj_path",
    "attempt_1_dir",
    "patch_path",
    "patch_exists",
    "patch_chars",
    "runtime_signals_path",
    "base_resolved",
    "eval_status",
    "eval_report_path",
    "source_of_truth",
    "f2p_passed",
    "f2p_total",
    "p2p_regressed",
    "p2p_total",
    "patch_apply_ok",
    "test_runs_count",
    "test_failures_count",
    "submitted_without_tests",
    "failure_class",
    "conflict",
    "method_version",
    "plan_version",
]


def resolve_artifact_dir() -> Path:
    """Resolve the artifact root directory."""
    env = os.environ.get("CONDIAG_ARTIFACTS")
    if env and os.path.isdir(env):
        return Path(env)
    candidates = [
        Path("/mnt/d/condiag-artifacts/condiag"),
        Path("/d/condiag-artifacts/condiag"),
    ]
    for c in candidates:
        if c.is_dir():
            return c
    raise RuntimeError(
        f"Cannot find artifact directory. Set CONDIAG_ARTIFACTS env var or "
        f"ensure one of {[str(c) for c in candidates]} exists."
    )


def discover_base_instances(artifact_dir: Path) -> list:
    """Scan all base_miniswe attempt_1 directories."""
    instances = []
    search_roots = [
        artifact_dir / "v0" / "d4_9_batch2_17x4" / "runs" / "miniswe" / "base_miniswe",
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for iid in sorted(os.listdir(root)):
            a1 = root / iid / "attempt_1"
            if a1.is_dir():
                instances.append((iid, a1, root.name))
    # Deduplicate by instance_id (keep last found)
    seen = {}
    for iid, a1, source in instances:
        seen[iid] = (a1, source)
    return [(iid, a1, source) for iid, (a1, source) in sorted(seen.items())]


def read_runtime_signals(a1_dir: Path) -> dict:
    """Read runtime_signals.json from attempt_1 directory."""
    rs_path = a1_dir / "runtime_signals.json"
    if not rs_path.is_file():
        return {"test_runs": [], "test_failures": [], "submitted_without_tests": False}
    with open(rs_path) as f:
        rs = json.load(f)
    test_runs = rs.get("test_runs", [])
    if isinstance(test_runs, list):
        test_runs_count = len(test_runs)
    else:
        test_runs_count = 0
    test_failures = rs.get("test_failures", [])
    if isinstance(test_failures, list):
        test_failures_count = len(test_failures)
    else:
        test_failures_count = 0
    return {
        "test_runs_count": test_runs_count,
        "test_failures_count": test_failures_count,
        "submitted_without_tests": bool(rs.get("submitted_without_tests", False)),
    }


def read_manifest(artifact_dir: Path) -> dict:
    """Read manifest.csv for batch_id mapping."""
    manifest_paths = [
        artifact_dir / "v0" / "d4_9_batch2_17x4" / "manifest.csv",
    ]
    for mp in manifest_paths:
        if mp.is_file():
            mapping = {}
            with open(mp) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    iid = row.get("instance_id", "")
                    mapping[iid] = row.get("source_batch", "unknown")
            return mapping
    return {}


def scan_eval_sources(artifact_dir: Path) -> list:
    """Find all official eval result files."""
    sources = []
    # Priority 1: repair_smoke_eval_matrix.json (official SWE-bench harness)
    p1 = artifact_dir / "v0" / "d4_9_batch2_17x4" / "repair_smoke_eval_matrix.json"
    if p1.is_file():
        sources.append(("repair_smoke_eval_matrix.json", p1, 1))

    # Priority 2: sympy19954_eval.json
    p2 = artifact_dir / "v0" / "d4_9_batch2_17x4" / "sympy19954_eval.json"
    if p2.is_file():
        sources.append(("sympy19954_eval.json", p2, 2))

    # Priority 3: eval_anomaly_inspection.json
    p3 = artifact_dir / "v0" / "d4_9_batch2_17x4" / "eval_anomaly_inspection.json"
    if p3.is_file():
        sources.append(("eval_anomaly_inspection.json", p3, 3))

    # Priority 4: case_bundles official_eval.json
    cb_dir = artifact_dir / "v0" / "case_bundles"
    if cb_dir.is_dir():
        for sub in sorted(os.listdir(cb_dir)):
            oe = cb_dir / sub / "official_eval.json"
            if oe.is_file():
                sources.append((f"case_bundles/{sub}/official_eval.json", oe, 4))

    return sources


def load_eval_records(filepath: Path) -> list:
    """Load eval records from a JSON file."""
    with open(filepath) as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    return [data]


def extract_base_eval(eval_records: list, instance_id: str) -> dict:
    """Extract base_miniswe eval data for a specific instance."""
    results = []
    for r in eval_records:
        baseline = r.get("baseline", "")
        if baseline != "base_miniswe":
            continue
        iid = r.get("instance_id", "")
        if iid != instance_id:
            continue
        results.append(r)
    return results


def merge_eval_status(all_records: list, instance_id: str) -> dict:
    """Merge multiple eval results for one instance. Returns canonical status."""
    if not all_records:
        return {
            "base_resolved": "NOT_EVALUATED",
            "eval_status": "NOT_EVALUATED",
            "eval_report_path": "",
            "source_of_truth": "",
            "f2p_passed": "",
            "f2p_total": "",
            "p2p_regressed": "",
            "p2p_total": "",
            "patch_apply_ok": "",
            "conflict": "",
        }

    if len(all_records) == 1:
        r = all_records[0]
        resolved = r.get("resolved", None)
        if resolved is None:
            base_resolved = "NOT_EVALUATED"
            eval_status = "NOT_EVALUATED"
        elif resolved is True:
            base_resolved = "True"
            eval_status = "EVALUATED"
        elif resolved is False:
            eval_error = r.get("eval_error", "")
            if eval_error and "patch_apply" in str(eval_error):
                eval_status = "PATCH_APPLY_FAILED"
            elif eval_error:
                eval_status = "ENV_ERROR"
            else:
                eval_status = "EVALUATED"
            base_resolved = "False"

        return {
            "base_resolved": base_resolved,
            "eval_status": eval_status,
            "eval_report_path": "",
            "source_of_truth": "",
            "f2p_passed": str(r.get("fail_to_pass_passed", "")),
            "f2p_total": str(r.get("fail_to_pass_total", "")),
            "p2p_regressed": str(r.get("pass_to_pass_regressed", "")),
            "p2p_total": str(r.get("pass_to_pass_total", "")),
            "patch_apply_ok": str(r.get("patch_apply_ok", "")),
            "conflict": "",
        }

    # Multiple records: merge with conflict detection
    resolved_values = set()
    for r in all_records:
        resolved = r.get("resolved", None)
        if resolved is True:
            resolved_values.add("True")
        elif resolved is False:
            resolved_values.add("False")
        else:
            resolved_values.add("NOT_EVALUATED")

    conflict_parts = []
    for r in all_records:
        src = r.get("_source_file", "unknown")
        conflict_parts.append(f"{src}:resolved={r.get('resolved')}")

    if len(resolved_values) > 1:
        conflict = "; ".join(conflict_parts)
        # Prefer False over True over NOT_EVALUATED (more conservative)
        if "False" in resolved_values:
            base_resolved = "False"
        elif "True" in resolved_values:
            base_resolved = "True"
        else:
            base_resolved = "NOT_EVALUATED"
        eval_status = "CONFLICT"
    else:
        base_resolved = resolved_values.pop() if resolved_values else "NOT_EVALUATED"
        eval_status = "EVALUATED" if base_resolved in ("True", "False") else "NOT_EVALUATED"
        conflict = ""

    # Use the first record's detailed fields
    r0 = all_records[0]
    return {
        "base_resolved": base_resolved,
        "eval_status": eval_status,
        "eval_report_path": "",
        "source_of_truth": conflict if conflict else "",
        "f2p_passed": str(r0.get("fail_to_pass_passed", "")),
        "f2p_total": str(r0.get("fail_to_pass_total", "")),
        "p2p_regressed": str(r0.get("pass_to_pass_regressed", "")),
        "p2p_total": str(r0.get("pass_to_pass_total", "")),
        "patch_apply_ok": str(r0.get("patch_apply_ok", "")),
        "conflict": conflict,
    }


def classify_failure(row: dict) -> str:
    """Classify failure type based on current data."""
    eval_status = row.get("eval_status", "")
    base_resolved = row.get("base_resolved", "")
    test_failures = int(row.get("test_failures_count", 0))
    submitted_without = row.get("submitted_without_tests", "").strip()
    patch_exists = row.get("patch_exists", "").strip()

    if eval_status in ("ENV_ERROR", "PATCH_APPLY_FAILED"):
        return "env-anomaly"
    if eval_status == "CONFLICT":
        return "unknown"
    if base_resolved == "True":
        return "resolved"
    if eval_status == "NOT_EVALUATED":
        return "unknown"
    if base_resolved == "False":
        if test_failures > 0:
            return "visible-failure"
        if submitted_without == "True":
            return "no-runtime-signal"
        if test_failures == 0 and patch_exists == "True":
            return "hidden-failure"
    return "unknown"


def build_matrix(artifact_dir: Path, dry_run: bool = False) -> list:
    """Build the canonical base eval matrix."""
    # Step 1: Discover instances
    base_instances = discover_base_instances(artifact_dir)
    # Step 2: Read manifest for batch_id
    batch_map = read_manifest(artifact_dir)
    # Step 3: Scan eval sources
    eval_sources = scan_eval_sources(artifact_dir)

    # Load all eval data
    all_eval_data = {}
    for source_name, source_path, priority in eval_sources:
        records = load_eval_records(source_path)
        for r in records:
            r["_source_file"] = str(source_path)
            r["_source_name"] = source_name
            r["_priority"] = priority
            iid = r.get("instance_id", "")
            if iid not in all_eval_data:
                all_eval_data[iid] = []
            all_eval_data[iid].append(r)

    # Sort by priority
    for iid in all_eval_data:
        all_eval_data[iid].sort(key=lambda x: x.get("_priority", 99))

    rows = []
    for iid, a1_dir, source in base_instances:
        # Instance-level data
        rs = read_runtime_signals(a1_dir)
        patch_path = a1_dir / "patch.diff"
        patch_exists = patch_path.is_file()
        patch_chars = os.path.getsize(patch_path) if patch_exists else 0

        # Find traj_path
        traj_candidates = [
            artifact_dir / "runs" / f"pilot50_{source}" / "miniswe" / "Verified" / iid / f"{iid}.traj.json",
            a1_dir / "raw_trajectory.json",
        ]
        traj_path = ""
        for tc in traj_candidates:
            if tc.is_file():
                traj_path = str(tc)
                break

        batch_id = batch_map.get(iid, "unknown")

        # Merge eval status
        eval_records = all_eval_data.get(iid, [])
        eval_data = merge_eval_status(eval_records, iid)

        row = {
            "instance_id": iid,
            "batch_id": batch_id,
            "traj_path": traj_path,
            "attempt_1_dir": str(a1_dir),
            "patch_path": str(patch_path) if patch_exists else "",
            "patch_exists": "True" if patch_exists else "False",
            "patch_chars": str(patch_chars),
            "runtime_signals_path": str(a1_dir / "runtime_signals.json"),
            **eval_data,
            "test_runs_count": str(rs["test_runs_count"]),
            "test_failures_count": str(rs["test_failures_count"]),
            "submitted_without_tests": str(rs["submitted_without_tests"]),
            "failure_class": "",
            "method_version": METHOD_VERSION,
            "plan_version": PLAN_VERSION,
        }
        row["failure_class"] = classify_failure(row)
        rows.append(row)

    return rows


def write_csv(rows: list, out_path: Path):
    """Write canonical matrix CSV."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary(rows: list, summary_path: Path, source_files: list):
    """Write canonical eval summary markdown."""
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(rows)
    evaluated = [r for r in rows if r["eval_status"] == "EVALUATED"]
    not_evaluated = [r for r in rows if r["eval_status"] == "NOT_EVALUATED"]
    env_error = [r for r in rows if r["eval_status"] in ("ENV_ERROR", "PATCH_APPLY_FAILED")]
    conflict = [r for r in rows if r["eval_status"] == "CONFLICT"]
    resolved = [r for r in rows if r["base_resolved"] == "True"]
    unresolved = [r for r in rows if r["base_resolved"] == "False"]
    first_failed = unresolved

    lines = [
        "# Canonical Base Eval Summary (Task 0)",
        "",
        f"**Generated**: by experiments/canonicalize_base_eval.py",
        f"**method_version**: {METHOD_VERSION}",
        f"**plan_version**: {PLAN_VERSION}",
        "",
        "## Overview",
        "",
        f"| Metric | Count |",
        f"|---|---|",
        f"| Discovered base attempt_1 | {total} |",
        f"| Evaluated | {len(evaluated)} |",
        f"| Not evaluated | {len(not_evaluated)} |",
        f"| Env/patch-apply error | {len(env_error)} |",
        f"| Resolved | {len(resolved)} |",
        f"| Unresolved (first-failed pool) | {len(unresolved)} |",
        f"| Conflicts | {len(conflict)} |",
        "",
        "## First-Failed Pool",
        "",
        "| instance_id | batch_id | patch_exists | failure_class | test_runs | test_failures | submitted_without |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in first_failed:
        lines.append(
            f"| {r['instance_id']} | {r['batch_id']} | {r['patch_exists']} "
            f"| {r['failure_class']} | {r['test_runs_count']} | {r['test_failures_count']} "
            f"| {r['submitted_without_tests']} |"
        )
    lines.append("")

    if not_evaluated:
        lines.append("## NOT EVALUATED Instances")
        lines.append("")
        lines.append("These instances lack official base_miniswe eval results.")
        lines.append("They need Docker-based official eval before Task 3.")
        lines.append("")
        lines.append("| instance_id | patch_path | patch_chars |")
        lines.append("|---|---|---|")
        for r in not_evaluated:
            pp = r["patch_path"] if r["patch_path"] else "N/A"
            lines.append(f"| {r['instance_id']} | {pp} | {r['patch_chars']} |")
        lines.append("")

    if conflict:
        lines.append("## Conflicts")
        for r in conflict:
            lines.append(f"- {r['instance_id']}: {r['conflict']}")
        lines.append("")

    lines.extend([
        "## Source Files Used", "",
    ])
    for sf in source_files:
        lines.append(f"- {sf}")
    lines.append("")

    lines.extend([
        "## Next Recommended Action", "",
        f"- {len(not_evaluated)} instances need official eval before Task 3.",
        "- After eval, re-run this script to update canonical matrix.",
        "- Once canonical matrix is complete, proceed to Task 1.",
        "",
    ])

    with open(summary_path, "w") as f:
        f.write("\n".join(lines))


def main():
    parser = argparse.ArgumentParser(description="Canonicalize base attempt_1 eval state")
    parser.add_argument("--out", help="Output CSV path")
    parser.add_argument("--summary", help="Output summary markdown path")
    parser.add_argument("--dry-run", action="store_true", help="Print matrix without writing")
    parser.add_argument("--artifact-dir", help="Override artifact root directory")
    args = parser.parse_args()

    artifact_dir = Path(args.artifact_dir) if args.artifact_dir else resolve_artifact_dir()

    rows = build_matrix(artifact_dir, dry_run=args.dry_run)

    if args.dry_run:
        print(f"[DRY RUN] Discovered {len(rows)} base instances")
        for r in rows:
            status = r["eval_status"]
            resolved = r["base_resolved"]
            fc = r["failure_class"]
            print(f"  {r['instance_id']:40s}  eval={status:20s}  resolved={resolved:12s}  failure_class={fc}")
        return

    # Determine output paths
    out_path = Path(args.out) if args.out else artifact_dir / "v0" / "canonical_base_eval_matrix.csv"
    summary_path = Path(args.summary) if args.summary else artifact_dir / "v0" / "canonical_base_eval_summary.md"

    source_files = []
    for _, sp, _ in scan_eval_sources(artifact_dir):
        source_files.append(str(sp))

    write_csv(rows, out_path)
    write_summary(rows, summary_path, source_files)

    print(f"Wrote {len(rows)} rows to {out_path}")
    print(f"Wrote summary to {summary_path}")

    evaluated = [r for r in rows if r["eval_status"] == "EVALUATED"]
    not_eval = [r for r in rows if r["eval_status"] == "NOT_EVALUATED"]
    print(f"  Evaluated: {len(evaluated)}")
    print(f"  Not evaluated: {len(not_eval)}")
    print(f"  Resolved: {len([r for r in rows if r['base_resolved'] == 'True'])}")
    print(f"  Unresolved: {len([r for r in rows if r['base_resolved'] == 'False'])}")


if __name__ == "__main__":
    main()
