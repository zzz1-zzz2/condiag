"""Quick check of predictions.json format."""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/deduped/predictions_Verified.json"
arr = json.load(open(path))
print(f"Total: {len(arr)}")
print(f"Sample 0 keys: {list(arr[0].keys())}")
print(f"Sample iid: {arr[0]['instance_id']}")
print(f"Sample model_name: {arr[0]['model_name_or_path']}")
print(f"Sample patch len: {len(arr[0]['model_patch'])}")
print(f"First 200 chars of patch:")
print(arr[0]['model_patch'][:200])
print(f"\nEmpty patches: {sum(1 for x in arr if not x.get('model_patch','').strip())}")