"""Test lightweight patch fix options: --recount, --whitespace=nowarn, newline."""
import subprocess, json
from pathlib import Path

container = "test_light"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64",
    "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
    "sleep", "3600",
], capture_output=True)

RUNS = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
baselines = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]

for bl in baselines:
    patch_path = RUNS / bl / "django__django-13513" / "attempt_2" / "patch.diff"
    if not patch_path.is_file():
        print(f"\n{bl}: NO PATCH")
        continue

    raw = patch_path.read_text(encoding="utf-8").strip()
    # Clean markdown
    lines = [l for l in raw.split("\n") if not l.startswith("```")]
    clean = "\n".join(lines)

    Path("/tmp/_p.diff").write_text(clean, encoding="utf-8")
    subprocess.run(["docker", "cp", "/tmp/_p.diff", f"{container}:/tmp/p.diff"], capture_output=True)

    print(f"\n{'='*60}")
    print(f"{bl} ({len(clean)} chars)")

    # Test 1: strict
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git checkout -- . 2>&1 && git apply --check /tmp/p.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    s1 = r.returncode == 0
    print(f"  1. strict apply --check: {'OK' if s1 else 'FAIL'}")

    # Test 2: --recount (does it exist?)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply --check --recount /tmp/p.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    s2 = r.returncode == 0
    err2 = (r.stdout + r.stderr)[:100]
    print(f"  2. --recount: {'OK' if s2 else 'FAIL'} {err2}")

    # Test 3: --whitespace=nowarn
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply --check --whitespace=nowarn /tmp/p.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    s3 = r.returncode == 0
    print(f"  3. --whitespace=nowarn: {'OK' if s3 else 'FAIL'}")

    # Test 4: --recount + --whitespace=nowarn
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git apply --check --recount --whitespace=nowarn /tmp/p.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    s4 = r.returncode == 0
    err4 = (r.stdout + r.stderr)[:100]
    print(f"  4. --recount + --whitespace=nowarn: {'OK' if s4 else 'FAIL'} {err4}")

    # Test 5: trailing newline then recount
    clean_nl = clean + "\n"
    Path("/tmp/_p2.diff").write_text(clean_nl, encoding="utf-8")
    subprocess.run(["docker", "cp", "/tmp/_p2.diff", f"{container}:/tmp/p2.diff"], capture_output=True)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git checkout -- . 2>&1 && git apply --check --recount --whitespace=nowarn /tmp/p2.diff 2>&1"
    ], capture_output=True, text=True, timeout=30)
    s5 = r.returncode == 0
    err5 = (r.stdout + r.stderr)[:100]
    print(f"  5. +newline +recount +whitespace: {'OK' if s5 else 'FAIL'} {err5}")

    # Test 6: norm_v3
    subprocess.run(["docker", "cp", "/home/swelite/condiag/scripts_tmp/norm_v3_inline.py", f"{container}:/tmp/norm.py"], capture_output=True)
    subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git checkout -- . 2>&1 && python3 /tmp/norm.py /tmp/p.diff 2>&1"
    ], capture_output=True, text=True, timeout=60)
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git diff --stat 2>&1"
    ], capture_output=True, text=True, timeout=30)
    print(f"  6. norm_v3 diff: {r.stdout.strip()[:150]}")

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
