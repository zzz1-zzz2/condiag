import json

preds_path = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/contextbench_input/preds_all.jsonl"
with open(preds_path) as f:
    lines = f.readlines()
for i, line in enumerate(lines):
    d = json.loads(line)
    iid = d["instance_id"][:75]
    print(f"[{i+1:3d}] {iid}")
print(f"\nTotal: {len(lines)} instances")
