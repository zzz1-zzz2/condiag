import csv, json
from pathlib import Path
from experiments.run_task6_alpha_protocol_smoke import validate_protocol

RERUN = Path("/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_retry_smoke_rerun")
IID = "django__django-12125"
OUT_CSV = Path("/mnt/d/condiag-artifacts/condiag/v0/task6_alpha_r1_protocol_matrix.csv")

rows = []
for bl in ["broad_expansion", "condiag_retry_v2_alpha"]:
    run_dir = RERUN / "miniswe" / bl / IID
    proto = validate_protocol(run_dir)
    rows.append({
        "instance_id": IID,
        "baseline": bl,
        "status": proto["exit_status"],
        "raw_trajectory_exists": proto["raw_trajectory_exists"],
        "tool_calls": proto["tool_calls"],
        "patch_source": proto["patch_source"],
        "patch_chars": proto["patch_chars"],
        "changed_files_count": len(proto["changed_files"]),
        "valid_protocol": proto["valid_protocol"],
        "timeout_seconds": proto["timeout_seconds"] or "",
        "timeout_stage": proto["timeout_stage"] or "",
        "stdout_log_path": proto["stdout_log_path"] or "",
        "stderr_log_path": proto["stderr_log_path"] or "",
        "docker_state_path": proto["docker_state_path"] or "",
        "docker_pre_state_path": proto["docker_pre_state_path"] or "",
        "direct_llm_patch_actual": proto["direct_llm_patch_actual"],
        "direct_llm_patch_reason": proto["direct_llm_patch_reason"],
        "canonical_acceptability": proto["canonical_acceptability"],
        "packet_source": "context_packet_v2_alpha" if bl == "condiag_retry_v2_alpha" else "v0_intervention",
        "run_dir": str(run_dir),
        "method_version": "v1",
        "context_packet_version": "v2_alpha" if bl == "condiag_retry_v2_alpha" else "not_used",
        "failure_witness_version": "v1",
        "api_navigation_version": "v1" if bl == "condiag_retry_v2_alpha" else "not_used",
        "eval_version": "not_run",
        "retry_runner_version": "host_agent_retry_runner_v0_with_v2alpha_mapping_r0_observability",
        "plan_version": "plan_v1.0_post_validation",
        "forensic_audit_version": "task6_alpha_forensic_v1",
    })

with open(OUT_CSV, "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader()
    w.writerows(rows)

print(f"Wrote {OUT_CSV}")
for r in rows:
    bl = r["baseline"]
    st = r["status"]
    ps = r["patch_source"]
    dl = r["direct_llm_patch_actual"]
    ca = r["canonical_acceptability"]
    tc = r["tool_calls"]
    print(f"  {bl:30s} status={st:15s} tool_calls={tc:3d} patch_source={ps:25s} direct_llm_patch_actual={dl:45s} canonical={ca}")
