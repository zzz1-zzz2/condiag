#!/usr/bin/env python3
"""Analyze Verified.csv difficulty distribution."""
import csv
from collections import Counter, defaultdict

PATH = "/home/swelite/condiag/ContextBench/data/Verified.csv"

rows = []
with open(PATH) as f:
    for r in csv.DictReader(f):
        rows.append(r)

print(f"total rows: {len(rows)}")
print()

# Status distribution
status_ct = Counter(r["status"] for r in rows)
print(f"status: {dict(status_ct)}")

# num_agents distribution
num_agents = [int(r["num_agents"]) for r in rows if r["num_agents"].isdigit()]
print(f"num_agents stats: min={min(num_agents)}, max={max(num_agents)}, mean={sum(num_agents)/len(num_agents):.1f}")

# Histogram of num_agents
print("\nnum_agents histogram (how many agents solved it):")
buckets = Counter()
for n in num_agents:
    if n == 0:
        buckets["0 (unsolved)"] += 1
    elif n <= 5:
        buckets["1-5 (hard)"] += 1
    elif n <= 15:
        buckets["6-15 (medium)"] += 1
    elif n <= 30:
        buckets["16-30 (easy)"] += 1
    else:
        buckets["31+ (trivial)"] += 1
for k in ["0 (unsolved)", "1-5 (hard)", "6-15 (medium)", "16-30 (easy)", "31+ (trivial)"]:
    print(f"  {k}: {buckets.get(k, 0)}")

# patch complexity
print("\npatch complexity (proxy for difficulty):")
for fld in ("patch_files", "patch_blocks", "patch_span"):
    vals = [float(r[fld]) for r in rows if r[fld].replace(".", "", 1).isdigit()]
    vals.sort()
    if vals:
        median = vals[len(vals)//2]
        p90 = vals[int(len(vals)*0.9)]
        print(f"  {fld}: median={median}, p90={p90}, max={max(vals)}")

# Cross: failure cases (status != pass or num_agents==0)
fail_rows = [r for r in rows if r["status"] != "pass" or (r["num_agents"].isdigit() and int(r["num_agents"]) == 0)]
print(f"\nfailure rows (status!=pass or num_agents==0): {len(fail_rows)}")

# Top 10 hardest by num_agents
sorted_rows = sorted(rows, key=lambda r: int(r["num_agents"]) if r["num_agents"].isdigit() else 9999)
print(f"\n10 hardest (lowest num_agents):")
for r in sorted_rows[:10]:
    print(f"  num_agents={r['num_agents']:>3}  status={r['status']:>5}  files={r['patch_files']:>3}  blocks={r['patch_blocks']:>3}  span={r['patch_span']:>6}  gold_len={r['gold_context_length']:>6}  {r['original_inst_id']}")

# Top 10 easiest
print(f"\n5 easiest (highest num_agents):")
for r in sorted_rows[-5:]:
    print(f"  num_agents={r['num_agents']:>3}  status={r['status']:>5}  files={r['patch_files']:>3}  blocks={r['patch_blocks']:>3}  span={r['patch_span']:>6}  {r['original_inst_id']}")

# Repo distribution among hard ones
print(f"\nrepo distribution among bottom 30 hardest:")
hard_repos = Counter(r["repo"] if r["repo"] else r["original_inst_id"].rsplit("-", 1)[0].replace("__", "/") for r in sorted_rows[:30])
for repo, ct in hard_repos.most_common(10):
    print(f"  {repo}: {ct}")
