"""Build case_bundles for Batch4 instances from trajectories (standalone)."""
import json
import re
from pathlib import Path

ARTIFACTS = Path("/mnt/d/condiag-artifacts/condiag/v0")
RUNS = Path("/mnt/d/condiag-artifacts/runs/pilot50_batch4_20260707_114055/miniswe/Verified")

INSTANCES = [
    "django__django-11433", "django__django-12262", "django__django-14140",
    "django__django-14787", "django__django-14792", "sympy__sympy-12096",
    "sympy__sympy-12419", "sympy__sympy-13852", "sympy__sympy-23824",
]


def rebuild(inst, run_dir):
    traj_path = run_dir / inst / f"{inst}.traj.json"
    if not traj_path.exists():
        return None

    traj = json.loads(traj_path.read_text())
    msgs = traj.get("messages", [])
    info = traj.get("info", {})
    submission = info.get("submission", "")

    # Parse viewed files/spans from EXPLORE_CONTEXT blocks
    viewed_files_in_order = []
    viewed_spans = {}
    for msg in msgs:
        content = msg.get("content", "")
        pattern = re.compile(
            r"<explore_context>\s*File:\s+/testbed/(.+?)\s*Lines:\s*(\d+)-(\d+)",
            re.IGNORECASE | re.DOTALL,
        )
        for match in pattern.finditer(content):
            path = match.group(1).strip()
            start = int(match.group(2))
            end = int(match.group(3))
            if path not in viewed_files_in_order:
                viewed_files_in_order.append(path)
            viewed_spans.setdefault(path, []).append([start, end])

    # Parse test commands
    test_runs = []
    test_failures = []
    test_patterns = re.compile(
        r"(python\s+-m\s+pytest|python\s+-m\s+unittest|python\s+runtests\.py|pytest|"
        r"python\s+-c\s+.*test|manage\.py\s+test)",
        re.IGNORECASE,
    )
    for i, msg in enumerate(msgs):
        content = msg.get("content", "")
        if msg.get("role") == "assistant" and test_patterns.search(content):
            # Check next user message for test output
            if i + 1 < len(msgs) and msgs[i + 1]["role"] == "user":
                output = msgs[i + 1].get("content", "")
                test_runs.append({"msg_index": i})
                n_fail = 0
                for line in output.split("\n"):
                    m = re.search(r"(\d+)\s+failed", line)
                    if m:
                        n_fail += int(m.group(1))
                if n_fail > 0:
                    test_failures.append({"msg_index": i, "failure_count": n_fail})

    # Parse search commands
    search_commands = []
    for msg in msgs:
        content = msg.get("content", "")
        if msg.get("role") == "assistant":
            cmd_match = re.search(r"```bash\n(.+?)\n```", content, re.DOTALL)
            if cmd_match:
                cmd = cmd_match.group(1).strip()
                if re.match(r"(grep|find|rg|ack|ag|git\s+(log|show|diff|blame))\s", cmd):
                    search_commands.append(cmd[:200])

    # Parse patch
    patch_str = submission if isinstance(submission, str) else ""
    edited_files = []
    for line in patch_str.split("\n"):
        if line.startswith("+++ b/"):
            f = line[6:]
            if f not in edited_files:
                edited_files.append(f)
    added = patch_str.count("\n+") - patch_str.count("\n+++")
    removed = patch_str.count("\n-") - patch_str.count("\n---")

    # Count bash commands
    bash_count = sum(1 for m in msgs if m.get("role") == "assistant" and "```bash\n" in m.get("content", ""))
    n_assistant = sum(1 for m in msgs if m.get("role") == "assistant")
    n_user = sum(1 for m in msgs if m.get("role") == "user")

    model_stats = info.get("model_stats", {})
    api_calls = model_stats.get("api_calls") if isinstance(model_stats, dict) else n_assistant

    # Derived
    viewed_set = set(viewed_files_in_order)
    edited_set = set(edited_files)
    viewed_not_final = [f for f in viewed_files_in_order if f not in edited_set]
    edited_not_viewed = [f for f in edited_files if f not in viewed_set]

    rs = {
        "schema_version": "condiag.runtime_signals.v0.1",
        "instance_id": inst,
        "agent": "mini-swe-agent",
        "exit_status": info.get("exit_status", "unknown"),
        "n_messages": len(msgs),
        "n_assistant_messages": n_assistant,
        "n_user_messages": n_user,
        "api_calls": api_calls or n_assistant,
        "viewed_files_in_order": viewed_files_in_order,
        "viewed_files_count": len(viewed_files_in_order),
        "viewed_spans": viewed_spans,
        "viewed_total_line_bytes": sum(end - start + 1 for spans in viewed_spans.values() for start, end in spans),
        "bash_commands_count": bash_count,
        "search_commands": search_commands,
        "search_commands_count": len(search_commands),
        "test_runs": test_runs,
        "test_runs_count": len(test_runs),
        "test_failures": test_failures,
        "test_failures_count": len(test_failures),
        "edited_files": edited_files,
        "edited_files_count": len(edited_files),
        "edited_hunks_total": len(re.findall(r"^@@ -\d+,\d+ \+\d+,\d+ @@", patch_str, re.MULTILINE)),
        "changed_lines_total": added + removed,
        "changed_lines_added": added,
        "changed_lines_removed": removed,
        "repeated_edit_pattern_detected": False,
        "submitted_without_tests": len(test_runs) == 0,
        "viewed_but_not_final_files": viewed_not_final,
        "viewed_but_not_final_files_count": len(viewed_not_final),
        "edited_but_not_viewed_files": edited_not_viewed,
        "edited_but_not_viewed_files_count": len(edited_not_viewed),
    }
    return rs


def main():
    for inst in INSTANCES:
        print(f"=== {inst} ===")
        rs = rebuild(inst, RUNS)
        if rs is None:
            traj_path = RUNS / inst / f"{inst}.traj.json"
            print(f"  SKIP: {traj_path} not found")
            continue

        out_dir = ARTIFACTS / "case_bundles" / inst
        out_dir.mkdir(parents=True, exist_ok=True)

        (out_dir / "runtime_signals.json").write_text(json.dumps(rs, indent=2))

        traj = json.loads((RUNS / inst / f"{inst}.traj.json").read_text())
        sub = traj["info"].get("submission", "")
        patch_str = sub if isinstance(sub, str) else ""
        (out_dir / "patch.diff").write_text(patch_str)

        print(f"  view={rs['viewed_files_count']} edit={rs['edited_files_count']} test={rs['test_runs_count']} patch={len(patch_str)}B")

    print("\nDone.")


if __name__ == "__main__":
    main()
