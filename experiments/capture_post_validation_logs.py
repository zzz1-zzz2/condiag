#!/usr/bin/env python3
"""Capture per-instance post-validation raw failure logs.

For the 5 canonical first-failed instances, rerun the same post-validation
tests that the SWE-bench harness runs, but save per-instance raw stdout/stderr
so that failure_witness_builder.from_eval_log() can parse actual failure output.

Usage:
    # Dry-run: print commands without executing
    python3 experiments/capture_post_validation_logs.py --dry-run

    # Execute: run Docker containers and capture logs
    python3 experiments/capture_post_validation_logs.py [--force]

Output layout:
    /mnt/d/condiag-artifacts/condiag/v0/post_validation_logs/<instance_id>/
        validation_combined.log
        validation_stdout.log
        validation_stderr.log
        validation_command.txt
        patch_apply.log
        metadata.json
"""

import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
DEFAULT_ARTIFACT_BASE = Path("/mnt/d/condiag-artifacts/condiag/v0")
DEFAULT_CANONICAL_MATRIX = DEFAULT_ARTIFACT_BASE / "canonical_base_eval_matrix.csv"
DEFAULT_DATASET_DIR = DEFAULT_ARTIFACT_BASE / "task0_missing_base_eval" / "swebench_verified_dataset"
DEFAULT_OUTPUT_BASE = DEFAULT_ARTIFACT_BASE / "post_validation_logs"

DOCKER_IMAGE_TEMPLATE = "swebench/sweb.eval.x86_64.{repo_escaped}:latest"


# ---------------------------------------------------------------------------
# Canonical matrix
# ---------------------------------------------------------------------------

def read_canonical_matrix(csv_path: Path) -> list[dict]:
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def filter_first_failed(rows: list[dict]) -> list[dict]:
    return [
        r for r in rows
        if r.get("base_resolved", "").strip() == "False"
        and r.get("eval_status", "").strip() == "EVALUATED"
        and r.get("conflict", "").strip() == ""
    ]


# ---------------------------------------------------------------------------
# SWE-bench dataset reader
# ---------------------------------------------------------------------------

def load_swebench_dataset(dataset_dir: Path) -> list[dict]:
    """Load SWE-Bench-Verified dataset from cached Arrow files.

    Returns list of row dicts with keys: instance_id, test_patch,
    FAIL_TO_PASS, PASS_TO_PASS, base_commit, repo, patch (gold —
    MUST NOT USE for agent input).
    """
    # Try Arrow IPC format
    arrow_path = dataset_dir / "data-00000-of-00001.arrow"
    if arrow_path.exists():
        try:
            import pyarrow as pa
            table = pa.ipc.open_file(open(arrow_path, "rb")).read_all()
            return table.to_pylist()
        except Exception:
            pass

    # Fallback: try datasets library
    try:
        from datasets import load_dataset
        ds = load_dataset(
            "arrow",
            data_files=str(arrow_path),
            split="train",
        )
        return [row for row in ds]
    except Exception:
        pass

    raise RuntimeError(
        f"Cannot read SWE-bench dataset at {dataset_dir}. "
        "Install pyarrow or datasets."
    )


def find_instance_in_dataset(
    dataset: list[dict], instance_id: str
) -> Optional[dict]:
    for row in dataset:
        if row.get("instance_id") == instance_id:
            return row
    return None


# ---------------------------------------------------------------------------
# Docker image helpers
# ---------------------------------------------------------------------------

def docker_image_name(instance_id: str) -> str:
    """Construct Docker image name for an instance.

    E.g. django__django-11820 →
        swebench/sweb.eval.x86_64.django_1776_django-11820:latest
    """
    parts = instance_id.split("__")
    if len(parts) != 2:
        raise ValueError(f"Unexpected instance_id format: {instance_id}")
    repo_part = parts[0]
    instance_num = parts[1]
    repo_escaped = f"{repo_part}_1776_{instance_num}"
    return f"swebench/sweb.eval.x86_64.{repo_escaped}:latest"


def _make_container_name(instance_id: str) -> str:
    safe = instance_id.replace("__", "_").replace("-", "_")
    return f"condiag_val_{safe}"


# ---------------------------------------------------------------------------
# Validation command construction
# ---------------------------------------------------------------------------

def _parse_test_list(test_str: str) -> list[str]:
    """Parse SWE-bench FAIL_TO_PASS / PASS_TO_PASS string into test list."""
    if not test_str or test_str.strip() == "":
        return []
    try:
        parsed = json.loads(test_str)
        if isinstance(parsed, list):
            return parsed
        return []
    except (json.JSONDecodeError, TypeError):
        return []


def _django_test_label(spec: str) -> str:
    """Convert SWE-bench FAIL_TO_PASS format to Django test label.

    SWE-bench format:  'test_method (module.Class)'
    Django format:      'module.Class.test_method'

    Handles edge case where module.Class already includes the test_method name.
    """
    m = re.match(r'(\w+) \(([\w.]+)\)', spec)
    if m:
        test_method = m.group(1)
        module_class = m.group(2)
        # If module_class already ends with test_method, use it directly
        if module_class.endswith("." + test_method) or module_class.endswith(test_method):
            return module_class
        return f"{module_class}.{test_method}"
    return spec


def build_validation_command(
    instance_id: str,
    fail_to_pass: list[str],
    repo: str,
    container_name: str,
) -> str:
    """Build the test command to run inside the Docker container.

    Adapts to Django vs sympy vs other repos.

    Args:
        instance_id: Full instance ID.
        fail_to_pass: List of FAIL_TO_PASS test names.
        repo: Repository name (e.g. "django", "sympy").
        container_name: Docker container name for docker exec.
    """
    if not fail_to_pass:
        # If no explicit FAIL_TO_PASS, run all tests
        return f"docker exec {container_name} bash -c 'echo NO_FAIL_TO_PASS_SPECIFIED'"

    if repo == "django":
        # Django: install deps, add tests/ to PYTHONPATH so that
        # --settings=test_sqlite is importable, then run from /testbed
        # so django module is also importable.
        specs_list = [_django_test_label(t) for t in fail_to_pass]
        specs = " ".join(specs_list)
        cmd = (
            f"pip install asgiref pytz sqlparse 2>&1 && "
            f"cd /testbed && PYTHONPATH=/testbed/tests:$PYTHONPATH "
            f"python -m django test {specs} --settings=test_sqlite "
            f"--verbosity=2 2>&1"
        )
    elif repo == "sympy":
        # Sympy: install deps + pytest, then run
        test_files = " or ".join(fail_to_pass)
        cmd = (
            f"pip install mpmath pytest 2>&1 && "
            f"cd /testbed && "
            f"python -m pytest -x -v --tb=long "
            f"-k '{test_files}' 2>&1"
        )
    else:
        # Generic: pytest
        test_files = " or ".join(fail_to_pass)
        cmd = (
            f"cd /testbed && "
            f"python -m pytest -x -v --tb=long "
            f"-k '{test_files}' 2>&1"
        )

    return f"docker exec {container_name} bash -c '{cmd}'"


def build_capture_plan(
    instance_id: str,
    patch_path: Path,
    test_patch: str,
    fail_to_pass: list[str],
    pass_to_pass: list[str],
    repo: str,
    output_dir: Path,
    docker_image: str,
) -> dict:
    """Build a capture plan (commands + metadata) without executing.

    Returns a dict describing what will happen.
    """
    container_name = _make_container_name(instance_id)

    steps = []
    # Save test_patch to a temp file for docker cp (avoids heredoc quoting issues)
    test_patch_local_path = output_dir / "test_patch.diff"
    if test_patch:
        test_patch_local_path.parent.mkdir(parents=True, exist_ok=True)
        test_patch_local_path.write_text(test_patch, encoding="utf-8")

    # Step 1: Start container
    steps.append({
        "step": 1,
        "action": "start_container",
        "description": "Start Docker container in background",
        "command": f"docker run -d --rm --name {container_name} {docker_image} sleep 3600",
    })

    # Step 2: Copy attempt_1 patch
    steps.append({
        "step": 2,
        "action": "copy_patch",
        "description": "Copy attempt_1 patch into container",
        "command": f"docker cp {patch_path} {container_name}:/tmp/attempt_1.patch",
    })

    # Step 3: Apply attempt_1 patch
    steps.append({
        "step": 3,
        "action": "apply_attempt_1_patch",
        "description": "Apply attempt_1 patch",
        "command": f"docker exec {container_name} bash -c 'cd /testbed && git apply /tmp/attempt_1.patch 2>&1'",
        "output_log": str(output_dir / "patch_apply.log"),
    })

    # Step 4: Copy test_patch into container
    if test_patch_local_path.exists():
        steps.append({
            "step": 4,
            "action": "copy_test_patch",
            "description": "Copy test (validation) patch into container",
            "command": f"docker cp {test_patch_local_path} {container_name}:/tmp/test_patch.diff",
        })

        # Step 5: Apply test_patch
        steps.append({
            "step": 5,
            "action": "apply_test_patch",
            "description": "Apply test (validation) patch",
            "command": f"docker exec {container_name} bash -c 'cd /testbed && git apply /tmp/test_patch.diff 2>&1'",
            "output_log": str(output_dir / "test_patch_apply.log"),
        })
    else:
        steps.append({
            "step": 4,
            "action": "skip_test_patch",
            "description": "No test patch available, skipping",
            "command": "echo 'NO_TEST_PATCH'",
        })

    # Step 5/6: Run validation tests
    test_step_num = 6 if test_patch_local_path.exists() else 5
    test_cmd = build_validation_command(
        instance_id, fail_to_pass, repo, container_name
    )
    steps.append({
        "step": test_step_num,
        "action": "run_validation_tests",
        "description": "Run post-validation FAIL_TO_PASS tests",
        "command": test_cmd,
        "stdout_log": str(output_dir / "validation_stdout.log"),
        "stderr_log": str(output_dir / "validation_stderr.log"),
        "combined_log": str(output_dir / "validation_combined.log"),
    })

    # Last step: Stop container
    stop_step_num = test_step_num + 1
    steps.append({
        "step": stop_step_num,
        "action": "stop_container",
        "description": "Stop and remove container",
        "command": f"docker stop {container_name} 2>/dev/null; docker rm {container_name} 2>/dev/null; true",
    })

    return {
        "instance_id": instance_id,
        "docker_image": docker_image,
        "container_name": container_name,
        "output_dir": str(output_dir),
        "test_patch_present": bool(test_patch),
        "fail_to_pass_count": len(fail_to_pass),
        "pass_to_pass_count": len(pass_to_pass),
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Plan execution
# ---------------------------------------------------------------------------

def execute_plan(plan: dict) -> dict:
    """Execute a capture plan, running Docker commands and saving logs.

    Returns metadata dict with execution results.
    """
    instance_id = plan["instance_id"]
    output_dir = Path(plan["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "instance_id": instance_id,
        "docker_image": plan["docker_image"],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "steps": [],
        "overall_status": "unknown",
        "error": None,
    }

    # Write validation command
    if plan["steps"]:
        with open(output_dir / "validation_command.txt", "w") as f:
            for step in plan["steps"]:
                f.write(f"# Step {step['step']}: {step['description']}\n")
                f.write(step["command"] + "\n\n")

    try:
        for step in plan["steps"]:
            step_result = {
                "step": step["step"],
                "action": step["action"],
                "status": "pending",
            }

            if step["action"] in ("stop_container",):
                # Best-effort cleanup, don't fail on error
                subprocess.run(
                    step["command"], shell=True,
                    capture_output=True, timeout=30
                )
                step_result["status"] = "completed"
                metadata["steps"].append(step_result)
                continue

            # Run the command
            result = subprocess.run(
                step["command"], shell=True,
                capture_output=True, timeout=600,  # 10 min max per step
            )

            stdout = result.stdout.decode("utf-8", errors="replace")
            stderr = result.stderr.decode("utf-8", errors="replace")
            combined = stdout + "\n" + stderr

            status = "completed" if result.returncode == 0 else "completed_with_errors"

            # Save logs if output paths are specified
            if "output_log" in step:
                with open(step["output_log"], "w") as f:
                    f.write(combined)

            if "stdout_log" in step and step["stdout_log"]:
                with open(step["stdout_log"], "w") as f:
                    f.write(stdout)

            if "stderr_log" in step and step["stderr_log"]:
                with open(step["stderr_log"], "w") as f:
                    f.write(stderr)

            if "combined_log" in step and step["combined_log"]:
                with open(step["combined_log"], "w") as f:
                    f.write(combined)

            step_result["status"] = status
            step_result["returncode"] = result.returncode
            if result.returncode != 0:
                step_result["stderr_preview"] = stderr[:500]

            metadata["steps"].append(step_result)

            # If patch apply fails, stop
            if step["action"] in ("apply_attempt_1_patch", "apply_test_patch") and result.returncode != 0:
                metadata["overall_status"] = f"{step['action']}_failed"
                metadata["error"] = f"{step['action']} failed: {stderr[:300]}"
                # Save what we have and stop
                _write_metadata(output_dir, metadata)
                return metadata

        metadata["overall_status"] = "completed"
    except subprocess.TimeoutExpired:
        metadata["overall_status"] = "timeout"
        metadata["error"] = "Docker command timed out"
    except Exception as e:
        metadata["overall_status"] = "error"
        metadata["error"] = str(e)
    finally:
        # Always try to stop container
        subprocess.run(
            f"docker stop {plan['container_name']} 2>/dev/null; "
            f"docker rm {plan['container_name']} 2>/dev/null",
            shell=True, capture_output=True, timeout=30
        )

    metadata["finished_at"] = datetime.now(timezone.utc).isoformat()
    _write_metadata(output_dir, metadata)
    return metadata


def _write_metadata(output_dir: Path, metadata: dict):
    """Write metadata.json."""
    with open(output_dir / "metadata.json", "w") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Repo name extraction
# ---------------------------------------------------------------------------

def extract_repo(instance_id: str) -> str:
    """Extract repo name from instance ID (e.g. django__django-11820 → django)."""
    return instance_id.split("__")[0]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    force = "--force" in sys.argv
    output_base = DEFAULT_OUTPUT_BASE

    if not dry_run and not force:
        print(
            "ERROR: Must specify either --dry-run (preview only) or --force (execute).\n"
            "  python3 capture_post_validation_logs.py --dry-run   # preview\n"
            "  python3 capture_post_validation_logs.py --force      # execute\n"
        )
        sys.exit(1)

    # Read canonical matrix
    if not DEFAULT_CANONICAL_MATRIX.exists():
        print(f"ERROR: canonical matrix not found at {DEFAULT_CANONICAL_MATRIX}")
        sys.exit(1)

    rows = read_canonical_matrix(DEFAULT_CANONICAL_MATRIX)
    first_failed = filter_first_failed(rows)

    print("=" * 60)
    print("Task 3C — Capture Post-validation Raw Failure Logs")
    print("=" * 60)
    print(f"  Canonical matrix: {DEFAULT_CANONICAL_MATRIX}")
    print(f"  Output base:      {output_base}")
    print(f"  Mode:             {'DRY RUN' if dry_run else 'EXECUTE'}")
    print(f"  Instances:        {len(first_failed)}")
    print()

    # Load SWE-bench dataset for test patches
    print("[1/3] Loading SWE-bench dataset...")
    try:
        dataset = load_swebench_dataset(DEFAULT_DATASET_DIR)
        print(f"  Loaded {len(dataset)} instances")
    except RuntimeError as e:
        print(f"  WARNING: {e}")
        print("  Will proceed with test_patch=None (validation commands may be incomplete)")
        dataset = []

    # Build plans for each instance
    print("[2/3] Building capture plans...")
    plans = []

    for ff in first_failed:
        instance_id = ff["instance_id"]
        patch_path = Path(ff.get("patch_path", "")) if ff.get("patch_path") else None
        repo = extract_repo(instance_id)
        output_dir = output_base / instance_id
        docker_image = docker_image_name(instance_id)

        # Find dataset row
        ds_row = find_instance_in_dataset(dataset, instance_id) if dataset else None
        test_patch = ds_row.get("test_patch", "") if ds_row else ""
        fail_to_pass = _parse_test_list(ds_row.get("FAIL_TO_PASS", "")) if ds_row else []
        pass_to_pass = _parse_test_list(ds_row.get("PASS_TO_PASS", "")) if ds_row else []

        plan = build_capture_plan(
            instance_id=instance_id,
            patch_path=patch_path,
            test_patch=test_patch,
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            repo=repo,
            output_dir=output_dir,
            docker_image=docker_image,
        )
        plans.append(plan)

        print(f"\n  {instance_id}:")
        print(f"    Docker image: {docker_image}")
        print(f"    Test patch:   {'present' if test_patch else 'MISSING'} ({len(test_patch)} chars)")
        print(f"    FAIL_TO_PASS: {len(fail_to_pass)} tests")
        print(f"    PASS_TO_PASS: {len(pass_to_pass)} tests")
        print(f"    Output dir:   {output_dir}")
        print(f"    Steps:        {len(plan['steps'])}")

        if dry_run:
            for step in plan["steps"]:
                cmd_preview = step["command"][:120] + "..." if len(step["command"]) > 120 else step["command"]
                print(f"      Step {step['step']}: {step['description']}")
                print(f"        $ {cmd_preview}")

    # Execute if --force
    if force:
        print("\n[3/3] Executing capture plans...")
        results = []
        for plan in plans:
            instance_id = plan["instance_id"]
            if (Path(plan["output_dir"]) / "validation_combined.log").exists():
                print(f"  {instance_id}: logs already exist (use --force to overwrite)")
                continue

            print(f"  {instance_id}: capturing...")
            metadata = execute_plan(plan)
            status = metadata.get("overall_status", "unknown")
            print(f"    Status: {status}")
            results.append(metadata)

        # Summary
        print()
        print("Capture summary:")
        for r in results:
            iid = r["instance_id"]
            status = r.get("overall_status", "unknown")
            steps_ok = sum(1 for s in r.get("steps", []) if s.get("status") == "completed")
            steps_total = len(r.get("steps", []))
            print(f"  {iid:30s} {status:25s} steps={steps_ok}/{steps_total}")
    elif not dry_run:
        # --force without --dry-run would have been caught above, but just in case
        print("\n  Use --force to execute these plans.")

    print()
    print("Task 3C plan prepared. Do not enter Task 4 or Task 5.")
    print("After logs are captured, re-run:")
    print("  python3 experiments/run_failure_witness_for_pool.py")


if __name__ == "__main__":
    main()
