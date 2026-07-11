import pyarrow as pa
import pyarrow.ipc as ipc

arrow_path = '/mnt/d/condiag-artifacts/cache/hf/datasets/princeton-nlp___swe-bench_verified/default/0.0.0/c104f840cc67f8b6eec6f759ebc8b2693d585d4a/swe-bench_verified-test.arrow'

with open(arrow_path, 'rb') as f:
    reader = ipc.open_stream(f)
    table = reader.read_all()

df = table.to_pandas()
print('total:', len(df))
print('cols:', list(df.columns))
print()

for iid in ['django__django-10880', 'django__django-11099']:
    row = df[df.instance_id == iid]
    if len(row):
        r = row.iloc[0]
        print(f"{iid}:")
        print(f"  repo        = {r.get('repo')}")
        print(f"  base_commit = {r.get('base_commit')}")
        print(f"  version     = {r.get('version')}")
        print()
    else:
        print(f"{iid}: NOT FOUND")
