#!/usr/bin/env python3
"""Step 6: Batch generate Diagnosis + Contract for Dev 10 instances.

Reads condiag_dev_pool.json, runs the full pipeline for each:
  TrajParser → RuntimeSignals.extract → ContextDeficiencyDiagnoser.diagnose
    → DiagnosticSearchContractBuilder.build

Saves individual diagnosis + contract JSON + batch summary.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")
from condiag.trajectory_signals import TrajParser, RuntimeSignals
from condiag.context_deficiency_diagnoser import (
    ContextDeficiencyDiagnoser, PatchBehavior,
)
from condiag.search_contract_builder import DiagnosticSearchContractBuilder, contract_to_file
from condiag.instance_identity import (
    instance_artifact_filename,
    resolve_canonical_instance_id,
)

INSTANCES = Path("/mnt/d/condiag-artifacts/condiag/instances")
DEV_POOL_PATH = Path("/mnt/d/condiag-artifacts/condiag/pool/condiag_dev_pool.json")
OUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/diagnoses")

# DIR_MAP for MULTI hash mismatches (same as in generate_pre_split_snapshot.py)
DIR_MAP = {
    "instance_NodeBB__NodeBB-767973717be700f46f06f3e7f4fc5504f2b6de5":
        "instance_NodeBB__NodeBB-767973717be700f46f06f3e7f4fc550c63509046-vnan",
    "instance_ansible__ansible-1a4644ff15355fd696ac5b9d074a561b82334e48":
        "instance_ansible__ansible-1a4644ff15355fd696ac5b9d074a566a80fe7ca3-v30a923fb5c164d6cd18280c02422f75e611e8fb2",
    "instance_ansible__ansible-8127abbc298cabf04aaa89a478fc5b4fb2eaa47b":
        "instance_ansible__ansible-8127abbc298cabf04aaa89a478fc5e5e3432a6fc-v30a923fb5c164d6cd18280c02422f75e611e8fb2",
    "instance_ansible__ansible-83909bfa22573777e3db5688773bd5c8543d84cf":
        "instance_ansible__ansible-83909bfa22573777e3db5688773bda59721962ad-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5",
    "instance_ansible__ansible-942424e10b2095a173dbd78e7128f6ce2b5bcac3":
        "instance_ansible__ansible-942424e10b2095a173dbd78e7128f52f7995849b-v30a923fb5c164d6cd18280c02422f75e611e8fb2",
    "instance_ansible__ansible-949c503f2ef4b2c5d668af0492a5c71e7f136ffa":
        "instance_ansible__ansible-949c503f2ef4b2c5d668af0492a5c0db1ab86140-v0f01c69f1e2528b935359cfe578530722bca2c59",
    "instance_ansible__ansible-bec27fb4c0a40c5f8bbcf26a47570416b4ac1db7":
        "instance_ansible__ansible-bec27fb4c0a40c5f8bbcf26a475704227d65ee73-v30a923fb5c164d6cd18280c02422f75e611e8fb2",
}


def find_dir(iid):
    if iid in DIR_MAP:
        return DIR_MAP[iid]
    if (INSTANCES / iid).exists():
        return iid
    for d in INSTANCES.iterdir():
        if d.name.startswith(iid):
            return d.name
    return None


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def get_error_type(witness_data, el_path: Path) -> str:
    """Extract error_type from witness or fallback to eligibility."""
    et = witness_data.get("failure_type", "") or witness_data.get("error_type", "")
    if et:
        return et
    msg = witness_data.get("error_message", "")
    for pat in ["AttributeError", "TypeError", "AssertionError",
                "ImportError", "ModuleNotFoundError", "ValueError",
                "KeyError", "IndexError", "OSError", "FileNotFoundError",
                "RuntimeError", "NameError", "SyntaxError",
                "ZeroDivisionError", "StopIteration"]:
        if pat in msg:
            return pat
    return "unknown"


# =========================================================================
# Load Dev pool
# =========================================================================
dev_pool = json.loads(DEV_POOL_PATH.read_text())
dev_instances = dev_pool["instances"]

diagnoser = ContextDeficiencyDiagnoser()

OUT_DIR.mkdir(parents=True, exist_ok=True)

results = []
ok = 0
fail = 0

print(f"Generating Diagnosis + Contract for {len(dev_instances)} Dev instances...")
print("=" * 70)

for inst in dev_instances:
    iid = inst["instance_id"]
    dir_name = find_dir(iid)
    if not dir_name:
        print(f"  FAIL {iid[:50]} dir not found")
        fail += 1
        continue

    attempt_dir = INSTANCES / dir_name / "attempt_1"
    traj_path = attempt_dir / "trajectory.json"
    witness_path = attempt_dir / "failure_witness.json"
    el_path = attempt_dir / "failure_eligibility.json"

    if not traj_path.exists() or not witness_path.exists():
        print(f"  FAIL {iid[:50]} missing traj or witness")
        fail += 1
        continue

    try:
        # Parse trajectory + extract signals
        parser = TrajParser(traj_path)
        witness_data = json.loads(witness_path.read_text())
        runtime = RuntimeSignals.extract(parser, witness_path)

        # Parse patch behavior from patch.diff
        patch_path = attempt_dir / "patch.diff"
        if patch_path.exists():
            text = patch_path.read_text(encoding="utf-8", errors="replace")
            files = set()
            added = 0
            removed = 0
            for line in text.splitlines():
                if line.startswith("+++ b/") or line.startswith("--- a/"):
                    files.add(line.split("/", 2)[-1] if "/" in line else line)
                elif line.startswith("+") and not line.startswith("+++"):
                    added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
            patch_behavior = PatchBehavior(
                has_edit=len(files) > 0,
                files_edited_count=len(files),
                multi_file_edit=len(files) > 1,
                patch_size=added + removed,
            )
        else:
            patch_behavior = PatchBehavior()

        # ...existing code...

        # Get error type + run diagnosis
        error_type = get_error_type(witness_data, el_path)
        error_message = witness_data.get("error_message", "")
        diagnosis = diagnoser.diagnose(error_type, runtime, patch_behavior, error_message=error_message)

        # Build search contract
        builder = DiagnosticSearchContractBuilder(parser, witness_path)
        contract = builder.build(runtime)

        # Save individual diagnosis using canonical instance ID
        canon_iid = dir_name  # find_dir(iid) returns the canonical dir_name
        diag_out = OUT_DIR / f"{instance_artifact_filename(canon_iid)}.diagnosis.json"
        diag_data = {
            "instance_id": canon_iid,
            "canonical_instance_id": canon_iid,
            "pool_alias_id": iid,
            "generated_at": now_iso(),
            "diagnosis_version": diagnosis.diagnosis_version,
            "diagnoser_version": diagnosis.diagnoser_version,
            "failure_family_version": diagnosis.failure_family_version,
            "confidence_version": diagnosis.confidence_version,
            "primary_cdtype": diagnosis.primary_cdtype,
            "cdtype_scores": diagnosis.cdtype_scores,
            "diagnosis_rationale": diagnosis.diagnosis_rationale,
            "confidence": diagnosis.confidence,
            "confidence_factors": diagnosis.confidence_factors,
            "prior_scores": diagnosis.prior_scores,
            "signal_evidence": diagnosis.signal_evidence,
            "patch_scores": diagnosis.patch_scores,
            "failure_family": diagnosis.failure_family,
            "error_type": diagnosis.error_type,
        }
        diag_out.write_text(json.dumps(diag_data, indent=2))

        # Save individual contract using canonical filename
        contract_out = OUT_DIR / f"{instance_artifact_filename(canon_iid)}.contract.json"
        contract_to_file(contract, contract_out)

        # Summary line
        cdtype = diagnosis.primary_cdtype
        mode = runtime.exploration_mode
        align = runtime.error_edit_alignment
        insp_count = len(contract.required_inspections)
        search_count = len(contract.required_searches)
        print(f"  OK  {iid[:50]:<50} CDType={cdtype:<35} mode={mode:<15} align={align:<22} insp={insp_count} srch={search_count}")
        ok += 1

        results.append({
            "instance_id": iid,
            "status": "ok",
            "primary_cdtype": cdtype,
            "exploration_mode": mode,
            "error_edit_alignment": align,
            "inspections_count": insp_count,
            "searches_count": search_count,
        })

    except Exception as e:
        print(f"  ERR {iid[:50]} {e}")
        fail += 1
        results.append({"instance_id": iid, "status": "error", "error": str(e)})

# =========================================================================
# Batch summary
# =========================================================================
summary = {
    "batch_version": "v1.0",
    "generated_at": now_iso(),
    "source_pool": "condiag_dev_pool.json",
    "dev_count": len(dev_instances),
    "ok": ok,
    "fail": fail,
    "results": results,
}
summary_path = OUT_DIR / "dev_batch_summary.json"
summary_path.write_text(json.dumps(summary, indent=2))

print(f"\nDone. OK={ok} FAIL={fail}")
print(f"Individual files saved to {OUT_DIR}/")
print(f"Summary saved to {summary_path}")

# CDType distribution
cdtypes = {}
for r in results:
    if r["status"] == "ok":
        ct = r["primary_cdtype"]
        cdtypes[ct] = cdtypes.get(ct, 0) + 1
print("\nDev CDType distribution:")
for ct, count in sorted(cdtypes.items(), key=lambda x: -x[1]):
    print(f"  {ct:<40} {count}")
