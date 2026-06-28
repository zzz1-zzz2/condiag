#!/usr/bin/env python3
"""Inspect a single ContextBench task instance in detail.

Usage: inspect_task.py <instance_id_substr>
"""
import sys
import json
import pandas as pd


def main(substr):
    df = pd.read_parquet("/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet")
    mask = df["instance_id"].str.contains(substr, na=False) | df["original_inst_id"].str.contains(substr, na=False)
    rows = df[mask]
    if len(rows) == 0:
        print(f"no match for {substr!r}")
        return
    r = rows.iloc[0]
    print(f"matched {len(rows)} row(s); showing first")
    print("=" * 60)
    for col in df.columns:
        v = r[col]
        if isinstance(v, str) and len(v) > 400:
            print(f"\n--- {col} (str, len={len(v)}) ---")
            print(v[:400] + "...(truncated)")
        elif isinstance(v, str) and (v.startswith("[") or v.startswith("{")):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    print(f"\n--- {col} (JSON list, len={len(parsed)}) ---")
                    if parsed:
                        print(f"first item keys: {list(parsed[0].keys())}")
                        print(f"first item: {json.dumps(parsed[0], ensure_ascii=False)[:400]}")
                else:
                    print(f"\n--- {col} (JSON dict, keys={list(parsed.keys())}) ---")
                    print(json.dumps(parsed, ensure_ascii=False)[:400])
            except Exception:
                print(f"{col}: {v[:200]}")
        else:
            print(f"{col}: {v!r}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "27320d49")
