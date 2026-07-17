#!/usr/bin/env bash
# ConDiag v4 — Pull all 16 pilot Docker images and tag env images
# Usage: bash scripts/pull_pilot_images.sh
set -euo pipefail

echo "================================================"
echo "  Pulling 16 pilot instance images"
echo "================================================"

# Compute image names from Python (uses InstanceRegistry + make_test_spec)
python3 -c "
import sys; sys.path.insert(0, '.')
import logging; logging.basicConfig(level=logging.WARNING)
import subprocess, docker
from condiag.instance_registry import InstanceRegistry
from swebench.harness.test_spec.test_spec import make_test_spec

client = docker.from_env()
reg = InstanceRegistry()

for spec in reg.list_pilot():
    sb = spec._swebench_row
    if not sb:
        print(f'SKIP {spec.instance_id}: no SWE-bench row')
        continue

    swe_inst = dict(
        instance_id=sb['instance_id'], repo=sb['repo'], version=sb['version'],
        base_commit=sb['base_commit'], test_patch=sb['test_patch'],
        FAIL_TO_PASS=sb.get('FAIL_TO_PASS', '[]'),
        PASS_TO_PASS=sb.get('PASS_TO_PASS', '[]'),
        problem_statement=sb['problem_statement'], patch=sb['patch'],
    )
    tspec = make_test_spec(swe_inst, namespace='swebench')
    eval_img = tspec.instance_image_key
    env_img = tspec.env_image_key

    # Check if eval image already exists
    try:
        client.images.get(eval_img)
        print(f'✅ {spec.instance_id:45s} {eval_img.split(\":\")[0][-25:]:30s} cached')
        # Still need to ensure env tag exists
        try:
            client.images.get(env_img)
        except:
            print(f'   → tagging env: {env_img}')
            client.images.get(eval_img).tag(env_img)
        continue
    except:
        pass

    print(f'⬇️  {spec.instance_id:45s} pulling {eval_img.split(\"/\")[-1][:30]}...')
    r = subprocess.run(['docker', 'pull', eval_img], capture_output=True, text=True)
    if r.returncode != 0:
        print(f'   ❌ pull failed: {r.stderr.strip()[:100]}')
        continue

    # Tag env image
    try:
        client.images.get(env_img)
    except:
        client.images.get(eval_img).tag(env_img)
        print(f'   → env tagged: {env_img}')

print()
print('Done!')
"
