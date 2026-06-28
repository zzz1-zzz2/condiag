#!/usr/bin/env python3
"""Find scikit-learn instances in contextbench_verified.parquet suitable for M0 re-run."""
import json
import pyarrow.dataset as ds

PARQUET = "/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet"

d = ds.dataset(PARQUET, format="parquet")
t = d.to_table()
rows = t.to_pylist()
print(f"total: {len(rows)}")

# Group by repo
from collections import defaultdict
by_repo = defaultdict(list)
for r in rows:
    by_repo[r.get("repo", "")].append(r)

# Print scikit-learn instances
sk = by_repo.get("scikit-learn/scikit-learn", [])
print(f"\nsklearn instances: {len(sk)}")
for r in sk[:15]:
    gc_raw = r.get("gold_context", "")
    try:
        gc = json.loads(gc_raw) if isinstance(gc_raw, str) else []
        n_files = len(set(e.get("file", "") for e in gc if isinstance(e, list) and e))
        if isinstance(gc, dict):
            n_files = len(gc)
    except Exception:
        n_files = -1

    ps = r.get("problem_statement", "")
    print(f"  - {r['instance_id']}")
    print(f"      base_commit: {r.get('base_commit', '')[:12]}")
    print(f"      language: {r.get('language')}")
    print(f"      problem_statement: {ps[:120]!r}")

# Also show repo distribution
print("\nrepo distribution (top 15):")
for repo, items in sorted(by_repo.items(), key=lambda x: -len(x[1]))[:15]:
    print(f"  {repo}: {len(items)}")
