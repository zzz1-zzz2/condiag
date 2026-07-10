"""Debug: check trigger-relevant fields across all 5 instances."""
import json

INSTANCES = [
    ("django__django-11820", "d4_9_batch2_17x4"),
    ("django__django-12125", "batch2_d4_7"),
    ("django__django-13513", "batch2_d4_7"),
    ("django__django-16454", "d4_9_batch2_17x4"),
    ("sympy__sympy-20428", "batch2_d4_7"),
]

FIELDS = [
    "exit_status", "test_failures_count", "test_runs_count", "git_checkout_count",
    "submitted_without_tests", "edited_files_count", "changed_lines_total",
    "repeated_edit_pattern_detected", "viewed_files_count",
    "viewed_but_not_final_files_count", "edited_but_not_viewed_files_count",
    "final_patch_context_files_count", "scope_anomaly_score",
    "test_failures", "api_calls",
]

header = f"{'Field':35s}"
for iid, _ in INSTANCES:
    short = iid.split("__")[1].split("-")[0][:10]
    header += f"{short:>12s}"
print(header)
print("-" * (35 + 12 * 5))

for f in FIELDS:
    line = f"{f:35s}"
    for iid, batch in INSTANCES:
        path = f"/mnt/d/condiag-artifacts/condiag/v0/{batch}/runs/miniswe/base_miniswe/{iid}/attempt_1/runtime_signals.json"
        rs = json.loads(open(path).read())
        val = rs.get(f, "MISSING")
        if isinstance(val, list):
            val = f"[len={len(val)}]"
        elif isinstance(val, dict):
            val = "{dict}"
        elif val is None:
            val = "None"
        s = str(val)[:12]
        line += f"{s:>12s}"
    print(line)
