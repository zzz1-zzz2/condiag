"""Test normalizer v3 on django-13513 x 4 baselines."""
import subprocess, json
from pathlib import Path
from datasets import load_dataset

ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
test_patch = ""
for row in ds:
    if row.get("instance_id") == "django__django-13513":
        test_patch = row.get("test_patch", "")
        break

Path("/tmp/_test.diff").write_text(test_patch)

container = "test_norm5"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64",
    "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
    "sleep", "3600",
], capture_output=True)

subprocess.run(["docker", "cp", "/tmp/_test.diff", f"{container}:/tmp/test.diff"], capture_output=True)
subprocess.run(["docker", "cp", "/home/swelite/condiag/scripts_tmp/norm_v3_inline.py", f"{container}:/tmp/norm.py"], capture_output=True)
subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && git apply /tmp/test.diff 2>&1"
], capture_output=True)

patches = {}
for bl in ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]:
    pp = Path(f"/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe/{bl}/django__django-13513/attempt_2/patch.diff")
    if pp.is_file():
        raw = pp.read_text(encoding="utf-8")
        patches[bl] = raw.strip()

for bl, patch_text in patches.items():
    print(f"\n{'='*60}")
    print(f"Testing: {bl}")

    # Reset repo to test_patch state
    subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git checkout -- . 2>&1 && git apply /tmp/test.diff 2>&1"
    ], capture_output=True)

    # Write raw patch
    Path("/tmp/_raw.diff").write_text(patch_text, encoding="utf-8")
    subprocess.run(["docker", "cp", "/tmp/_raw.diff", f"{container}:/tmp/raw_patch.diff"], capture_output=True)

    # Run normalizer v3
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && python3 /tmp/norm.py /tmp/raw_patch.diff 2>&1"
    ], capture_output=True, text=True, timeout=60)

    print(f"Normalizer: {r.stdout[:800]}")
    if r.stderr:
        print(f"ERR: {r.stderr[:300]}")

    # Check git diff
    r = subprocess.run([
        "docker", "exec", container, "bash", "-lc",
        "cd /testbed && git diff -- django/views/debug.py 2>&1"
    ], capture_output=True, text=True, timeout=30)
    if r.stdout.strip():
        print(f"DIFF APPLIED ({len(r.stdout)} chars):")
        print(r.stdout[:800])
    else:
        print("NO DIFF on debug.py")

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
