"""Check actual source file structure for explicit_or_implicit_cause function."""
import subprocess

container = "test_norm5"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64",
    "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
    "sleep", "3600",
], capture_output=True)

# Apply test_patch so we see the same state as during eval
from datasets import load_dataset
from pathlib import Path
ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
test_patch = ""
for row in ds:
    if row.get("instance_id") == "django__django-13513":
        test_patch = row.get("test_patch", "")
        break
Path("/tmp/_test.diff").write_text(test_patch)
subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
subprocess.run(["docker", "exec", container, "bash", "-lc",
    "cd /testbed && git apply /tmp/test.diff 2>&1"], capture_output=True)

# Search for the function
print("=" * 60)
print("1. Search for 'explicit_or_implicit_cause' in debug.py")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && grep -n 'explicit_or_implicit_cause\\|__suppress_context__\\|__context__\\|__cause__' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)
if r.stderr:
    print("STDERR:", r.stderr[:300])

print("=" * 60)
print("2. Lines 65-100 of debug.py")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && sed -n '65,100p' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("3. Lines 390-410 of debug.py")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && sed -n '390,410p' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
