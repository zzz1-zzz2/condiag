"""Rebuild runtime_signals.json from trajectory for Dev-5 instances that lost them.

Usage: wsl python3 scripts_tmp/rebuild_runtime_signals.py
"""
import json
import re
from pathlib import Path

ARTIFACTS = Path("/mnt/d/condiag-artifacts/condiag/v0")
RUNS = Path("/mnt/d/condiag-artifacts/runs/pilot50_batch2_20260628_114704/miniswe/Verified")

INSTANCES = [
    "django__django-12125",
    "django__django-13513",
    "sympy__sympy-20428",
]


def _parse_viewed_spans(msgs):
    """Extract viewed_files_in_order and viewed_spans from trajectory messages."""
    viewed_files_in_order = []
    viewed_spans = {}

    for msg in msgs:
        content = msg.get("content", "")
        # Match patterns like:
        # <EXPLORE_CONTEXT>  File: /testbed/path/to/file.py  Lines: 1-50  </EXPLORE_CONTEXT>
        # (case-insensitive, multiline)
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

    return viewed_files_in_order, viewed_spans


def _count_test_commands(msgs):
    """Count test commands and estimate failures."""
    test_commands = []
    test_runs = []
    test_failures = []

    test_patterns = re.compile(
        r"(python\s+-m\s+pytest|python\s+-m\s+unittest|python\s+runtests\.py|pytest|"
        r"python\s+-c\s+.*test|manage\.py\s+test)",
        re.IGNORECASE,
    )
    failure_pattern = re.compile(r"(FAILED|ERROR|failures?|errors?)", re.IGNORECASE)
    success_pattern = re.compile(r"(passed|ok\s*$)", re.IGNORECASE)

    for i, msg in enumerate(msgs):
        content = msg.get("content", "")
        role = msg.get("role", "")

        if role == "assistant" and test_patterns.search(content):
            # Extract the bash command
            cmd_match = re.search(r"```bash\n(.+?)\n```", content, re.DOTALL)
            if cmd_match:
                cmd = cmd_match.group(1).strip()[:200]
                test_commands.append({"msg_index": i, "command": cmd, "output_msg_index": i + 1})

                # Look at the next user message for test output
                if i + 1 < len(msgs) and msgs[i + 1]["role"] == "user":
                    output = msgs[i + 1].get("content", "")

                    # Check for failure indicators
                    n_fail = 0
                    for line in output.split("\n"):
                        if "FAILED" in line or "failed" in line:
                            m = re.search(r"(\d+)\s+failed", line)
                            if m:
                                n_fail += int(m.group(1))
                            else:
                                n_fail += 1

                    if n_fail > 0:
                        test_failures.append(
                            {"msg_index": i, "failure_count": n_fail}
                        )

                    # Count as a test run if there's output
                    if success_pattern.search(output) or failure_pattern.search(output) or len(output) > 50:
                        test_runs.append({"msg_index": i})

    return test_commands, test_runs, test_failures


def _count_search_commands(msgs):
    """Extract search/grep commands."""
    search_commands = []
    for msg in msgs:
        content = msg.get("content", "")
        role = msg.get("role", "")
        if role == "assistant":
            cmd_match = re.search(r"```bash\n(.+?)\n```", content, re.DOTALL)
            if cmd_match:
                cmd = cmd_match.group(1).strip()
                if re.match(r"(grep|find|rg|ack|ag)\s", cmd):
                    search_commands.append(cmd[:200])

                # Also detect git log/show/diff as investigation commands
                if re.match(r"git\s+(log|show|diff|blame)", cmd):
                    search_commands.append(cmd[:200])

    return search_commands


def _parse_patch(submission):
    """Extract patch metrics from submission."""
    if not isinstance(submission, str) or not submission.strip():
        return {"edited_files": [], "edited_files_count": 0, "changed_lines_total": 0, "changed_lines_added": 0, "changed_lines_removed": 0}

    edited_files = []
    for line in submission.split("\n"):
        if line.startswith("+++ b/"):
            f = line[6:]
            if f not in edited_files:
                edited_files.append(f)

    added = submission.count("\n+") - submission.count("\n+++")
    removed = submission.count("\n-") - submission.count("\n---")

    # Count hunks
    hunk_count = len(re.findall(r"^@@ -\d+,\d+ \+\d+,\d+ @@", submission, re.MULTILINE))

    return {
        "edited_files": edited_files,
        "edited_files_count": len(edited_files),
        "edited_hunks_total": hunk_count,
        "changed_lines_total": added + removed,
        "changed_lines_added": added,
        "changed_lines_removed": removed,
    }


def _compute_derived(viewed_files_in_order, edited_files):
    """Compute viewed_but_not_final and edited_but_not_viewed."""
    viewed_set = set(viewed_files_in_order)
    edited_set = set(edited_files) if edited_files else set()

    viewed_not_final = [
        f for f in viewed_files_in_order if f not in edited_set
    ]
    edited_not_viewed = [
        f for f in (edited_files or []) if f not in viewed_set
    ]

    return {
        "viewed_but_not_final_files": viewed_not_final,
        "viewed_but_not_final_files_count": len(viewed_not_final),
        "edited_but_not_viewed_files": edited_not_viewed,
        "edited_but_not_viewed_files_count": len(edited_not_viewed),
    }


def rebuild(inst):
    traj_path = RUNS / inst / f"{inst}.traj.json"
    if not traj_path.exists():
        print(f"  SKIP: {traj_path} not found")
        return None

    traj = json.loads(traj_path.read_text())
    msgs = traj.get("messages", [])
    info = traj.get("info", {})
    submission = info.get("submission", "")

    # Parse viewed files/spans
    viewed_files_in_order, viewed_spans = _parse_viewed_spans(msgs)

    # Parse test commands
    test_commands, test_runs, test_failures = _count_test_commands(msgs)

    # Parse search commands
    search_commands = _count_search_commands(msgs)

    # Parse patch
    patch_info = _parse_patch(submission)

    # Compute derived
    derived = _compute_derived(viewed_files_in_order, patch_info["edited_files"])

    # Count bash commands
    bash_count = 0
    for msg in msgs:
        if msg.get("role") == "assistant":
            if re.search(r"```bash\n", msg.get("content", "")):
                bash_count += 1

    # Count assistant/user messages
    n_assistant = sum(1 for m in msgs if m.get("role") == "assistant")
    n_user = sum(1 for m in msgs if m.get("role") == "user")

    # Estimate api_calls from assistant messages
    model_stats = info.get("model_stats", {})
    api_calls = model_stats.get("api_calls") if isinstance(model_stats, dict) else n_assistant

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
        "viewed_total_line_bytes": sum(
            end - start + 1
            for spans in viewed_spans.values()
            for start, end in spans
        ),
        "bash_commands_count": bash_count,
        "search_commands": search_commands,
        "search_commands_count": len(search_commands),
        "test_commands": test_commands,
        "test_runs": test_runs,
        "test_runs_count": len(test_runs),
        "test_failures": test_failures,
        "test_failures_count": len(test_failures),
        "possible_regression_failures": [],
        "patch_context_files": patch_info["edited_files"][:1],
        "patch_context_files_count": min(1, patch_info["edited_files_count"]),
        "final_patch_context_files": patch_info["edited_files"][:1],
        "final_patch_context_files_count": min(1, patch_info["edited_files_count"]),
        "edited_files": patch_info["edited_files"],
        "edited_files_count": patch_info["edited_files_count"],
        "edited_hunks_total": patch_info["edited_hunks_total"],
        "edited_spans_per_file": {},
        "changed_files": patch_info["edited_files"],
        "changed_files_count": patch_info["edited_files_count"],
        "changed_lines_total": patch_info["changed_lines_total"],
        "changed_lines_added": patch_info["changed_lines_added"],
        "changed_lines_removed": patch_info["changed_lines_removed"],
        "repeated_edit_patterns": [],
        "repeated_edit_pattern_detected": False,
        "submitted_without_tests": len(test_runs) == 0,
        "git_checkout_count": 0,
        "viewed_but_not_final_files": derived["viewed_but_not_final_files"],
        "viewed_but_not_final_files_count": derived["viewed_but_not_final_files_count"],
        "edited_but_not_viewed_files": derived["edited_but_not_viewed_files"],
        "edited_but_not_viewed_files_count": derived["edited_but_not_viewed_files_count"],
        "last_user_messages_tail": [],
        "stack_trace": "",
        "error_tokens": [],
        "error_origin_candidates": [],
        "quality": {},
    }

    return rs


def main():
    for inst in INSTANCES:
        print(f"\n=== {inst} ===")
        rs = rebuild(inst)
        if rs is None:
            continue

        # Save to case_bundles (alongside existing ones)
        out_dir = ARTIFACTS / "case_bundles" / inst
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "runtime_signals.json"
        out_path.write_text(json.dumps(rs, indent=2))
        print(f"  SAVED: {out_path}")
        print(f"  viewed={rs['viewed_files_count']} edited={rs['edited_files_count']} tests={rs['test_runs_count']} failures={rs['test_failures_count']}")

    print("\nDone.")


if __name__ == "__main__":
    main()
