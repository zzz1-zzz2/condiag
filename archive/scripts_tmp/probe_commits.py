import pandas as pd
df = pd.read_parquet('/home/swelite/condiag/ContextBench/agent-frameworks/data/Verified/data/test-00000-of-00001.parquet')
for iid in ['django__django-10880', 'django__django-11099', 'astropy__astropy-14995', 'sympy__sympy-16597']:
    row = df[df.instance_id == iid]
    if len(row):
        r = row.iloc[0]
        print(f"{iid}:")
        print(f"  repo        = {r.get('repo')}")
        print(f"  base_commit = {r.get('base_commit')}")
        print()
