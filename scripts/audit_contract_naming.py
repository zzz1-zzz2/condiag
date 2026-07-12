#!/usr/bin/env python3
"""Audit and migrate Contract filenames to canonical naming.

Reads each JSON file in `dev10_contracts/`, resolves the canonical
instance ID from the JSON content, and renames the file to match.

Usage:
    # Dry-run (show what would change, no files modified)
    python scripts/audit_contract_naming.py --contracts-dir pool/dev10_contracts

    # Execute migration
    python scripts/audit_contract_naming.py --contracts-dir pool/dev10_contracts --execute

    # Full gate check (verify + test handler loadability)
    python scripts/audit_contract_naming.py --contracts-dir pool/dev10_contracts --gate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
_PROJECT = _HERE.parent
sys.path.insert(0, str(_PROJECT))

from condiag.instance_identity import (
    instance_artifact_filename,
    resolve_canonical_instance_id,
)


def _load_pool(pool_path: Path) -> list[dict[str, Any]]:
    data = json.loads(pool_path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "instances" in data:
        return data["instances"]
    if isinstance(data, list):
        return data
    raise ValueError(f"Unexpected pool format in {pool_path}")


def _resolve_canonical_from_json(contract_data: dict[str, Any]) -> str:
    cid = contract_data.get("instance_id", "")
    if not cid:
        raise ValueError("Contract JSON missing 'instance_id' field")
    return cid


class AuditEntry:
    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path
        self.current_filename = file_path.name
        self.json_instance_id: str = ""
        self.canonical_instance_id: str = ""
        self.expected_filename: str = ""
        self.needs_rename: bool = False
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def ok(self) -> bool:
        return len(self.errors) == 0


def audit_single(file_path: Path) -> AuditEntry:
    entry = AuditEntry(file_path)
    try:
        data = json.loads(file_path.read_text(encoding="utf-8"))
    except Exception as e:
        entry.errors.append(f"Cannot parse JSON: {e}")
        return entry
    try:
        entry.json_instance_id = _resolve_canonical_from_json(data)
    except ValueError as e:
        entry.errors.append(str(e))
        return entry
    entry.canonical_instance_id = entry.json_instance_id
    try:
        safe_name = instance_artifact_filename(entry.canonical_instance_id)
        entry.expected_filename = f"{safe_name}.json"
    except ValueError as e:
        entry.errors.append(f"Cannot compute filename: {e}")
        return entry
    entry.needs_rename = (entry.current_filename != entry.expected_filename)
    return entry


def audit_contracts(contracts_dir: Path) -> list[AuditEntry]:
    files = sorted(contracts_dir.glob("*.json"))
    if not files:
        print(f"WARNING: no .json files found in {contracts_dir}")
        return []
    return [audit_single(fp) for fp in files]


def print_audit_report(entries: list[AuditEntry], pool_records: list[dict[str, Any]] | None = None) -> int:
    total = len(entries)
    ok_count = sum(1 for e in entries if e.ok() and not e.needs_rename)
    rename_count = sum(1 for e in entries if e.needs_rename)
    error_count = sum(1 for e in entries if not e.ok())

    print(f"{'=' * 80}")
    print(f"CONTRACT NAMING AUDIT")
    print(f"{'=' * 80}")
    print(f"  Total contracts: {total}")
    print(f"  Correct:         {ok_count}")
    print(f"  Needs rename:    {rename_count}")
    print(f"  Errors:          {error_count}")
    print()

    for entry in entries:
        if entry.ok() and not entry.needs_rename:
            print(f"  [OK]     {entry.current_filename}")
        elif entry.needs_rename:
            print(f"  [RENAME] {entry.current_filename}")
            print(f"           JSON instance_id:  {entry.json_instance_id}")
            print(f"           Expected filename: {entry.expected_filename}")
        else:
            print(f"  [ERROR]  {entry.current_filename}")
            for err in entry.errors:
                print(f"           ERROR: {err}")

    if pool_records:
        print()
        print(f"{'=' * 80}")
        print(f"POOL RECORD vs CANONICAL ID CONSISTENCY")
        print(f"{'=' * 80}")
        pool_ok = pool_err = 0
        for rec in pool_records:
            iid = rec.get("instance_id", "")
            dname = rec.get("dir_name", iid)
            try:
                canonical = resolve_canonical_instance_id(rec)
            except ValueError as e:
                print(f"  [ERROR] {iid:<55} {e}")
                pool_err += 1
                continue
            if canonical == dname:
                print(f"  [OK]    {iid:<55} -> {canonical}")
                pool_ok += 1
            else:
                print(f"  [ERROR] {iid:<55} resolved to {canonical}, expected {dname}")
                pool_err += 1
        print(f"  Pool records: {pool_ok} ok, {pool_err} errors")

    print()
    return 1 if (rename_count > 0 or error_count > 0) else 0


def migrate_contracts(entries: list[AuditEntry], dry_run: bool = True) -> int:
    to_rename = [e for e in entries if e.needs_rename and e.ok()]
    renamed = 0
    if not to_rename:
        print("No files need renaming.")
        return 0
    print(f"\n{'=' * 80}")
    print(f"{'DRY-RUN' if dry_run else 'EXECUTING'} MIGRATION")
    print(f"{'=' * 80}")
    for entry in to_rename:
        src = entry.file_path
        dst = src.with_name(entry.expected_filename)
        if dst.exists():
            print(f"  SKIP  {src.name} -> {dst.name}  (target exists)")
            continue
        if dry_run:
            print(f"  WOULD RENAME:  {src.name}  ->  {dst.name}")
        else:
            src.rename(dst)
            print(f"  RENAMED:       {src.name}  ->  {dst.name}")
        renamed += 1
    return renamed


def gate_check(entries: list[AuditEntry], pool_records: list[dict[str, Any]]) -> int:
    print(f"\n{'=' * 80}")
    print(f"GATE CHECK")
    print(f"{'=' * 80}")
    gates = []

    all_canonical = set()
    g1_ok = True
    for rec in pool_records:
        try:
            cid = resolve_canonical_instance_id(rec)
            if cid in all_canonical:
                print(f"  [G1 FAIL] Duplicate canonical ID: {cid}")
                g1_ok = False
            all_canonical.add(cid)
        except ValueError as e:
            print(f"  [G1 FAIL] {rec.get('instance_id', '?')}: {e}")
            g1_ok = False
    if g1_ok:
        print(f"  [G1 PASS] All {len(pool_records)} pool records resolve to unique canonical IDs")
    gates.append(("G1", "Unique canonical resolution", g1_ok))

    g2_ok = all(not e.needs_rename for e in entries if e.ok())
    print(f"  [{'G2 PASS' if g2_ok else 'G2 FAIL'}] Filenames match canonical IDs")
    gates.append(("G2", "Canonical filenames", g2_ok))

    g3_ok = all(e.ok() for e in entries)
    print(f"  [{'G3 PASS' if g3_ok else 'G3 FAIL'}] No audit errors")
    gates.append(("G3", "No errors", g3_ok))

    filenames = [e.expected_filename for e in entries if e.ok()]
    dupes = {f for f in filenames if filenames.count(f) > 1}
    g4_ok = len(dupes) == 0
    print(f"  [{'G4 PASS' if g4_ok else 'G4 FAIL'}] No duplicate filenames")
    gates.append(("G4", "No duplicates", g4_ok))

    passed = sum(1 for _, _, ok in gates if ok)
    print(f"\nGates: {passed}/{len(gates)} passed")
    for name, desc, ok in gates:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {desc}")
    return 0 if all(ok for _, _, ok in gates) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit and migrate Contract filenames")
    parser.add_argument("--contracts-dir", default="/mnt/d/condiag-artifacts/condiag/pool/dev10_contracts")
    parser.add_argument("--pool", default="/mnt/d/condiag-artifacts/condiag/pool/condiag_dev_pool.json")
    parser.add_argument("--execute", action="store_true", help="Actually rename files")
    parser.add_argument("--gate", action="store_true", help="Run full gate check")
    args = parser.parse_args(argv)

    contracts_dir = Path(args.contracts_dir)
    pool_path = Path(args.pool)

    if not contracts_dir.is_dir():
        print(f"ERROR: contracts directory not found: {contracts_dir}")
        return 1

    pool_records = []
    if pool_path.is_file():
        pool_records = _load_pool(pool_path)
        print(f"Loaded {len(pool_records)} pool records from {pool_path}")
    else:
        print(f"WARNING: pool file not found, skipping pool checks")

    entries = audit_contracts(contracts_dir)
    exit_code = print_audit_report(entries, pool_records)
    migrate_contracts(entries, dry_run=not args.execute)

    if args.gate and pool_records:
        gate_exit = gate_check(entries, pool_records)
        exit_code = max(exit_code, gate_exit)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
