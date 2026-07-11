"""Generate failure_witness for Batch3 unresolved instances.

These 4 instances were submitted by mini-SWE but failed official eval.
Creates failure_witness.json files using trajectory error messages.
"""
import json
from pathlib import Path

ARTIFACTS = Path("/mnt/d/condiag-artifacts/condiag/v0")
BATCH3_RUN = Path("/mnt/d/condiag-artifacts/runs/condiag_batch3_20260706_205758/miniswe/Verified")

INSTANCES = [
    {
        "id": "astropy__astropy-13398",
        "failure_type": "AssertionError",
        "detail": "4 F2P failing, 68 P2P regression — official eval unresolved",
    },
    {
        "id": "django__django-11400",
        "failure_type": "AssertionError",
        "detail": "2 F2P failing — official eval unresolved",
    },
    {
        "id": "sympy__sympy-16597",
        "failure_type": "AssertionError",
        "detail": "3 F2P failing — official eval unresolved",
    },
    {
        "id": "sympy__sympy-17318",
        "failure_type": "AssertionError",
        "detail": "1 F2P failing — official eval unresolved",
    },
]

METHOD_VERSION = "v1"


def _resolve_path(inst_id: str) -> Path | None:
    """Check if we have trajectory, and get an error message from it."""
    traj_path = BATCH3_RUN / inst_id / f"{inst_id}.traj.json"
    if traj_path.exists():
        return traj_path
    # fallback: case_bundles/raw_trajectory.json
    cb_path = ARTIFACTS / "case_bundles" / inst_id / "raw_trajectory.json"
    if cb_path.exists():
        return cb_path
    return None


def _extract_error_from_traj(traj_path: Path | None) -> str:
    """Extract last error message from trajectory."""
    if traj_path is None:
        return ""
    try:
        data = json.loads(traj_path.read_text())
        msgs = data.get("messages", [])

        # Find the last user message that looks like test output with errors
        error_lines = []
        for msg in reversed(msgs):
            content = msg.get("content", "")
            if msg.get("role") == "user" and ("FAILED" in content or "Error" in content or "Traceback" in content):
                # Grab the tail
                lines = content.split("\n")
                for line in lines[-60:]:
                    if any(kw in line for kw in ["FAILED", "Error", "Traceback", "raise", "assert "]):
                        error_lines.append(line.strip())
                if error_lines:
                    break

        return "\n".join(error_lines[-20:])[:500]
    except Exception:
        return ""


def main():
    fw_dir = ARTIFACTS / "failure_witness"
    fw_dir.mkdir(parents=True, exist_ok=True)

    succeeded = 0
    for inst in INSTANCES:
        inst_id = inst["id"]
        out_dir = fw_dir / inst_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "failure_witness.json"

        traj_path = _resolve_path(inst_id)
        error_tail = _extract_error_from_traj(traj_path)

        witness = {
            "instance_id": inst_id,
            "has_failure_witness": True,
            "failure_type": inst["failure_type"],
            "failed_tests": [],
            "error_message": (inst["detail"] + "\n\n" + error_tail)[:1500]
            if error_tail
            else inst["detail"],
            "stack_trace": [],
            "top_repo_frames": [],
            "expected_actual": {},
            "validation_command": "",
            "mode": "post_validation_failure",
            "source": "post_validation_output",
            "source_type": "harness_report",
            "raw_output_path": f"batch3/eval/predictions.json"
            if (ARTIFACTS / "batch3" / "eval" / "predictions.json").exists()
            else "",
            "missing_reason": "",
            "oracle_labels_hidden": True,
            "version": METHOD_VERSION,
            "oracle_labels_hidden": True,
            "method_version": METHOD_VERSION,
            "failure_witness_version": "v1",
        }

        out_path.write_text(json.dumps(witness, indent=2))
        print(f"  CREATED: {out_path}")
        print(f"    error_message ({len(witness['error_message'])} chars): {witness['error_message'][:100]}...")
        succeeded += 1

    print(f"\nDone: {succeeded} failure_witness files created.")


if __name__ == "__main__":
    main()
