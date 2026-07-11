"""Check source WITHOUT test_patch to see original state."""
import subprocess

container = "test_source"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64",
    "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
    "sleep", "3600",
], capture_output=True)

print("=" * 60)
print("1. Search for explicit_or_implicit_cause (NO test_patch)")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && grep -n 'explicit_or_implicit_cause\\|__suppress_context__' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("2. Lines 70-85 of debug.py")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && sed -n '70,85p' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("3. Lines 395-405 of debug.py")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && sed -n '395,405p' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("4. Check if there are TWO definitions of the function")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && grep -n '^def explicit_or_implicit_cause\|^    def explicit_or_implicit_cause' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("5. Check test_patch content (first 50 lines)")
from datasets import load_dataset
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
for row in ds:
    if row.get("instance_id") == "django__django-13513":
        tp = row.get("test_patch", "")
        for i, line in enumerate(tp.split("\n")[:50]):
            print(f"{i+1}: {line}")
        break

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
