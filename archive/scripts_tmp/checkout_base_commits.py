"""Checkout Docker testbed repos to clean base_commit (parent of SWE-bench commit)."""

import json, csv, subprocess
from pathlib import Path

WORKSPACE_ROOT = Path('/home/swelite/condiag/workspaces')
MANIFEST_PATH = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/manifest.csv')
INSTANCES = ['django__django-11820', 'django__django-16454']


def run(cmd, cwd=None):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=cwd)


reports = []

for inst_id in INSTANCES:
    print(f'\n=== {inst_id} ===')
    repo = WORKSPACE_ROOT / inst_id / 'repo_base'

    # Get parents of HEAD
    parents_r = run('git rev-list --parents -n 1 HEAD', cwd=repo)
    parents = parents_r.stdout.strip().split()
    print(f'  parents list ({len(parents)}): {[p[:12] for p in parents]}')

    # parents[0] = HEAD, parents[1] = first parent (clean base for non-merge)
    head_commit = parents[0]
    if len(parents) >= 3:
        # Merge commit: P1 = SWE-bench infra, P2 = clean base
        swbench_infra = parents[1]
        clean_base = parents[2]
    else:
        # Non-merge: P1 = clean base (SWE-bench commit is on top)
        swbench_infra = head_commit
        clean_base = parents[1]

    print(f'  HEAD (SWE-bench): {head_commit[:12]}')
    print(f'  Clean base_commit: {clean_base[:12]}')

    # Read manifest
    manifest_commit = None
    with open(MANIFEST_PATH) as f:
        for row in csv.DictReader(f):
            if row['instance_id'] == inst_id:
                manifest_commit = row.get('base_commit', '')
                break

    short = manifest_commit[:10] if manifest_commit else ''
    actual = run(f'git rev-parse {short}', cwd=repo).stdout.strip() if short else ''
    if actual:
        print(f'  Manifest short {short} resolves to: {actual[:12]}')
        print(f'  Matches clean_base: {clean_base == actual}')

    # Checkout clean base
    print(f'  git checkout {clean_base[:12]}')
    r = run(f'git checkout {clean_base[:12]}', cwd=repo)
    if r.returncode != 0:
        print(f'  ERROR: {r.stderr}')
        continue

    new_head = run('git rev-parse HEAD', cwd=repo).stdout.strip()
    dirty = run('git status --porcelain', cwd=repo).stdout.strip()
    print(f'  New HEAD: {new_head[:12]}  clean={not bool(dirty)}')

    reports.append({
        'instance_id': inst_id,
        'swbench_head': head_commit,
        'swbench_infra_commit': swbench_infra,
        'clean_base_commit': clean_base,
        'manifest_base_commit_old': manifest_commit,
        'manifest_base_commit_new': clean_base,
        'workspace_clean': not bool(dirty),
        'short_prefix_match': actual == clean_base if actual else 'N/A',
    })

# Update manifest
print('\n=== Updating manifest ===')
with open(MANIFEST_PATH) as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

updated = 0
for row in rows:
    iid = row.get('instance_id', '')
    for rpt in reports:
        if rpt['instance_id'] == iid:
            old = row.get('base_commit', '')[:12]
            row['base_commit'] = rpt['clean_base_commit']
            row['docker_head_commit'] = rpt['clean_base_commit']
            row['base_commit_match'] = 'true'
            print(f'  {iid}: {old}... -> {rpt["clean_base_commit"][:12]}... (match=true)')
            updated += 1

with open(MANIFEST_PATH, 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

# Write combined report
out = Path('/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/batch1_repo_checkout_report.json')
out.write_text(json.dumps(reports, indent=2, ensure_ascii=False), encoding='utf-8')
print(f'\nReport: {out}')
print(f'Updated {updated} manifest rows')
print('Done.')
