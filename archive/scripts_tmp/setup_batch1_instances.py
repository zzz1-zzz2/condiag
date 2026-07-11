"""Set up manifest + base_miniswe artifacts for batch1 unresolved instances."""
import json, csv, shutil
from pathlib import Path

batch1 = Path('/mnt/d/condiag-artifacts/runs/pilot50_batch1_20260627_234801/miniswe/Verified')
out_base = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe')
manifest_path = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/manifest.csv')
workspace_base = Path('/home/swelite/condiag/workspaces')

instances = {
    'django__django-11820': {
        'base_commit': 'c2678e49759e28a2e16db2939b8a56e1c87c96c7',
    },
    'django__django-16454': {
        'base_commit': '1250483ebf737381ab320fb99e766dc43ce40d59',
    },
}

# Collect existing manifest entries
existing_ids = set()
with open(manifest_path, 'r') as f:
    reader = csv.DictReader(f)
    existing_rows = list(reader)
    for row in existing_rows:
        existing_ids.add(row['instance_id'])

# Add new entries
new_rows = []
for inst_id, meta in instances.items():
    if inst_id in existing_ids:
        print('%s: already in manifest' % inst_id)
        continue

    traj_path = batch1 / inst_id / (inst_id + '.traj.json')
    traj = json.loads(traj_path.read_text(encoding='utf-8'))
    info = traj.get('info', {}) or {}
    model_stats = info.get('model_stats', {}) or {}

    # Check if workspace exists
    repo_dir = workspace_base / inst_id / 'repo_base'
    repo_ready = 'yes' if repo_dir.is_dir() else 'no'

    row = {
        'instance_id': inst_id,
        'traj_path': str(traj_path),
        'run_dir': str(batch1 / inst_id),
        'source_batch': 'batch1_20260627_234801',
        'agent': 'miniswe',
        'model': 'deepseek/deepseek-v4-pro',
        'exit_status': info.get('exit_status', ''),
        'api_calls': str(model_stats.get('api_calls', 0)),
        'wall_time_seconds': '',
        'repo_base_path': str(repo_dir) if repo_ready == 'yes' else '',
        'base_commit': meta['base_commit'],
        'repo_ready': repo_ready,
    }
    new_rows.append(row)
    print('%s: added to manifest (repo_ready=%s)' % (inst_id, repo_ready))

    # Set up base_miniswe/attempt_1
    attempt_1 = out_base / 'base_miniswe' / inst_id / 'attempt_1'
    attempt_1.mkdir(parents=True, exist_ok=True)

    # Write patch.diff from submission
    submission = info.get('submission', '')
    if submission:
        (attempt_1 / 'patch.diff').write_text(submission, encoding='utf-8')
        print('  patch.diff: %d chars' % len(submission))
    else:
        (attempt_1 / 'patch.diff').write_text('', encoding='utf-8')
        print('  patch.diff: empty (no submission)')

    # Write runtime_signals.json (minimal from trajectory)
    rs = {
        'exit_status': info.get('exit_status', ''),
        'instance_id': inst_id,
        'agent': 'miniswe',
        'submission_chars': len(submission),
        'has_patch': len(submission) > 0,
    }
    (attempt_1 / 'runtime_signals.json').write_text(
        json.dumps(rs, indent=2, ensure_ascii=False), encoding='utf-8')
    print('  runtime_signals.json written')

# Append new rows to manifest
if new_rows:
    fieldnames = list(existing_rows[0].keys()) if existing_rows else list(new_rows[0].keys())
    with open(manifest_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in existing_rows:
            writer.writerow(row)
        for row in new_rows:
            writer.writerow(row)
    print('\nManifest updated: %d existing + %d new = %d rows' % (
        len(existing_rows), len(new_rows), len(existing_rows) + len(new_rows)))
