#!/usr/bin/env python3
"""Task 3C — Pre-execution checks."""
import csv
import subprocess
from pathlib import Path

path = Path("/mnt/d/condiag-artifacts/condiag/v0/canonical_base_eval_matrix.csv")
with open(path, newline="", encoding="utf-8") as f:
    rows = list(csv.DictReader(f))

first_failed_ids = [
    "django__django-11820",
    "django__django-12125",
    "django__django-13513",
    "django__django-16454",
    "sympy__sympy-20428",
]

def docker_image_name(iid):
    parts = iid.split("__")
    repo = parts[0]
    num = parts[1]
    escaped = f"{repo}_1776_{num}"
    return f"swebench/sweb.eval.x86_64.{escaped}:latest"

print("=" * 80)
print("TASK 3C — PRE-EXECUTION CHECKS")
print("=" * 80)

for r in rows:
    iid = r["instance_id"]
    if iid not in first_failed_ids:
        continue

    img = docker_image_name(iid)
    outdir = Path(f"/mnt/d/condiag-artifacts/condiag/v0/post_validation_logs/{iid}")

    print(f"\n--- {iid} ---")
    print(f"  1. Docker image:      {img}")
    print(f"  2. attempt_1 patch:   {r.get('patch_path', '(empty)')}")
    print(f"  3. output dir:        {outdir}")
    print(f"  4. failure_class:     {r.get('failure_class', '(empty)')}")
    print(f"  5. base_resolved:     {r.get('base_resolved', '(empty)')}")
    print(f"  6. eval_status:       {r.get('eval_status', '(empty)')}")

    if outdir.exists():
        existing = list(outdir.iterdir())
        if existing:
            print(f"  7. EXISTING logs (will OVERWRITE):")
            for f in existing:
                print(f"       {f.name}  ({f.stat().st_size} bytes)")
        else:
            print(f"  7. Output dir exists (empty)")
    else:
        print(f"  7. Output dir does not exist (fresh)")

    patch_path = r.get('patch_path', '')
    if patch_path:
        pp = Path(patch_path)
        if pp.exists():
            print(f"  8. Patch file exists: {patch_path}  ({pp.stat().st_size} bytes)")
        else:
            print(f"  8. WARNING: Patch file NOT FOUND: {patch_path}")
    else:
        print(f"  8. WARNING: patch_path is empty!")

    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", img],
        capture_output=True, text=True, timeout=10
    )
    if result.stdout.strip():
        print(f"  9. Docker image LOCAL: YES")
    else:
        print(f"  9. Docker image LOCAL: NOT FOUND (will pull)")

print("\n" + "=" * 80)
print("Pre-flight complete.")
print("=" * 80)
