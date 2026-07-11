"""Generate first_failed_rescue_matrix.csv from all available retry results."""
import json, csv
from pathlib import Path

runs = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe')

# Known base eval results from official eval
base_results = {
    'django__django-12125': False,   # base failed
    'sympy__sympy-20428': False,     # base failed
    'django__django-11820': False,   # base failed
    'django__django-16454': False,   # base failed
}

# Known official retry eval results
eval_results = {
    ('django__django-12125', 'feedback_retry'): True,
    ('django__django-12125', 'condiag_retry'): True,
    ('sympy__sympy-20428', 'feedback_retry'): False,
    ('sympy__sympy-20428', 'condiag_retry'): False,
    ('django__django-11820', 'feedback_retry'): 'ERROR',  # patch malformed
    ('django__django-11820', 'condiag_retry'): False,
    ('django__django-16454', 'feedback_retry'): True,
    ('django__django-16454', 'condiag_retry'): False,
}

baselines = ['feedback_retry', 'condiag_retry']
instances = list(base_results.keys())

rows = []
for inst_id in instances:
    for bl in baselines:
        attempt_dir = runs / bl / inst_id / 'attempt_2'
        ar_path = attempt_dir / 'attempt_report.json'
        protocol_valid = False
        patch_source = ''
        patch_chars = 0

        if ar_path.exists():
            ar = json.loads(ar_path.read_text(encoding='utf-8'))
            protocol_valid = ar.get('valid_protocol', False)
            patch_source = ar.get('patch_source', '')
            # Normalize legacy naming
            if patch_source == 'direct_git_diff':
                patch_source = 'workspace_git_diff'
            patch_chars = ar.get('patch_chars', 0)

        # Also try run_report.json
        rr_path = runs / bl / inst_id / 'run_report.json'
        if rr_path.exists():
            rr = json.loads(rr_path.read_text(encoding='utf-8'))
            if not patch_source:
                ps = rr.get('patch_source', '')
                if ps == 'direct_git_diff':
                    ps = 'workspace_git_diff'
                patch_source = ps
            if not patch_chars:
                patch_chars = rr.get('patch_chars', 0)
            if not protocol_valid:
                protocol_valid = rr.get('valid_protocol', False)

        eval_result = eval_results.get((inst_id, bl), '?')
        if eval_result == 'ERROR':
            rescue = 'ERROR'
        elif eval_result is True:
            rescue = True
        elif eval_result is False:
            rescue = False
        else:
            rescue = '?'

        rows.append({
            'instance_id': inst_id,
            'base_resolved': base_results[inst_id],
            'baseline': bl,
            'protocol_valid': protocol_valid,
            'patch_source': patch_source,
            'patch_chars': patch_chars,
            'attempt2_resolved': eval_result,
            'rescue': rescue,
        })

# Write CSV
out = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/first_failed_rescue_matrix.csv')
fields = ['instance_id', 'base_resolved', 'baseline', 'protocol_valid',
          'patch_source', 'patch_chars', 'attempt2_resolved', 'rescue']
with open(out, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    for row in rows:
        w.writerow(row)

print('Wrote %d rows to %s' % (len(rows), out))
print()

# Print table
print('instance_id          | base | baseline         | protocol | patch_source        | patch | resolved | rescue')
print('-' * 105)
for row in rows:
    rescue_str = str(row['rescue'])
    print('%-22s | %-4s | %-16s | %-8s | %-19s | %5s | %-8s | %s' % (
        row['instance_id'],
        str(row['base_resolved']),
        row['baseline'],
        str(row['protocol_valid']),
        row['patch_source'][:18],
        row['patch_chars'],
        str(row['attempt2_resolved']),
        rescue_str,
    ))

# Summary
feedback_rescues = [r for r in rows if r['baseline'] == 'feedback_retry' and r['rescue'] is True]
condiag_rescues = [r for r in rows if r['baseline'] == 'condiag_retry' and r['rescue'] is True]
condiag_unique = [r for r in condiag_rescues if r['instance_id'] not in {x['instance_id'] for x in feedback_rescues}]
print()
print('feedback_retry rescues: %d (%s)' % (len(feedback_rescues), [r['instance_id'] for r in feedback_rescues]))
print('condiag_retry rescues: %d (%s)' % (len(condiag_rescues), [r['instance_id'] for r in condiag_rescues]))
print('condiag unique rescues: %d' % len(condiag_unique))
print('current verdict: feedback=%d, condiag=%d, condiag_unique=%d' % (
    len(feedback_rescues), len(condiag_rescues), len(condiag_unique)))
