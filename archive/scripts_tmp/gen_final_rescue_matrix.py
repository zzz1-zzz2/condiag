"""Generate final rescue matrix with all v2 condiag results."""
import json, csv
from pathlib import Path

runs = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe')

data = [
    # (instance_id, baseline, base_resolved, attempt2_resolved, notes)
    ('django__django-12125', 'feedback_retry', False, True, ''),
    ('django__django-12125', 'condiag_retry', False, True, ''),
    ('sympy__sympy-20428', 'feedback_retry', False, False, ''),
    ('sympy__sympy-20428', 'condiag_retry', False, False, ''),
    ('django__django-11820', 'feedback_retry', False, 'ERROR', 'patch malformed (extra test_sqlite_settings.py)'),
    ('django__django-11820', 'condiag_retry', False, False, 'v2 repo-backed retrieval (497 chars)'),
    ('django__django-16454', 'feedback_retry', False, True, ''),
    ('django__django-16454', 'condiag_retry', False, False, 'v2 repo-backed retrieval (1240 chars)'),
]

rows = []
for inst_id, bl, base_res, attempt2_res, notes in data:
    attempt_dir = runs / bl / inst_id / 'attempt_2'
    ar_path = attempt_dir / 'attempt_report.json'
    protocol_valid = False
    patch_source = ''
    patch_chars = 0

    if ar_path.exists():
        ar = json.loads(ar_path.read_text(encoding='utf-8'))
        protocol_valid = ar.get('valid_protocol', False)
        patch_source = ar.get('patch_source', '')
        if patch_source == 'direct_git_diff':
            patch_source = 'workspace_git_diff'
        patch_chars = ar.get('patch_chars', 0)

    rescue = None
    if attempt2_res == 'ERROR':
        rescue = 'ERROR'
    elif base_res == False and attempt2_res == True:
        rescue = True
    else:
        rescue = False

    rows.append({
        'instance_id': inst_id,
        'base_resolved': base_res,
        'baseline': bl,
        'protocol_valid': protocol_valid,
        'patch_source': patch_source,
        'patch_chars': patch_chars,
        'attempt2_resolved': attempt2_res,
        'rescue': rescue,
        'notes': notes,
    })

out = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/first_failed_rescue_matrix.csv')
fields = ['instance_id', 'base_resolved', 'baseline', 'protocol_valid',
          'patch_source', 'patch_chars', 'attempt2_resolved', 'rescue', 'notes']
with open(out, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
    w.writeheader()
    for row in rows:
        w.writerow(row)

print('=== Final Rescue Matrix ===')
print()
print('instance          | base | baseline    | protocol | patch_source       | patch | resolved | rescue  | notes')
print('-' * 110)
for row in rows:
    rescue_str = str(row['rescue'])
    print('%-18s | %-4s | %-11s | %-8s | %-18s | %5s | %-8s | %-7s | %s' % (
        row['instance_id'],
        str(row['base_resolved']),
        row['baseline'],
        str(row['protocol_valid']),
        row['patch_source'][:17],
        row['patch_chars'],
        str(row['attempt2_resolved']),
        rescue_str,
        row['notes'],
    ))

feedback_rescues = [r for r in rows if r['baseline'] == 'feedback_retry' and r['rescue'] is True]
condiag_rescues = [r for r in rows if r['baseline'] == 'condiag_retry' and r['rescue'] is True]
condiag_unique = [r for r in condiag_rescues if r['instance_id'] not in {x['instance_id'] for x in feedback_rescues}]

print()
print('=== Summary ===')
print('feedback_retry rescues: %d (%s)' % (len(feedback_rescues), [r['instance_id'] for r in feedback_rescues]))
print('condiag_retry rescues:  %d (%s)' % (len(condiag_rescues), [r['instance_id'] for r in condiag_rescues]))
print('condiag unique rescues: %d' % len(condiag_unique))
print()
print('Verdict: feedback=%d, condiag=%d, condiag_unique=%d' % (
    len(feedback_rescues), len(condiag_rescues), len(condiag_unique)))
print()
if len(condiag_unique) == 0:
    print('No ConDiag unique rescue observed.')
    print('django-16454: feedback rescued, condiag (with full retrieval) did NOT.')
    print('django-11820: feedback patch malformed, condiag (with full retrieval) did NOT rescue.')
    print()
    print('Conclusion: repo-backed ConDiag retrieval, while technically functioning,')
    print('has not yet translated into unique repair-rate gains over feedback_retry.')

print('\nWrote %d rows to %s' % (len(rows), out))
