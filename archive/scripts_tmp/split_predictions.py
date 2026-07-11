"""Split retry predictions into feedback_retry and condiag_retry files."""
import json, os

base = '/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe'
instances = ['django__django-12125', 'sympy__sympy-20428']

for baseline in ['feedback_retry', 'condiag_retry']:
    preds = []
    for inst in instances:
        patch_path = os.path.join(base, baseline, inst, 'attempt_2', 'patch.diff')
        if os.path.isfile(patch_path):
            p = open(patch_path).read()
            model_name = 'miniswe_v4pro_' + baseline
            preds.append({
                'instance_id': inst,
                'model_name_or_path': model_name,
                'model_patch': p,
            })
            print('  %s %s patch_chars=%d' % (inst, baseline, len(p)))
        else:
            print('  %s %s NO PATCH FILE' % (inst, baseline))

    out = '/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/predictions_%s.jsonl' % baseline
    with open(out, 'w') as f:
        for p in preds:
            f.write(json.dumps(p) + '\n')
    print('Wrote %d predictions to %s\n' % (len(preds), out))
