"""Generate predictions.jsonl for batch1 retry attempt_2 patches."""
import json, os

base = '/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe'

pairs = [
    ('django__django-11820', 'feedback_retry'),
    ('django__django-11820', 'condiag_retry'),
    ('django__django-16454', 'feedback_retry'),
    ('django__django-16454', 'condiag_retry'),
]

preds = []
for inst, bl in pairs:
    patch_path = os.path.join(base, bl, inst, 'attempt_2', 'patch.diff')
    if os.path.isfile(patch_path):
        p = open(patch_path).read()
        model_name = "miniswe_v4pro_" + bl
        preds.append({
            "instance_id": inst,
            "model_name_or_path": model_name,
            "model_patch": p,
        })
        print("  %s %s patch_chars=%d" % (inst, bl, len(p)))
    else:
        print("  %s %s NO PATCH FILE" % (inst, bl))

out = '/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/predictions_batch1_retry.jsonl'
with open(out, 'w') as f:
    for p in preds:
        f.write(json.dumps(p) + '\n')

print('\nWrote %d predictions to %s' % (len(preds), out))

# Also split by baseline
for baseline in ['feedback_retry', 'condiag_retry']:
    bp = [p for p in preds if p['model_name_or_path'] == 'miniswe_v4pro_' + baseline]
    out2 = '/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/predictions_batch1_%s.jsonl' % baseline
    with open(out2, 'w') as f:
        for p in bp:
            f.write(json.dumps(p) + '\n')
    print('Wrote %d predictions to %s' % (len(bp), out2))
