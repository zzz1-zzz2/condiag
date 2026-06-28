#!/usr/bin/env python3
"""Inspect a mini-SWE-Agent trajectory and validate Runtime view integrity.

Checks:
1. traj.json top-level structure
2. <EXPLORE_CONTEXT> blocks are present and parseable across the conversation
3. Final <PATCH_CONTEXT> declaration
4. Extract the patch from preds.json
5. Runtime view leakage check: ensure gold-side fields never appeared in agent messages
   (gold_context / patch / test_patch / FAIL_TO_PASS / PASS_TO_PASS)
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TRAJ = Path("/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/miniswe/Verified/"
            "scikit-learn__scikit-learn-25232/scikit-learn__scikit-learn-25232.traj.json")
PREDS = Path("/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/miniswe/Verified/preds.json")
EXIT_YAML = Path("/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/miniswe/Verified/"
                 "exit_statuses_1782446829.5709674.yaml")

print(f"traj exists: {TRAJ.exists()}, size={TRAJ.stat().st_size if TRAJ.exists() else 0}")
print(f"preds exists: {PREDS.exists()}, size={PREDS.stat().st_size if PREDS.exists() else 0}")
print(f"exit exists: {EXIT_YAML.exists()}")
print()

# ---- 1. traj.json top-level structure ----
traj = json.loads(TRAJ.read_text(encoding="utf-8"))
print("=" * 60)
print("1. traj.json top-level keys")
print("=" * 60)
print(list(traj.keys()))
print()

if "info" in traj:
    print("info keys:", list(traj["info"].keys()))
    info = traj["info"]
    for k in ("exit_status", "submission", "submitted", "model_stats", "total_cost"):
        if k in info:
            v = info[k]
            if isinstance(v, (dict, list)):
                print(f"  info.{k} = {json.dumps(v)[:200]}")
            else:
                print(f"  info.{k} = {v!r}")
    print()

# ---- 2. <EXPLORE_CONTEXT> blocks ----
print("=" * 60)
print("2. <EXPLORE_CONTEXT> blocks")
print("=" * 60)

messages = traj.get("messages", [])
print(f"messages: {len(messages)}")

EXPLORE_RE = re.compile(r"<EXPLORE_CONTEXT>(.*?)</EXPLORE_CONTEXT>", re.DOTALL)
explore_count = 0
explore_entries = 0
for m in messages:
    content = m.get("content", "")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str):
        continue
    for match in EXPLORE_RE.finditer(content):
        explore_count += 1
        # Count File: entries
        file_lines = re.findall(r"File:\s*(\S+)", match.group(1))
        explore_entries += len(file_lines)

print(f"explore blocks: {explore_count}")
print(f"explore entries (File: lines): {explore_entries}")
print()

# ---- 3. <PATCH_CONTEXT> final declaration ----
print("=" * 60)
print("3. <PATCH_CONTEXT>")
print("=" * 60)

PATCH_RE = re.compile(r"<PATCH_CONTEXT>(.*?)</PATCH_CONTEXT>", re.DOTALL)
patch_contexts = []
for m in messages:
    content = m.get("content", "")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str):
        continue
    for match in PATCH_RE.finditer(content):
        patch_contexts.append(match.group(1))

print(f"PATCH_CONTEXT declarations: {len(patch_contexts)}")
if patch_contexts:
    last = patch_contexts[-1].strip()
    print(f"last PATCH_CONTEXT ({len(last)} chars):")
    print(last[:600])
print()

# ---- 4. patch from preds.json ----
print("=" * 60)
print("4. preds.json patch")
print("=" * 60)

preds = json.loads(PREDS.read_text(encoding="utf-8"))
print(f"preds type: {type(preds).__name__}")
if isinstance(preds, dict):
    print(f"preds keys: {list(preds.keys())[:10]}")
    instance_key = next(iter(preds.keys())) if preds else None
    if instance_key:
        print(f"instance key: {instance_key!r}")
        v = preds[instance_key]
        if isinstance(v, dict):
            print(f"value keys: {list(v.keys())}")
        elif isinstance(v, str):
            print(f"value (str, len={len(v)}): {v[:300]!r}")
elif isinstance(preds, list):
    print(f"preds len: {len(preds)}")
    if preds:
        print(f"first item keys: {list(preds[0].keys()) if isinstance(preds[0], dict) else type(preds[0]).__name__}")
print()

# ---- 5. Runtime view leakage check ----
print("=" * 60)
print("5. Runtime view leakage check")
print("=" * 60)

LEAK_PATTERNS = [
    ("gold_context", r"\bgold_context\b"),
    ("test_patch",   r"\btest_patch\b"),
    ("FAIL_TO_PASS", r"\bFAIL_TO_PASS\b"),
    ("PASS_TO_PASS", r"\bPASS_TO_PASS\b"),
    ("patch_field",  r"<patch>\s*\n```"),
]
leak_hits = {name: 0 for name, _ in LEAK_PATTERNS}
for m in messages:
    content = m.get("content", "")
    if isinstance(content, list):
        content = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    if not isinstance(content, str):
        continue
    for name, pat in LEAK_PATTERNS:
        leak_hits[name] += len(re.findall(pat, content))

for name, n in leak_hits.items():
    flag = "OK" if n == 0 else "LEAK"
    print(f"  [{flag}] {name}: {n}")

print()
print("inspection complete")
