#!/usr/bin/env python3
"""Extract test_patch, f2p, p2p from the parquet and write as files for the docker run."""
import json
import pathlib
import pyarrow.dataset as ds

PARQUET = "/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet"
TARGET = "SWE-Bench-Verified__python__maintenance__bugfix__27320d49"

OUT = pathlib.Path("/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/official_tests")
OUT.mkdir(parents=True, exist_ok=True)

d = ds.dataset(PARQUET, format="parquet")
t = d.to_table()
rows = t.to_pylist()
for r in rows:
    if r["instance_id"] == TARGET:
        (OUT / "test_patch.diff").write_text(r["test_patch"] or "")
        f2p = json.loads(r["f2p"]) if r.get("f2p") else []
        p2p = json.loads(r["p2p"]) if r.get("p2p") else []
        (OUT / "f2p.json").write_text(json.dumps(f2p, indent=2))
        (OUT / "p2p.json").write_text(json.dumps(p2p, indent=2))
        print(f"f2p count: {len(f2p)}")
        for t in f2p:
            print(f"  {t}")
        print(f"p2p count: {len(p2p)}")
        for t in p2p[:5]:
            print(f"  {t}")
        if len(p2p) > 5:
            print(f"  ... +{len(p2p)-5} more")
        break
