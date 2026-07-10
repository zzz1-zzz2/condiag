"""Task 6-alpha — Host-Agent Retry Mini-suite Protocol Smoke.

One-case (django__django-12125) × 4 baselines protocol smoke.
Validates that each baseline enters Host-Agent attempt_2 correctly and
produces protocol-valid artifacts. Does NOT run official eval.

Baselines:
    1. plain_rerun           — original issue only, no packet
    2. feedback_retry        — failure witness packet (v0 intervention staged)
    3. broad_expansion       — failure witness + broad context (v0 staged)
    4. condiag_retry_v2_alpha — v2-alpha context packet (NEW)

Output root (isolated, does NOT overwrite v0/v1):
    /mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke/

Run from WSL:
    cd /home/swelite/condiag
    DEEPSEEK_API_KEY=<key> python3 -m experiments.run_task6_alpha_protocol_smoke
"""
from __future__ import annotations
import csv
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from experiments.host_agent_retry_runner import run_host_agent_retry

INSTANCE_ID = "django__django-12125"
AGENT = "miniswe"

V0_RUNS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs")
MANIFEST_CSV = V0_RUNS_ROOT.parent / "manifest.csv"
NEW_RUNS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke")
SUMMARY_CSV = Path("/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_protocol_matrix.csv")

V2_ALPHA_PACKET = Path(
    f"/mnt/d/condiag-artifacts/condiag/v0/context_packet_v2_alpha/{INSTANCE_ID}/context_packet.md"
)

BASELINES = [
    "plain_rerun",
    "feedback_retry",
    "broad_expansion",
    "condiag_retry_v2_alpha",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _symlink_or_copy_dir(src: Path, dst: Path) -> None:
    """Symlink a directory into place; fall back to copy if cross-fs."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() or dst.is_symlink():
        return
    try:
        dst.symlink_to(src, target_is_directory=True)
    except (OSError, NotImplementedError):
        shutil.copytree(src, dst)


def stage_inputs() -> dict:
    """Stage all input artifacts into NEW_RUNS_ROOT.

    Returns dict of staged paths for reporting.
    """
    staged = {}

    # 1. base_miniswe attempt_1 (symlink to v0)
    base_src = V0_RUNS_ROOT / AGENT / "base_miniswe" / INSTANCE_ID
    base_dst = NEW_RUNS_ROOT / AGENT / "base_miniswe" / INSTANCE_ID
    _symlink_or_copy_dir(base_src, base_dst)
    staged["base_miniswe"] = str(base_dst)

    # 2. plain_rerun: no intervention dir needed (runner handles it)

    # 3. feedback_retry intervention (copy from v0)
    fb_src = V0_RUNS_ROOT / AGENT / "feedback_retry" / INSTANCE_ID / "intervention"
    fb_dst = NEW_RUNS_ROOT / AGENT / "feedback_retry" / INSTANCE_ID / "intervention"
    if fb_src.is_dir():
        fb_dst.parent.mkdir(parents=True, exist_ok=True)
        if not fb_dst.exists():
            shutil.copytree(fb_src, fb_dst)
        staged["feedback_retry_intervention"] = str(fb_dst)

    # 4. broad_expansion intervention (copy from v0)
    be_src = V0_RUNS_ROOT / AGENT / "broad_expansion" / INSTANCE_ID / "intervention"
    be_dst = NEW_RUNS_ROOT / AGENT / "broad_expansion" / INSTANCE_ID / "intervention"
    if be_src.is_dir():
        be_dst.parent.mkdir(parents=True, exist_ok=True)
        if not be_dst.exists():
            shutil.copytree(be_src, be_dst)
        staged["broad_expansion_intervention"] = str(be_dst)

    # 5. condiag_retry_v2_alpha: stage v2-alpha packet at
    #    miniswe/context_packet_v2_alpha/<iid>/intervention/
    v2_dst = NEW_RUNS_ROOT / AGENT / "context_packet_v2_alpha" / INSTANCE_ID / "intervention"
    v2_dst.mkdir(parents=True, exist_ok=True)
    pkt_dst = v2_dst / "context_packet.md"
    shutil.copyfile(V2_ALPHA_PACKET, pkt_dst)
    # Synthesize minimal intervention_report.json (internal scheduling
    # metadata; NOT agent-facing. No taxonomy labels.)
    ireport = {
        "schema_version": "condiag.intervention_report.v0",
        "instance_id": INSTANCE_ID,
        "agent": AGENT,
        "baseline": "condiag_retry_v2_alpha",
        "mode": "packet_v2_alpha",
        "status": "context_packet_v2_alpha_built",
        "should_retry": True,
        "trigger_type": "post_validation_failure",
        "trigger_reason": ["post-validation failure witness captured"],
        "has_context_packet": True,
        "context_packet_path": str(pkt_dst),
        "context_packet_version": "v2_alpha",
        "packet_source": "context_packet_v2_alpha",
        "api_navigation_used": True,
        "failure_witness_used": True,
    }
    (v2_dst / "intervention_report.json").write_text(
        json.dumps(ireport, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    staged["v2_alpha_intervention"] = str(v2_dst)

    return staged


def validate_protocol(run_dir: Path) -> dict:
    """Check that one baseline run produced protocol-valid artifacts.

    New (R0): direct_llm_patch defaults to 'unknown', NOT True.
      - 'false'  only when patch_source == 'workspace_git_diff'
      - 'true'   only when patch_source == 'agent_submission' (or other non-workspace-git)
      - 'unknown_timeout_no_attempt_report' when no attempt_report.json (launch timeout)
      - 'unknown' otherwise (parse error, unrecognized patch_source, no patch)
    """
    checks = {
        "raw_trajectory_exists": False,
        "tool_calls": 0,
        "patch_source": "",
        "attempt_report_exists": False,
        "final_report_exists": False,
        "patch_chars": 0,
        "changed_files": [],
        "valid_protocol": False,
        # R0: direct_llm_patch with explicit reason (never default True)
        "direct_llm_patch_actual": "unknown",
        "direct_llm_patch_reason": "no_attempt_report",
        # R0: timeout / launch failure diagnostics from run_report.json
        "exit_status": "",
        "timeout_seconds": None,
        "timeout_stage": "",
        "stdout_log_path": "",
        "stderr_log_path": "",
        "docker_state_path": "",
        "docker_pre_state_path": "",
        # R0: canonical_acceptability computed at end
        "canonical_acceptability": "pending",
    }
    attempt_2 = run_dir / "attempt_2"
    raw_traj = attempt_2 / "raw_trajectory.json"
    if raw_traj.is_file():
        checks["raw_trajectory_exists"] = True
        try:
            td = json.loads(raw_traj.read_text(encoding="utf-8"))
            info = td.get("info", {}) or {}
            checks["exit_status"] = info.get("exit_status", "")
        except Exception:
            pass

    attempt_report = attempt_2 / "attempt_report.json"
    if not attempt_report.is_file():
        # No attempt_report — likely timeout/abort before agent produced trajectory
        checks["direct_llm_patch_actual"] = "unknown_timeout_no_attempt_report"
        checks["direct_llm_patch_reason"] = (
            "attempt_2/attempt_report.json missing (launch timeout or abort)"
        )
    else:
        checks["attempt_report_exists"] = True
        try:
            ar = json.loads(attempt_report.read_text(encoding="utf-8"))
            checks["tool_calls"] = ar.get("tool_calls_count", 0)
            checks["patch_source"] = ar.get("patch_source", "")
            checks["patch_chars"] = ar.get("patch_chars", 0)
            checks["changed_files"] = ar.get("changed_files", [])
            checks["valid_protocol"] = ar.get("valid_protocol", False)
            ps = ar.get("patch_source", "")
            if ps == "workspace_git_diff":
                checks["direct_llm_patch_actual"] = "false"
                checks["direct_llm_patch_reason"] = f"patch_source={ps!r}"
            elif ps == "agent_submission":
                checks["direct_llm_patch_actual"] = "true"
                checks["direct_llm_patch_reason"] = (
                    f"patch_source={ps!r} (agent submission, not workspace git diff)"
                )
            elif ps in ("none", "", None):
                checks["direct_llm_patch_actual"] = "unknown"
                checks["direct_llm_patch_reason"] = (
                    f"patch_source={ps!r} (no patch produced)"
                )
            else:
                checks["direct_llm_patch_actual"] = "unknown"
                checks["direct_llm_patch_reason"] = (
                    f"patch_source={ps!r} (unrecognized)"
                )
        except Exception as e:
            checks["direct_llm_patch_actual"] = "unknown"
            checks["direct_llm_patch_reason"] = f"attempt_report parse error: {e}"

    final_report = run_dir / "final" / "final_report.json"
    if final_report.is_file():
        checks["final_report_exists"] = True

    # R0: read run_report.json for timeout / launch diagnostics
    run_report_path = run_dir / "run_report.json"
    if run_report_path.is_file():
        try:
            rr = json.loads(run_report_path.read_text(encoding="utf-8"))
            checks["timeout_seconds"] = rr.get("timeout_seconds")
            checks["timeout_stage"] = rr.get("timeout_stage") or ""
            checks["stdout_log_path"] = rr.get("stdout_log_path") or ""
            checks["stderr_log_path"] = rr.get("stderr_log_path") or ""
            checks["docker_state_path"] = rr.get("docker_state_path") or ""
            checks["docker_pre_state_path"] = rr.get("docker_pre_state_path") or ""
            if not checks["exit_status"]:
                checks["exit_status"] = rr.get("status", "")
        except Exception:
            pass

    # R0: compute canonical_acceptability
    if (checks["valid_protocol"]
            and checks["patch_source"] == "workspace_git_diff"
            and checks["raw_trajectory_exists"]):
        checks["canonical_acceptability"] = "accepted"
    elif checks["direct_llm_patch_actual"] == "true":
        checks["canonical_acceptability"] = "rejected"
    else:
        checks["canonical_acceptability"] = "pending"

    return checks


def run_one_baseline(baseline: str, timeout_sec: int = 1800) -> dict:
    """Run one baseline and return protocol validation summary."""
    print(f"\n=== {baseline} ===")
    result = run_host_agent_retry(
        instance_id=INSTANCE_ID,
        baseline=baseline,
        agent=AGENT,
        runs_root=NEW_RUNS_ROOT,
        manifest_csv=MANIFEST_CSV,
        out_root=NEW_RUNS_ROOT,
        mode="smoke",
        timeout_sec=timeout_sec,
        max_steps=50,
    )
    run_dir = NEW_RUNS_ROOT / AGENT / baseline / INSTANCE_ID
    proto = validate_protocol(run_dir)
    return {
        "instance_id": INSTANCE_ID,
        "baseline": baseline,
        "status": result.get("status", "unknown"),
        "exit_status": proto["exit_status"],
        "raw_trajectory_exists": str(proto["raw_trajectory_exists"]),
        "tool_calls": proto["tool_calls"],
        "patch_source": proto["patch_source"],
        "patch_chars": proto["patch_chars"],
        "changed_files_count": len(proto["changed_files"]),
        "attempt_report_exists": str(proto["attempt_report_exists"]),
        "final_report_exists": str(proto["final_report_exists"]),
        "valid_protocol": str(proto["valid_protocol"]),
        # R0: direct_llm_patch with actual + reason (never default True)
        "direct_llm_patch_actual": proto["direct_llm_patch_actual"],
        "direct_llm_patch_reason": proto["direct_llm_patch_reason"],
        # R0: timeout diagnostics
        "timeout_seconds": proto["timeout_seconds"] if proto["timeout_seconds"] is not None else "",
        "timeout_stage": proto["timeout_stage"],
        "stdout_log_path": proto["stdout_log_path"],
        "stderr_log_path": proto["stderr_log_path"],
        "docker_state_path": proto["docker_state_path"],
        "docker_pre_state_path": proto["docker_pre_state_path"],
        # R0: canonical_acceptability
        "canonical_acceptability": proto["canonical_acceptability"],
        # Existing fields (kept for backward compat)
        "packet_source": "context_packet_v2_alpha" if baseline == "condiag_retry_v2_alpha"
                         else ("v0_intervention" if baseline != "plain_rerun" else "none"),
        "run_dir": str(run_dir),
        "method_version": "v1",
        "context_packet_version": "v2_alpha" if baseline == "condiag_retry_v2_alpha" else "not_used",
        "failure_witness_version": "v1",
        "api_navigation_version": "v1" if baseline == "condiag_retry_v2_alpha" else "not_used",
        "eval_version": "not_run",
        "retry_runner_version": "host_agent_retry_runner_v0_with_v2alpha_mapping_r0_observability",
        "plan_version": "plan_v1.0_post_validation",
        "forensic_audit_version": "task6_alpha_forensic_v1",
    }


def dry_run_check() -> int:
    """R0: verify direct_llm_patch logic without launching any agent.

    Simulates:
      Case A (completed workspace_git_diff): writes fake attempt_report.json
        with patch_source=workspace_git_diff → assert direct_llm_patch_actual == 'false'
      Case B (timeout, no attempt_report): writes nothing → assert
        direct_llm_patch_actual == 'unknown_timeout_no_attempt_report'
      Case C (agent_submission): writes attempt_report with
        patch_source=agent_submission → assert direct_llm_patch_actual == 'true'

    Does NOT launch agent, does NOT call DEEPSEEK_API_KEY.
    """
    import tempfile
    print("=== Task 6-alpha-R0 dry-run check ===")
    print("Verifying direct_llm_patch logic (no agent launch)...\n")

    results = []

    # Case A: completed workspace_git_diff
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        (run_dir / "attempt_2").mkdir(parents=True)
        (run_dir / "final").mkdir(parents=True)
        (run_dir / "attempt_2" / "raw_trajectory.json").write_text(
            json.dumps({"info": {"exit_status": "EOFError", "submission": "x"}}), encoding="utf-8")
        (run_dir / "attempt_2" / "attempt_report.json").write_text(
            json.dumps({
                "patch_source": "workspace_git_diff",
                "patch_chars": 100,
                "tool_calls_count": 30,
                "valid_protocol": True,
                "changed_files": ["M  foo.py"],
            }), encoding="utf-8")
        (run_dir / "final" / "final_report.json").write_text("{}", encoding="utf-8")
        (run_dir / "run_report.json").write_text(
            json.dumps({"status": "completed", "timeout_seconds": None, "timeout_stage": ""}),
            encoding="utf-8")
        proto = validate_protocol(run_dir)
        ok = proto["direct_llm_patch_actual"] == "false"
        results.append(("Case A: workspace_git_diff",
                        ok,
                        proto["direct_llm_patch_actual"],
                        "expected 'false'"))
        if not ok:
            print(f"  FAIL Case A: direct_llm_patch_actual={proto['direct_llm_patch_actual']!r} (expected 'false')")
            print(f"    reason: {proto['direct_llm_patch_reason']}")
        else:
            print(f"  PASS Case A: direct_llm_patch_actual={proto['direct_llm_patch_actual']!r}")
        # Also check canonical_acceptability
        ok_ca = proto["canonical_acceptability"] == "accepted"
        results.append(("Case A: canonical_acceptability",
                        ok_ca,
                        proto["canonical_acceptability"],
                        "expected 'accepted'"))
        print(f"  {'PASS' if ok_ca else 'FAIL'} Case A: canonical_acceptability={proto['canonical_acceptability']!r}")

    # Case B: timeout, no attempt_report
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        (run_dir / "attempt_2").mkdir(parents=True)
        (run_dir / "run_report.json").write_text(
            json.dumps({
                "status": "aborted",
                "timeout_seconds": 1800,
                "timeout_stage": "subprocess_run",
                "stdout_log_path": "/tmp/x/timeout_stdout.log",
                "stderr_log_path": "/tmp/x/timeout_stderr.log",
                "docker_state_path": "/tmp/x/docker_ps_at_timeout.txt",
            }), encoding="utf-8")
        proto = validate_protocol(run_dir)
        ok = proto["direct_llm_patch_actual"] == "unknown_timeout_no_attempt_report"
        results.append(("Case B: timeout (no attempt_report)",
                        ok,
                        proto["direct_llm_patch_actual"],
                        "expected 'unknown_timeout_no_attempt_report'"))
        if not ok:
            print(f"  FAIL Case B: direct_llm_patch_actual={proto['direct_llm_patch_actual']!r}")
        else:
            print(f"  PASS Case B: direct_llm_patch_actual={proto['direct_llm_patch_actual']!r}")
        # Check timeout fields propagated
        ok_ts = (proto["timeout_seconds"] == 1800
                 and proto["timeout_stage"] == "subprocess_run"
                 and proto["stdout_log_path"] == "/tmp/x/timeout_stdout.log")
        results.append(("Case B: timeout fields propagated",
                        ok_ts,
                        f"ts={proto['timeout_seconds']} stage={proto['timeout_stage']}",
                        "expected ts=1800 stage=subprocess_run"))
        print(f"  {'PASS' if ok_ts else 'FAIL'} Case B: timeout_seconds={proto['timeout_seconds']}, "
              f"timeout_stage={proto['timeout_stage']!r}, stdout_log_path={proto['stdout_log_path']!r}")
        # canonical_acceptability should be pending (not rejected, not accepted)
        ok_ca = proto["canonical_acceptability"] == "pending"
        results.append(("Case B: canonical_acceptability",
                        ok_ca,
                        proto["canonical_acceptability"],
                        "expected 'pending'"))
        print(f"  {'PASS' if ok_ca else 'FAIL'} Case B: canonical_acceptability={proto['canonical_acceptability']!r}")

    # Case C: agent_submission (NOT workspace_git_diff) → direct_llm_patch=true
    with tempfile.TemporaryDirectory() as td:
        run_dir = Path(td)
        (run_dir / "attempt_2").mkdir(parents=True)
        (run_dir / "attempt_2" / "raw_trajectory.json").write_text(
            json.dumps({"info": {"exit_status": "EOFError"}}), encoding="utf-8")
        (run_dir / "attempt_2" / "attempt_report.json").write_text(
            json.dumps({
                "patch_source": "agent_submission",
                "patch_chars": 50,
                "tool_calls_count": 20,
                "valid_protocol": True,
                "changed_files": [],
            }), encoding="utf-8")
        (run_dir / "run_report.json").write_text(
            json.dumps({"status": "completed"}), encoding="utf-8")
        proto = validate_protocol(run_dir)
        ok = proto["direct_llm_patch_actual"] == "true"
        results.append(("Case C: agent_submission",
                        ok,
                        proto["direct_llm_patch_actual"],
                        "expected 'true'"))
        if not ok:
            print(f"  FAIL Case C: direct_llm_patch_actual={proto['direct_llm_patch_actual']!r}")
        else:
            print(f"  PASS Case C: direct_llm_patch_actual={proto['direct_llm_patch_actual']!r}")
        # canonical_acceptability should be rejected (direct_llm_patch=true)
        ok_ca = proto["canonical_acceptability"] == "rejected"
        results.append(("Case C: canonical_acceptability",
                        ok_ca,
                        proto["canonical_acceptability"],
                        "expected 'rejected'"))
        print(f"  {'PASS' if ok_ca else 'FAIL'} Case C: canonical_acceptability={proto['canonical_acceptability']!r}")

    # Summary
    n_pass = sum(1 for _, ok, _, _ in results if ok)
    n_total = len(results)
    print(f"\n=== Dry-run check: {n_pass}/{n_total} PASS ===")
    if n_pass != n_total:
        print("FAILURES:")
        for name, ok, got, expected in results:
            if not ok:
                print(f"  - {name}: got {got!r}, {expected}")
        return 1
    return 0


def main():
    # R0: --dry-run-check mode (no agent, no API key, no packet)
    if "--dry-run-check" in sys.argv:
        return dry_run_check()

    if not V2_ALPHA_PACKET.is_file():
        print(f"ERROR: v2-alpha packet not found at {V2_ALPHA_PACKET}")
        return 1
    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("ERROR: DEEPSEEK_API_KEY not set in env")
        return 1

    print(f"Task 6-alpha protocol smoke — {INSTANCE_ID}")
    print(f"  runs_root: {NEW_RUNS_ROOT}")
    print(f"  baselines: {BASELINES}")
    print(f"  packet:    {V2_ALPHA_PACKET} ({V2_ALPHA_PACKET.stat().st_size} bytes)")

    staged = stage_inputs()
    print(f"\nStaged inputs:")
    for k, v in staged.items():
        print(f"  {k}: {v}")

    rows = []
    for bl in BASELINES:
        try:
            row = run_one_baseline(bl)
        except Exception as e:
            row = {
                "instance_id": INSTANCE_ID,
                "baseline": bl,
                "status": f"exception: {e}",
                "exit_status": "exception",
                "raw_trajectory_exists": "False",
                "tool_calls": 0,
                "patch_source": "",
                "patch_chars": 0,
                "changed_files_count": 0,
                "attempt_report_exists": "False",
                "final_report_exists": "False",
                "valid_protocol": "False",
                "direct_llm_patch_actual": "unknown_exception",
                "direct_llm_patch_reason": f"run_one_baseline raised: {e}",
                "timeout_seconds": "",
                "timeout_stage": "",
                "stdout_log_path": "",
                "stderr_log_path": "",
                "docker_state_path": "",
                "docker_pre_state_path": "",
                "canonical_acceptability": "pending",
                "packet_source": "context_packet_v2_alpha" if bl == "condiag_retry_v2_alpha" else "none",
                "run_dir": "",
                "method_version": "v1",
                "context_packet_version": "v2_alpha" if bl == "condiag_retry_v2_alpha" else "not_used",
                "failure_witness_version": "v1",
                "api_navigation_version": "v1" if bl == "condiag_retry_v2_alpha" else "not_used",
                "eval_version": "not_run",
                "retry_runner_version": "host_agent_retry_runner_v0_with_v2alpha_mapping_r0_observability",
                "plan_version": "plan_v1.0_post_validation",
                "forensic_audit_version": "task6_alpha_forensic_v1",
            }
        rows.append(row)
        print(f"  -> status={row['status']}, tool_calls={row['tool_calls']}, "
              f"patch_source={row['patch_source']}, valid={row['valid_protocol']}, "
              f"direct_llm_patch_actual={row['direct_llm_patch_actual']}, "
              f"canonical={row['canonical_acceptability']}")

    # Write summary CSV
    fieldnames = list(rows[0].keys())
    with open(SUMMARY_CSV, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(fh_row for fh_row in rows)
    print(f"\nWrote protocol matrix: {SUMMARY_CSV}")

    # Summary
    n_valid = sum(1 for r in rows if r["valid_protocol"] == "True")
    n_patch = sum(1 for r in rows if r["patch_source"] == "workspace_git_diff")
    n_accepted = sum(1 for r in rows if r["canonical_acceptability"] == "accepted")
    n_pending = sum(1 for r in rows if r["canonical_acceptability"] == "pending")
    n_rejected = sum(1 for r in rows if r["canonical_acceptability"] == "rejected")
    print(f"\n  protocol valid:     {n_valid}/{len(rows)}")
    print(f"  workspace_git_diff: {n_patch}/{len(rows)}")
    print(f"  canonical accepted: {n_accepted}/{len(rows)}")
    print(f"  canonical pending:  {n_pending}/{len(rows)}")
    print(f"  canonical rejected: {n_rejected}/{len(rows)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
