"""Add batch1 unresolved instances to manifest."""
import csv, json
from pathlib import Path

batch1 = Path('/mnt/d/condiag-artifacts/runs/pilot50_batch1_20260627_234801/miniswe/Verified')
manifest_path = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/manifest.csv')

instances = {
    'django__django-11820': 'c2678e49759e28a2e16db2939b8a56e1c87c96c7',
    'django__django-16454': '1250483ebf737381ab320fb99e766dc43ce40d59',
}

# Read existing
with open(manifest_path, 'r') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    existing_rows = list(reader)
existing_ids = {r['instance_id'] for r in existing_rows}

# Add new
new_rows = []
for inst_id, base_commit in instances.items():
    if inst_id in existing_ids:
        print('%s: already in manifest, skipping' % inst_id)
        continue

    traj_path = batch1 / inst_id / (inst_id + '.traj.json')
    traj = json.loads(traj_path.read_text(encoding='utf-8'))
    info = traj.get('info', {}) or {}
    model_stats = info.get('model_stats', {}) or {}

    workspace = Path('/home/swelite/condiag/workspaces') / inst_id / 'repo_base'
    repo_ready = 'yes' if workspace.is_dir() else 'no'

    # Get exit_status from info (mini-SWE trajectory format)
    exit_status = info.get('exit_status', '')
    if not exit_status:
        # fallback: check messages for submission
        msgs = traj.get('messages', [])
        for m in reversed(msgs):
            if 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT' in str(m.get('content', '')):
                exit_status = 'Submitted'
                break

    row = {
        'instance_id': inst_id,
        'traj_path': str(traj_path),
        'run_dir': str(batch1 / inst_id),
        'source_batch': 'batch1_20260627_234801',
        'agent': 'miniswe',
        'model': 'deepseek/deepseek-v4-pro',
        'exit_status': exit_status,
        'api_calls': str(model_stats.get('api_calls', len(traj.get('messages', [])) // 2)),
        'wall_time_seconds': '',
        'repo_base_path': str(workspace) if repo_ready == 'yes' else '',
        'base_commit': base_commit,
        'repo_ready': repo_ready,
    }
    new_rows.append(row)
    print('%s: added (repo_ready=%s, exit=%s)' % (inst_id, repo_ready, exit_status))

# Write
if new_rows:
    with open(manifest_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in existing_rows:
            writer.writerow(row)
        for row in new_rows:
            writer.writerow(row)
    print('Manifest: %d existing + %d new' % (len(existing_rows), len(new_rows)))
else:
    print('No new rows to add')
