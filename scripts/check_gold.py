#!/usr/bin/env python3
"""Check that our instance is in the verified parquet and inspect gold fields."""
import json
import sys
import pyarrow.dataset as ds

PARQUET = "/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet"
TARGET = "scikit-learn__scikit-learn-25232"

d = ds.dataset(PARQUET, format="parquet")
t = d.to_table()
print(f"total rows: {t.num_rows}")
print(f"columns: {t.column_names}")

ids = t.column("instance_id").to_pylist()
print(f"target {TARGET!r} present: {TARGET in ids}")

if TARGET in ids:
    idx = ids.index(TARGET)
    row = {k: t.column(k)[idx].as_py() for k in t.column_names}
    print()
    print(f"row keys: {list(row.keys())}")
    for k in ("instance_id", "original_inst_id", "repo", "repo_url", "base_commit", "source", "language"):
        v = row.get(k)
        if isinstance(v, str) and len(v) > 100:
            v = v[:100] + "..."
        print(f"  {k}: {v!r}")

    gc_raw = row.get("gold_context")
    if isinstance(gc_raw, str):
        try:
            gc = json.loads(gc_raw)
            print()
            print(f"gold_context: type={type(gc).__name__}, entries={len(gc) if isinstance(gc, list) else 'n/a'}")
            if isinstance(gc, list) and gc:
                print(f"  first entry: {gc[0]}")
                files = sorted(set(e.get("file", "") for e in gc if isinstance(e, dict)))
                print(f"  files ({len(files)}): {files}")
        except Exception as e:
            print(f"  parse error: {e}")
