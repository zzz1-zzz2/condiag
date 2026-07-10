#!/usr/bin/env python3
"""Run FailureWitness source inventory + witness building for canonical first-failed pool.

Usage:
    python3 experiments/run_failure_witness_for_pool.py [--dry-run] [--witness-dir PATH]

Steps:
    1. Read canonical_base_eval_matrix.csv
    2. Filter: base_resolved=False, eval_status=EVALUATED, conflict=""
    3. Task 3A: Build failure_witness_source_inventory.csv
    4. Task 3B: Build failure_witness.json for each instance

Output:
    - /mnt/d/condiag-artifacts/condiag/v0/failure_witness_source_inventory.csv
    - /mnt/d/condiag-artifacts/condiag/v0/failure_witness/<instance_id>/failure_witness.json
"""

import csv
import json
import os
import sys
from pathlib import Path

# Ensure condiag package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from experiments.failure_witness_builder import (
    build_failure_witness,
    build_source_inventory,
    resolve_artifact_path,
)


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEFAULT_ARTIFACT_BASE = Path("/mnt/d/condiag-artifacts/condiag/v0")
DEFAULT_CANONICAL_MATRIX = DEFAULT_ARTIFACT_BASE / "canonical_base_eval_matrix.csv"
DEFAULT_INVENTORY_CSV = DEFAULT_ARTIFACT_BASE / "failure_witness_source_inventory.csv"
DEFAULT_WITNESS_DIR = DEFAULT_ARTIFACT_BASE / "failure_witness"

CANONICAL_FIRST_FAILED = [
    "django__django-11820",
    "django__django-12125",
    "django__django-13513",
    "django__django-16454",
    "sympy__sympy-20428",
]


# ---------------------------------------------------------------------------
# Canonical matrix reader
# ---------------------------------------------------------------------------

def read_canonical_matrix(csv_path: Path) -> list[dict]:
    """Read canonical_base_eval_matrix.csv, return list of row dicts."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows


def filter_first_failed(rows: list[dict]) -> list[dict]:
    """Filter to first-failed pool.

    Criteria:
    - base_resolved == "False"
    - eval_status == "EVALUATED"
    - conflict == "" (empty)
    """
    filtered = []
    for r in rows:
        if (
            r.get("base_resolved", "").strip() == "False"
            and r.get("eval_status", "").strip() == "EVALUATED"
            and r.get("conflict", "").strip() == ""
        ):
            filtered.append(r)
    return filtered


def resolve_run_dir(row: dict) -> Path:
    """Resolve attempt_1 directory from canonical matrix row."""
    raw = row.get("attempt_1_dir", "")
    if raw:
        return resolve_artifact_path(raw)
    # Fallback: construct from traj_path
    traj = row.get("traj_path", "")
    if traj:
        return resolve_artifact_path(traj).parent
    return Path("")


# ---------------------------------------------------------------------------
# Inventory CSV writer
# ---------------------------------------------------------------------------

def write_inventory_csv(inventory: list[dict], csv_path: Path):
    """Write failure_witness_source_inventory.csv."""
    fieldnames = [
        "instance_id",
        "has_raw_validation_output",
        "raw_output_path",
        "source_type",
        "missing_reason",
    ]
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in inventory:
            writer.writerow(record)
    print(f"  Inventory written to {csv_path}")


# ---------------------------------------------------------------------------
# Witness writer
# ---------------------------------------------------------------------------

def write_witness_json(witness, instance_id: str, witness_dir: Path):
    """Write failure_witness.json for a single instance."""
    out_dir = witness_dir / instance_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "failure_witness.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(_dataclass_to_dict(witness), f, indent=2, ensure_ascii=False)
    return out_path


def _dataclass_to_dict(obj):
    """Convert dataclass to dict, handling nested dataclasses."""
    if hasattr(obj, "__dataclass_fields__"):
        return {f.name: _dataclass_to_dict(getattr(obj, f.name)) for f in obj.__dataclass_fields__.values()}
    if isinstance(obj, list):
        return [_dataclass_to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

def print_summary(summary_rows: list[dict]):
    """Print summary table."""
    print()
    print(f"{'instance_id':30s} {'raw_out':8s} {'has_wit':8s} {'failure_type':20s} {'mode':40s} {'source':28s} {'source_type':24s} {'missing_reason':40s}")
    print("-" * 200)
    for r in summary_rows:
        print(
            f"{r['instance_id']:30s} "
            f"{r['has_raw_validation_output']:8s} "
            f"{r['has_failure_witness']:8s} "
            f"{r['failure_type']:20s} "
            f"{r['mode']:40s} "
            f"{r['source']:28s} "
            f"{r['source_type']:24s} "
            f"{r['missing_reason']:40s}"
        )
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    witness_dir_arg = None

    if "--witness-dir" in sys.argv:
        idx = sys.argv.index("--witness-dir")
        if idx + 1 < len(sys.argv):
            witness_dir_arg = Path(sys.argv[idx + 1])

    artifact_base = DEFAULT_ARTIFACT_BASE
    canonical_csv = DEFAULT_CANONICAL_MATRIX
    inventory_csv = DEFAULT_INVENTORY_CSV
    witness_dir = witness_dir_arg or DEFAULT_WITNESS_DIR

    if not canonical_csv.exists():
        # Fall back to Windows path for development
        alt = Path("D:/condiag-artifacts/condiag/v0/canonical_base_eval_matrix.csv")
        if alt.exists():
            canonical_csv = alt
            artifact_base = Path("D:/condiag-artifacts/condiag/v0")
            inventory_csv = artifact_base / "failure_witness_source_inventory.csv"
            witness_dir = artifact_base / "failure_witness"
        else:
            print(f"ERROR: canonical matrix not found at {canonical_csv}")
            sys.exit(1)

    print("=" * 60)
    print("Task 3 — FailureWitness Builder")
    print("=" * 60)
    print(f"  Canonical matrix: {canonical_csv}")
    print(f"  Artifact base:    {artifact_base}")
    print(f"  Output inventory: {inventory_csv}")
    print(f"  Output witness:   {witness_dir}/<instance_id>/failure_witness.json")
    if dry_run:
        print("  Mode: DRY RUN (no files written)")
    print()

    # ------------------------------------------------------------------
    # Step 1: Read canonical matrix
    # ------------------------------------------------------------------
    print("[1/4] Reading canonical matrix...")
    rows = read_canonical_matrix(canonical_csv)
    print(f"  Total rows: {len(rows)}")

    # ------------------------------------------------------------------
    # Step 2: Filter first-failed pool
    # ------------------------------------------------------------------
    print("[2/4] Filtering first-failed pool...")
    first_failed = filter_first_failed(rows)
    print(f"  First-failed instances: {len(first_failed)}")
    for ff in first_failed:
        print(f"    {ff['instance_id']}  (failure_class={ff.get('failure_class', '')})")

    # Verify against canonical list
    first_failed_ids = {r["instance_id"] for r in first_failed}
    missing_from_canonical = set(CANONICAL_FIRST_FAILED) - first_failed_ids
    extra = first_failed_ids - set(CANONICAL_FIRST_FAILED)
    if missing_from_canonical:
        print(f"  WARNING: expected instances not in filter: {missing_from_canonical}")
    if extra:
        print(f"  NOTE: additional instances in filter: {extra}")

    # ------------------------------------------------------------------
    # Step 3 (Task 3A): Source inventory
    # ------------------------------------------------------------------
    print("[3/4] Building source inventory (Task 3A)...")
    instance_ids = [r["instance_id"] for r in first_failed]
    inventory = build_source_inventory(canonical_csv.parent, artifact_base, instance_ids)

    if not dry_run:
        write_inventory_csv(inventory, inventory_csv)
    else:
        print("  (dry-run)")

    # Print inventory
    for rec in inventory:
        status = "HAS_RAW" if rec["has_raw_validation_output"] else "MISSING"
        print(f"  {rec['instance_id']:30s} {status:10s}  {rec['source_type']:24s}  {rec['missing_reason']}")

    # ------------------------------------------------------------------
    # Step 4 (Task 3B): Build witness JSON
    # ------------------------------------------------------------------
    print("[4/4] Building FailureWitness (Task 3B)...")
    summary_rows = []

    for ff in first_failed:
        instance_id = ff["instance_id"]
        run_dir = resolve_run_dir(ff)

        # Check if inventory says we have raw output
        inv_record = next((i for i in inventory if i["instance_id"] == instance_id), None)
        has_raw = inv_record and inv_record["has_raw_validation_output"]

        if has_raw:
            # Task 3B: parse from post-validation raw output
            eval_log_path = inv_record["raw_output_path"]
            witness = build_failure_witness(
                instance_id=instance_id,
                eval_log_path=eval_log_path,
            )
        else:
            # No raw post-validation output: write diagnostic no-witness record
            witness = build_failure_witness(
                instance_id=instance_id,
            )

        if not dry_run:
            out_path = write_witness_json(witness, instance_id, witness_dir)
            print(f"  {instance_id:30s} → {out_path}")
        else:
            print(f"  {instance_id:30s}  (dry-run, would write to {witness_dir}/{instance_id}/failure_witness.json)")

        summary_rows.append({
            "instance_id": instance_id,
            "has_raw_validation_output": "true" if has_raw else "false",
            "has_failure_witness": str(witness.has_failure_witness),
            "failure_type": witness.failure_type or "(empty)",
            "mode": witness.mode,
            "source": witness.source,
            "source_type": witness.source_type,
            "missing_reason": witness.missing_reason or "(empty)",
        })

    # ------------------------------------------------------------------
    # Summary table
    # ------------------------------------------------------------------
    print_summary(summary_rows)
    print("Task 3 complete. Stop — do not enter Task 4 or Task 5.")


if __name__ == "__main__":
    main()
