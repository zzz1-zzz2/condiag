#!/usr/bin/env python3
"""Search all parquet/csv files in data/ for our instance."""
import os
import json
import pyarrow.dataset as ds

DATA = "/home/swelite/condiag/ContextBench/data"
TARGET = "scikit-learn__scikit-learn-25232"

for name in sorted(os.listdir(DATA)):
    p = os.path.join(DATA, name)
    if not os.path.isfile(p):
        continue
    ext = name.split(".")[-1].lower()
    print(f"--- {name} ({os.path.getsize(p)} bytes) ---")
    if ext == "parquet":
        try:
            d = ds.dataset(p, format="parquet")
            t = d.to_table(columns=None)
            cols = t.column_names
            id_col = None
            for cand in ("instance_id", "original_inst_id", "inst_id"):
                if cand in cols:
                    id_col = cand
                    break
            if not id_col:
                print(f"  no id column, cols={cols}")
                continue
            ids = t.column(id_col).to_pylist()
            print(f"  rows={len(ids)}, id_col={id_col}")
            if TARGET in ids:
                idx = ids.index(TARGET)
                print(f"  FOUND at index {idx}")
            else:
                # try substring match
                hits = [i for i, x in enumerate(ids) if isinstance(x, str) and TARGET in x]
                if hits:
                    print(f"  substring matches at indices: {hits[:5]}")
        except Exception as e:
            print(f"  ERROR: {e}")
    elif ext in ("csv", "tsv"):
        try:
            import csv
            with open(p, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                rows = list(reader)
            if not rows:
                continue
            header = rows[0]
            id_col_idx = None
            for cand in ("instance_id", "original_inst_id", "inst_id"):
                if cand in header:
                    id_col_idx = header.index(cand)
                    break
            if id_col_idx is None:
                print(f"  rows={len(rows)-1}, no id column, header={header}")
                continue
            ids = [r[id_col_idx] for r in rows[1:]]
            print(f"  rows={len(ids)}, id_col={header[id_col_idx]}")
            if TARGET in ids:
                print(f"  FOUND at row {ids.index(TARGET)+1}")
        except Exception as e:
            print(f"  ERROR: {e}")
