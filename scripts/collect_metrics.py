#!/usr/bin/env python3
"""Compile M0 run metrics for the case card."""
import json
import pathlib
import re

RUN = pathlib.Path("/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49")
TRAJ = RUN / "miniswe/Verified/scikit-learn__scikit-learn-25232/scikit-learn__scikit-learn-25232.traj.json"
PREDS = RUN / "miniswe/Verified/preds.json"
EVAL = RUN / "eval_results.json"

traj = json.loads(TRAJ.read_text())
info = traj["info"]
messages = traj["messages"]

# Step count
n_messages = len(messages)
n_user = sum(1 for m in messages if m.get("role") == "user")
n_assistant = sum(1 for m in messages if m.get("role") == "assistant")
n_tool = sum(1 for m in messages if m.get("role") == "tool")

# Trajectory_format
print(f"trajectory_format: {traj.get('trajectory_format')}")
print(f"instance_id: {traj.get('instance_id')}")
print(f"exit_status: {info.get('exit_status')}")
print(f"submission bytes: {len(info.get('submission', ''))}")
print(f"mini_version: {info.get('mini_version')}")
print(f"model_stats: {info.get('model_stats')}")
print(f"messages: total={n_messages}, user={n_user}, assistant={n_assistant}, tool={n_tool}")
print()

# Count <EXPLORE_CONTEXT> blocks and entries
EXPLORE_RE = re.compile(r"<EXPLORE_CONTEXT>(.*?)</EXPLORE_CONTEXT>", re.DOTALL)
PATCH_RE = re.compile(r"<PATCH_CONTEXT>(.*?)</PATCH_CONTEXT>", re.DOTALL)
explore_blocks = 0
explore_entries = 0
patch_decls = 0
for m in messages:
    c = m.get("content", "")
    if isinstance(c, list):
        c = " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    if not isinstance(c, str):
        continue
    for match in EXPLORE_RE.finditer(c):
        explore_blocks += 1
        explore_entries += len(re.findall(r"File:\s*(\S+)", match.group(1)))
    patch_decls += len(PATCH_RE.findall(c))

print(f"explore_blocks: {explore_blocks}, explore_entries: {explore_entries}")
print(f"patch_context_decls: {patch_decls}")
print()

# Token usage
total_input = total_output = 0
for m in messages:
    if isinstance(m.get("content"), list):
        for b in m["content"]:
            if isinstance(b, dict) and "usage" in b:
                u = b["usage"]
                if isinstance(u, dict):
                    total_input += int(u.get("input_tokens", u.get("input", 0)))
                    total_output += int(u.get("output_tokens", u.get("output", 0)))

print(f"token usage from content blocks: input={total_input}, output={total_output}")

# Eval metrics
ev = json.loads(EVAL.read_text())
f = ev["final"]
t = ev["trajectory"]
print()
print("=== FINAL METRICS ===")
for g in ("file", "symbol", "span", "line"):
    fm = f[g]
    auc = t["auc_coverage"].get(g, 0)
    red = t["redundancy"].get(g, 0)
    print(f"  {g:8s} Cov={fm['coverage']:.3f} Prec={fm['precision']:.3f}  AUC={auc:.3f} Redund={red:.3f}")
print(f"  editloc recall={ev['editloc']['recall']:.3f} precision={ev['editloc']['precision']:.3f}")
print(f"  num_steps={ev['num_steps']}")

# Write a compact summary file
summary = {
    "instance_id": traj.get("instance_id"),
    "contextbench_id": "SWE-Bench-Verified__python__maintenance__bugfix__27320d49",
    "exit_status": info.get("exit_status"),
    "api_calls": info.get("model_stats", {}).get("api_calls"),
    "instance_cost": info.get("model_stats", {}).get("instance_cost"),
    "messages_total": n_messages,
    "messages_user": n_user,
    "messages_assistant": n_assistant,
    "messages_tool": n_tool,
    "explore_blocks": explore_blocks,
    "explore_entries": explore_entries,
    "patch_context_declarations": patch_decls,
    "final": f,
    "trajectory_auc": t["auc_coverage"],
    "trajectory_redundancy": t["redundancy"],
    "editloc": ev["editloc"],
    "num_steps": ev["num_steps"],
    "patch_bytes": len(info.get("submission", "")),
}
out = RUN / "case_card_metrics.json"
out.write_text(json.dumps(summary, indent=2))
print(f"\nWrote: {out}")
