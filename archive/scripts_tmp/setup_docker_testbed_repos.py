"""Export Docker /testbed as repo_base for batch1 instances + record mismatch."""
import json, csv, subprocess, shutil
from pathlib import Path
from datetime import datetime, timezone

WORKSPACE_ROOT = Path('/home/swelite/condiag/workspaces')
MANIFEST_PATH = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/manifest.csv')
REPORT_DIR = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4')

INSTANCES = {
    'django__django-11820': {
        'container': 'tmp-django-11820',
        'manifest_base_commit': 'c2678e49759e28a2e16db2939b8a56e1c87c96c7',
    },
    'django__django-16454': {
        'container': 'tmp-django-16454',
        'manifest_base_commit': '1250483ebf737381ab320fb99e766dc43ce40d59',
    },
}

def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)

reports = []

for inst_id, cfg in INSTANCES.items():
    print(f'\n=== {inst_id} ===')
    container = cfg['container']
    manifest_commit = cfg['manifest_base_commit']

    # 1. Get docker testbed metadata
    head = run(f'docker exec {container} bash -c "cd /testbed && git rev-parse HEAD"')
    docker_head = head.stdout.strip()
    short = run(f'docker exec {container} bash -c "cd /testbed && git log --oneline -1"')
    docker_short = short.stdout.strip()
    base = run(f'docker exec {container} bash -c "cd /testbed && git rev-parse {manifest_commit[:10]}"')
    actual_base = base.stdout.strip()
    status = run(f'docker exec {container} bash -c "cd /testbed && git status --porcelain"')
    is_clean = len(status.stdout.strip()) == 0
    diff_size = run(f'docker exec {container} bash -c "cd /testbed && git diff HEAD | wc -c"')
    diff_bytes = int(diff_size.stdout.strip() or 0)

    base_commit_match = (manifest_commit == actual_base)

    print(f'  docker_head: {docker_head[:12]} ({docker_short})')
    print(f'  manifest_commit: {manifest_commit[:12]}...')
    print(f'  actual_base: {actual_base[:12]}...')
    print(f'  base_commit_match: {base_commit_match}')
    print(f'  workspace_clean: {is_clean}, diff_bytes: {diff_bytes}')

    # 2. Export /testbed to workspace
    dest = WORKSPACE_ROOT / inst_id / 'repo_base'
    if dest.exists():
        print(f'  Removing existing: {dest}')
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    # Copy via docker cp to a temp location then move
    tmp_dest = Path(f'/tmp/{inst_id}_testbed')
    r = run(f'docker cp {container}:/testbed {tmp_dest}')
    if r.returncode != 0:
        print(f'  ERROR: docker cp failed: {r.stderr}')
        continue

    # Move to workspace
    shutil.move(str(tmp_dest), str(dest))
    print(f'  Copied to: {dest}')

    # 3. Record repo_resolution_report
    report = {
        'instance_id': inst_id,
        'repo_source': 'docker_image_testbed',
        'docker_image': f'swebench/sweb.eval.x86_64.django_1776_{inst_id}:latest',
        'manifest_base_commit': manifest_commit,
        'docker_testbed_head': docker_head,
        'actual_base_commit': actual_base,
        'base_commit_match': base_commit_match,
        'workspace_clean': is_clean,
        'workspace_diff_bytes': diff_bytes,
        'exported_at': datetime.now(timezone.utc).isoformat(),
    }
    reports.append(report)

    # Write per-instance report
    inst_report_path = dest.parent / 'repo_resolution_report.json'
    inst_report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'  Report: {inst_report_path}')

# 4. Write collective report
collective = REPORT_DIR / 'batch1_docker_repo_resolution_report.json'
collective.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding='utf-8')
print(f'\nCollective report: {collective}')

# 5. Update manifest
print('\n=== Updating manifest ===')
with open(MANIFEST_PATH, 'r') as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

# Add new fields if needed
new_fields = ['repo_source', 'base_commit_match', 'docker_head_commit']
for nf in new_fields:
    if nf not in fieldnames:
        fieldnames.append(nf)

updated = 0
for row in rows:
    iid = row.get('instance_id', '')
    if iid in INSTANCES:
        report = next(r for r in reports if r['instance_id'] == iid)
        dest = WORKSPACE_ROOT / iid / 'repo_base'
        row['repo_base_path'] = str(dest)
        row['repo_ready'] = 'yes'
        row['repo_source'] = 'docker_image_testbed'
        row['base_commit_match'] = str(report['base_commit_match']).lower()
        row['docker_head_commit'] = report['docker_testbed_head']
        updated += 1
        print(f'  {iid}: repo_ready=yes, repo_source=docker_image_testbed, match={report["base_commit_match"]}')

with open(MANIFEST_PATH, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

print(f'Manifest updated: {updated} rows')
print('Done.')
