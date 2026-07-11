"""Verify: is f36862b69c an ancestor of base_commit 6599608c4d?

If YES: __suppress_context__ was already in the base commit, gold patch is about refactoring
If NO: __suppress_context__ was added AFTER base commit, docker image may have extra commits
"""
import subprocess

container = "verify_git"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64",
    "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
    "sleep", "3600",
], capture_output=True)

print("=" * 60)
print("1. Is f36862b69c an ancestor of 6599608c4d?")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git merge-base --is-ancestor f36862b69c 6599608c4d && echo 'YES: f36862b69c is ancestor of 6599608c4d' || echo 'NO: f36862b69c is NOT ancestor of 6599608c4d'"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("2. Date order of the two commits")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git log --oneline --format='%h %ad %s' --date=short f36862b69c -1 && echo '---' && git log --oneline --format='%h %ad %s' --date=short 6599608c4d -1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("3. What commit is HEAD?")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git log --oneline -1 && echo '---' && git log --oneline --format='%h %ad %s' --date=short HEAD -5"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("4. Is base_commit 6599608c4d the actual HEAD~0? Or is it behind HEAD?")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && echo 'HEAD commit:' && git rev-parse HEAD && echo 'base_commit:' && git rev-parse 6599608c4d && echo 'Relation:' && git merge-base --is-ancestor 6599608c4d HEAD && echo 'base_commit IS ancestor of HEAD' || echo 'base_commit is NOT ancestor of HEAD'"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("5. Check source WITHOUT any patches applied - does it have __suppress_context__?")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git checkout -- . 2>&1 && grep -n '__suppress_context__' django/views/debug.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("6. Check what debug.py looked like at base_commit 6599608c4d")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git show 6599608c4d:django/views/debug.py | grep -n '__suppress_context__' 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("7. Check what debug.py looked like at f36862b69c (the __suppress_context__ commit)")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git show f36862b69c:django/views/debug.py | grep -n '__suppress_context__' 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
