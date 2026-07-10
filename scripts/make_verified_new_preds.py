"""Compute eval plan: which 51 Verified instances to eval.
The canonical 19 are already eval'd — but they have old eval results, so re-running is also valid (verification).
Run only the 32 NEW ones to save time."""
import json, csv

PREDS = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/deduped/predictions_Verified.json"
CANON = "/mnt/d/condiag-artifacts/condiag/v0/canonical_base_eval_matrix.csv"

# Load preds
preds = json.load(open(PREDS))
pred_iids = {p['instance_id']: p for p in preds}
print(f"Predictions: {len(preds)}")

# Load canonical
canon = {}
with open(CANON) as f:
    r = csv.DictReader(f)
    for row in r:
        canon[row['instance_id']] = row
print(f"Canonical: {len(canon)}")

already = set(canon) & set(pred_iids)
need_eval = sorted(set(pred_iids) - set(canon))
already_in_preds = sorted(already)

print(f"\nAlready eval'd (canonical, also in preds): {len(already)}")
for i in already_in_preds:
    print(f"  {i}: {canon[i]['eval_status']} (resolved={canon[i].get('base_resolved')})")

print(f"\nNeed eval (NEW): {len(need_eval)}")
for i in need_eval:
    print(f"  {i}")

# Write a predictions file with only NEW instances
new_preds = [pred_iids[i] for i in need_eval]
out = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/deduped/predictions_Verified_NEW32.json"
json.dump(new_preds, open(out, 'w'), indent=2)
print(f"\nWrote: {out} ({len(new_preds)} instances)")

# Write a JSONL for harness
out_jsonl = "/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/deduped/predictions_Verified_NEW32.jsonl"
with open(out_jsonl, 'w') as f:
    for p in new_preds:
        f.write(json.dumps(p) + '\n')
print(f"Wrote: {out_jsonl}")