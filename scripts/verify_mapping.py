#!/usr/bin/env python3
"""Verify that scikit-learn__scikit-learn-25232 == SWE-Bench-Verified__python__maintenance__bugfix__27320d49."""
import json
import pyarrow.dataset as ds

PARQUET = "/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet"
TARGET_CB = "SWE-Bench-Verified__python__maintenance__bugfix__27320d49"

d = ds.dataset(PARQUET, format="parquet")
t = d.to_table()
rows = t.to_pylist()

for r in rows:
    if r["instance_id"] == TARGET_CB:
        print(f"instance_id: {r['instance_id']}")
        print(f"original_inst_id: {r['original_inst_id']}")
        print(f"repo: {r['repo']}")
        print(f"repo_url: {r['repo_url']}")
        print(f"base_commit: {r['base_commit']}")
        print(f"source: {r['source']}")
        print(f"language: {r['language']}")
        print()
        gc_raw = r['gold_context']
        print(f"gold_context (raw, first 500 chars): {gc_raw[:500]!r}")
        print()
        gc = json.loads(gc_raw) if isinstance(gc_raw, str) else []
        print(f"gold_context type: {type(gc).__name__}")
        if isinstance(gc, list):
            print(f"entries: {len(gc)}")
            print(f"first 3 entries:")
            for e in gc[:3]:
                print(f"  {e}")
            files = sorted(set(e.get('file', '') for e in gc if isinstance(e, dict)))
            print(f"files ({len(files)}):")
            for f in files:
                print(f"  {f}")
        print()
        print(f"patch (first 400 chars):")
        print(r['patch'][:400])
        print()
        print(f"test_patch (first 400 chars):")
        print(r['test_patch'][:400])
        print()
        print(f"f2p: {r['f2p'][:200]}")
        print()
        print(f"p2p: {r['p2p'][:200]}")
        break
