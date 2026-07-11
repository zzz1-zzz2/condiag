"""Generate failed_case_condiag_diagnostics.csv for the 4 unresolved instances."""
import json, csv, os
from pathlib import Path

base = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe')
workspace_base = Path('/home/swelite/condiag/workspaces')
instances = [
    'django__django-12125',
    'sympy__sympy-20428',
    'django__django-11820',
    'django__django-16454',
]

rows = []
for inst_id in instances:
    row = {'instance_id': inst_id}

    # Check workspace repo
    repo_dir = workspace_base / inst_id / 'repo_base'
    row['repo_base_exists'] = 'yes' if repo_dir.is_dir() else 'no'

    # Read intervention_report.json
    ir_path = base / 'condiag_packet_only' / inst_id / 'intervention' / 'intervention_report.json'
    if ir_path.exists():
        ir = json.loads(ir_path.read_text(encoding='utf-8'))
    else:
        ir = {}
    row['intervention_report_exists'] = 'yes' if ir else 'no'

    # Read retry_trigger_result.json for packet_mode and repo_status
    rt_path = base / 'condiag_packet_only' / inst_id / 'intervention' / 'retry_trigger_result.json'
    if rt_path.exists():
        rt = json.loads(rt_path.read_text(encoding='utf-8'))
    else:
        rt = {}
    row['packet_mode'] = rt.get('packet_mode', ir.get('packet_mode', 'unknown'))
    row['repo_status'] = rt.get('repo_status', ir.get('repo_status', 'unknown'))

    # Read executed_actions.json
    ea_path = base / 'condiag_packet_only' / inst_id / 'intervention' / 'executed_actions.json'
    if ea_path.exists():
        ea = json.loads(ea_path.read_text(encoding='utf-8'))
        actions = ea if isinstance(ea, list) else ea.get('actions', ea)
        row['actions_done'] = sum(1 for a in (actions if isinstance(actions, list) else []) if isinstance(a, dict) and a.get('status') in ('done', 'success', 'completed'))
        row['actions_skipped'] = sum(1 for a in (actions if isinstance(actions, list) else []) if isinstance(a, dict) and a.get('status') in ('skipped', 'failed', 'error'))
        row['actions_total'] = len(actions) if isinstance(actions, list) else 0
    else:
        row['actions_done'] = 0
        row['actions_skipped'] = 0
        row['actions_total'] = 0

    # Read selected_evidence.json
    se_path = base / 'condiag_packet_only' / inst_id / 'intervention' / 'selected_evidence.json'
    if se_path.exists():
        se = json.loads(se_path.read_text(encoding='utf-8'))
        if isinstance(se, list):
            row['selected_evidence_count'] = len(se)
        elif isinstance(se, dict):
            evidence_items = se.get('evidence', se.get('items', se.get('selected', [])))
            row['selected_evidence_count'] = len(evidence_items) if isinstance(evidence_items, list) else 0
        else:
            row['selected_evidence_count'] = 0
    else:
        row['selected_evidence_count'] = 0

    # Read context_packet.md size
    cp_path = base / 'condiag_packet_only' / inst_id / 'intervention' / 'context_packet.md'
    if cp_path.exists():
        row['context_packet_chars'] = len(cp_path.read_text(encoding='utf-8'))
    else:
        row['context_packet_chars'] = 0

    # Read recovery_report.json for further detail
    rr_path = base / 'condiag_packet_only' / inst_id / 'intervention' / 'recovery_report.json'
    if rr_path.exists():
        rr = json.loads(rr_path.read_text(encoding='utf-8'))
        row['recovery_status'] = rr.get('status', rr.get('recovery_status', ''))
        row['recovery_actions'] = str(rr.get('actions_count', rr.get('total_actions', '')))
    else:
        row['recovery_status'] = ''
        row['recovery_actions'] = ''

    # Read retry attempt_2 patch.diff size
    patch_path = base / 'condiag_retry' / inst_id / 'attempt_2' / 'patch.diff'
    if patch_path.exists():
        row['condiag_patch_chars'] = len(patch_path.read_text(encoding='utf-8'))
    else:
        row['condiag_patch_chars'] = 0

    # Read feedback retry attempt_2 patch.diff size too
    fb_patch_path = base / 'feedback_retry' / inst_id / 'attempt_2' / 'patch.diff'
    if fb_patch_path.exists():
        row['feedback_patch_chars'] = len(fb_patch_path.read_text(encoding='utf-8'))
    else:
        row['feedback_patch_chars'] = 0

    rows.append(row)

# Write CSV
out_path = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/failed_case_condiag_diagnostics.csv')
fieldnames = [
    'instance_id', 'packet_mode', 'repo_status', 'repo_base_exists',
    'intervention_report_exists',
    'actions_done', 'actions_skipped', 'actions_total',
    'selected_evidence_count', 'context_packet_chars',
    'recovery_status', 'recovery_actions',
    'condiag_patch_chars', 'feedback_patch_chars',
]
with open(out_path, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

print('Wrote %d rows to %s' % (len(rows), out_path))

# Also print as table
print()
print('=' * 100)
print('instance_id          | packet_mode                        | repo_status    | repo_base | actions_done/skipped/total | evidence | cp_chars | condiag_patch | feedback_patch')
print('-' * 100)
for row in rows:
    print('%-22s | %-35s | %-14s | %-9s | %-26s | %-8s | %-8s | %-13s | %-13s' % (
        row['instance_id'],
        row['packet_mode'][:34],
        row['repo_status'][:13],
        row['repo_base_exists'],
        '%s/%s/%s' % (row['actions_done'], row['actions_skipped'], row['actions_total']),
        row['selected_evidence_count'],
        row['context_packet_chars'],
        row['condiag_patch_chars'],
        row['feedback_patch_chars'],
    ))
print('=' * 100)
