#!/usr/bin/env python3
"""Regenerate dev-10 contracts with v1.1 builder + run compliance analyzer."""
import json, sys
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")

from condiag.trajectory_signals import TrajParser, RuntimeSignals
from condiag.search_contract_builder import build_contract, contract_to_file
from condiag.instance_identity import (
    instance_artifact_filename,
    resolve_canonical_instance_id,
)
from experiments.contract_compliance_analyzer import ContractComplianceAnalyzer

POOL = Path("/mnt/d/condiag-artifacts/condiag/pool/condiag_dev_pool.json")
INSTANCES = Path("/mnt/d/condiag-artifacts/condiag/instances")
OUTPUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/pool/dev10_contracts")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

pool = json.loads(POOL.read_text())
rows = []

for inst in pool["instances"]:
    iid = inst["instance_id"]
    dname = inst.get("dir_name", iid)
    sep = "=" * 70
    print(f"\n{sep}")
    print(f"Processing: {iid}")
    print(sep)

    traj_path = INSTANCES / dname / "attempt_1" / "trajectory.json"
    witness_path = INSTANCES / dname / "attempt_1" / "failure_witness.json"
    if not traj_path.exists():
        print(f"  SKIP: no trajectory at {traj_path}")
        continue

    # Build contract
    parser = TrajParser(traj_path)
    signals = RuntimeSignals.extract(parser, witness_path)
    contract = build_contract(traj_path, witness_path, signals=signals)

    # Resolve canonical instance ID from pool record
    canonical_iid = resolve_canonical_instance_id(inst)
    if dname != canonical_iid:
        print(f"  NOTE: dir_name ({dname}) != canonical ID ({canonical_iid})")
        # The resolve function may return a different form; use the resolved value

    # Save contract using canonical filename
    safe_name = instance_artifact_filename(canonical_iid)
    out_path = OUTPUT_DIR / f"{safe_name}.json"
    contract_to_file(contract, out_path)
    print(f"  Saved to {out_path}")
    print(f"  Canonical instance_id: {canonical_iid}")

    # Run compliance analyzer (attempt-1 = incidental counterfactual)
    analyzer = ContractComplianceAnalyzer(parser, contract)
    report = analyzer.analyze()
    s = report.summary

    # Print per-instraint detail
    print(f"  Constraints:")
    for ae in report.constraints:
        print(f"    [{ae.status}] {ae.action_label} severity={ae.severity}")
    print(f"  Inspections: {sum(1 for a in report.inspections if a.status=='COMPLIANT')}/{len(report.inspections)} COMPLIANT")
    print(f"  Searches: {sum(1 for a in report.searches if a.status=='COMPLIANT')}/{len(report.searches)} COMPLIANT")

    rows.append({
        "instance_id": iid[:55],
        "cdtype": contract.context_deficiency_diagnosis.primary_cdtype if contract.context_deficiency_diagnosis else "?",
        "contract_mode": contract.contract_mode,
        "n_inspections": len(contract.required_inspections),
        "n_searches": len(contract.required_searches),
        "n_constraints": len(contract.structured_constraints),
        "total_actions": s.total_actions,
        "determinability": f"{s.determinability_rate:.2f}" if s.determinability_rate is not None else "None",
        "required_comp": f"{s.required_action_compliance:.2f}" if s.required_action_compliance is not None else "None",
        "recommended_comp": f"{s.recommended_action_compliance:.2f}" if s.recommended_action_compliance is not None else "None",
        "inspection_comp": f"{s.inspection_compliance:.2f}" if s.inspection_compliance is not None else "None",
        "search_comp": f"{s.search_compliance:.2f}" if s.search_compliance is not None else "None",
        "constraint_comp": f"{s.constraint_compliance:.2f}" if s.constraint_compliance is not None else "None",
        "undetermined": s.undetermined_count,
        "not_applicable": s.not_applicable_count,
    })

# Summary table
sep2 = "=" * 120
print(f"\n\n{sep2}")
print("DEV-10 CONTRACT REGENERATION + COMPLIANCE AUDIT SUMMARY")
print(sep2)
header = (
    f"{'Instance':<55} {'CDType':<25} {'Mode':<10} "
    f"{'Ins':>3} {'Srch':>4} {'Con':>3} {'Total':>5} "
    f"{'Det':>5} {'ReqC':>5} {'RecC':>5} {'InsC':>5} "
    f"{'SrcC':>5} {'ConC':>5} {'Unk':>3} {'NA':>3}"
)
print(header)
print("-" * len(header))
for r in rows:
    print(
        f"{r['instance_id']:<55} {r['cdtype']:<25} {r['contract_mode']:<10} "
        f"{r['n_inspections']:>3} {r['n_searches']:>4} {r['n_constraints']:>3} "
        f"{r['total_actions']:>5} {r['determinability']:>5} "
        f"{r['required_comp']:>5} {r['recommended_comp']:>5} "
        f"{r['inspection_comp']:>5} {r['search_comp']:>5} "
        f"{r['constraint_comp']:>5} {r['undetermined']:>3} {r['not_applicable']:>3}"
    )

print(f"\nSaved {len(rows)} contracts to {OUTPUT_DIR}")
