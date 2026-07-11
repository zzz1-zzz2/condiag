"""Check gold patch and git log for django-13513."""
import subprocess

container = "test_gold"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64",
    "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
    "sleep", "3600",
], capture_output=True)

print("=" * 60)
print("1. Git log for debug.py (last 5 commits)")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git log --oneline -10 -- django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("2. Git log for all (last 5)")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git log --oneline -5 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("3. Check if gold patch from datasets matches")
from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
for row in ds:
    if row.get("instance_id") == "django__django-13513":
        print(f"base_commit: {row.get('base_commit', 'N/A')}")
        print(f"patch length: {len(row.get('patch', ''))}")
        print(f"test_patch length: {len(row.get('test_patch', ''))}")
        # Print gold patch first 80 lines
        gold = row.get('patch', '')
        for i, line in enumerate(gold.split('\n')[:80]):
            print(f"G{i+1}: {line}")
        break

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
