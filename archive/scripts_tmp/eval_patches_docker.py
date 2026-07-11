"""Step 7a: Docker eval of attempt_2 patches on django-13513 x 4 baselines.

Applies SWE-bench test_patch + model_patch, then runs F2P and P2P tests.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

RUNS_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/runs/miniswe")
OUT_DIR = Path("/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4")

# Load instance metadata from HF dataset
def load_instance_info():
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-Bench_Verified", split="test")
    info = {}
    targets = [
        "django__django-13513",
        "sympy__sympy-19954",
        "django__django-11099",
    ]
    for row in ds:
        iid = row.get("instance_id", "")
        if iid in targets:
            image_name = row.get("image_name") or ""
            if not image_name:
                id_docker = iid.replace("__", "_1776_")
                image_name = f"docker.io/swebench/sweb.eval.x86_64.{id_docker}:latest".lower()
            info[iid] = {
                "image": image_name,
                "f2p": row.get("FAIL_TO_PASS") or [],
                "p2p": row.get("PASS_TO_PASS") or [],
                "test_patch": row.get("test_patch") or "",
                "base_commit": row.get("base_commit", ""),
            }
    return info


def clean_patch(text: str) -> str:
    """Remove markdown fences and normalize."""
    text = text.strip()
    # Strip ```diff or ``` fences
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    text = "\n".join(lines).strip()
    return text


def run_eval(image: str, test_patch_text: str, model_patch_path: Path,
             f2p_tests: list[str], p2p_tests: list[str], label: str,
             timeout: int = 600) -> dict:
    """Run full SWE-bench eval for one patch."""
    if not model_patch_path.is_file():
        return {"error": "patch_not_found"}

    model_patch_raw = model_patch_path.read_text(encoding="utf-8")
    model_patch_text = clean_patch(model_patch_raw)
    if len(model_patch_text) < 50:
        return {"error": "patch_too_small", "patch_chars": len(model_patch_text)}

    # Write patches to temp files
    tmp_test = Path("/tmp/_eval_test_patch.diff")
    tmp_model = Path("/tmp/_eval_model_patch.diff")
    tmp_test.write_text(test_patch_text, encoding="utf-8")
    tmp_model.write_text(model_patch_text, encoding="utf-8")

    container_name = f"eval_swe_{hash(label) & 0xFFFF:04x}"

    # Clean up old container
    subprocess.run(["docker", "rm", "-f", container_name],
                   capture_output=True, timeout=10)

    # Start container
    start_cmd = [
        "docker", "run", "--rm", "-d",
        "--name", container_name,
        "-v", f"{tmp_test}:/tmp/test_patch.diff:ro",
        "-v", f"{tmp_model}:/tmp/model_patch.diff:ro",
        "--platform", "linux/amd64",
        image, "sleep", "3600",
    ]
    result = subprocess.run(start_cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return {"error": "container_start_failed", "stderr": result.stderr[:500]}

    try:
        # Apply test_patch first, then model_patch
        apply_cmd = [
            "docker", "exec", container_name,
            "bash", "-lc",
            "cd /testbed && git checkout -- . && "
            "git apply --verbose /tmp/test_patch.diff 2>&1 && "
            "git apply --verbose /tmp/model_patch.diff 2>&1",
        ]
        result = subprocess.run(apply_cmd, capture_output=True, text=True, timeout=30)
        apply_output = result.stdout + result.stderr
        if result.returncode != 0:
            # Try with patch command (more lenient)
            apply2 = subprocess.run(
                ["docker", "exec", container_name, "bash", "-lc",
                 "cd /testbed && git checkout -- . && "
                 "git apply /tmp/test_patch.diff 2>&1 && "
                 "patch -p1 --force < /tmp/model_patch.diff 2>&1"],
                capture_output=True, text=True, timeout=30
            )
            if apply2.returncode != 0:
                return {"error": "patch_apply_failed",
                        "stdout": apply_output[:500],
                        "stderr2": (apply2.stdout + apply2.stderr)[:500]}

        # Install pytest (needed for Django test running)
        install = subprocess.run(
            ["docker", "exec", container_name, "bash", "-lc",
             "pip install pytest -q 2>&1 | tail -3"],
            capture_output=True, text=True, timeout=120
        )

        def run_pytest(tests: list[str]) -> dict:
            if not tests:
                return {"passed": 0, "failed": 0, "error": 0, "output": ""}
            test_args = " ".join(tests)
            cmd = (
                f"cd /testbed && python -m pytest {test_args} "
                f"-v --no-header --tb=line -p no:cacheprovider 2>&1"
            )
            r = subprocess.run(
                ["docker", "exec", container_name, "bash", "-lc", cmd],
                capture_output=True, text=True, timeout=timeout
            )
            output = r.stdout + r.stderr
            passed = 0
            failed = 0
            error = 0
            m = re.search(r"=+\s+(\d+)\s+passed", output)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+)\s+failed", output)
            if m:
                failed = int(m.group(1))
            m = re.search(r"(\d+)\s+errors?", output)
            if m:
                error = int(m.group(1))
            return {"passed": passed, "failed": failed, "error": error, "output": output[-3000:]}

        f2p_result = run_pytest(f2p_tests)
        p2p_result = run_pytest(p2p_tests)

        f2p_all_pass = f2p_result["failed"] == 0 and f2p_result["error"] == 0 and f2p_result["passed"] >= len(f2p_tests)
        p2p_all_pass = p2p_result["failed"] == 0 and p2p_result["error"] == 0

        resolved = f2p_all_pass and p2p_all_pass
        partial = f2p_all_pass and not p2p_all_pass

        return {
            "resolved": resolved,
            "partial": partial,
            "f2p": f2p_result,
            "p2p": p2p_result,
            "patch_chars": len(model_patch_text),
            "apply_output": apply_output[:500],
        }
    finally:
        subprocess.run(["docker", "rm", "-f", container_name],
                       capture_output=True, timeout=10)


def main():
    info = load_instance_info()
    print("Loaded instance info:", list(info.keys()))

    targets = ["django__django-13513"]
    if len(sys.argv) > 1:
        targets = [a for a in sys.argv[1:] if a in info]
        if not targets:
            targets = list(info.keys())

    baselines = ["base_miniswe", "feedback_retry", "broad_expansion", "condiag_packet_only"]
    results = {}
    start_time = time.time()

    for iid in targets:
        inst = info[iid]
        print(f"\n{'='*70}")
        print(f"INSTANCE: {iid}")
        print(f"Image: {inst['image']}")
        print(f"F2P tests: {len(inst['f2p'])}")
        print(f"P2P tests: {len(inst['p2p'])}")
        print(f"Test patch chars: {len(inst['test_patch'])}")
        print(f"{'='*70}")

        for bl in baselines:
            patch_path = RUNS_ROOT / bl / iid / "attempt_2" / "patch.diff"
            label = f"{iid}/{bl}"
            print(f"\n  [{label}]")
            print(f"  Patch: {patch_path}")
            exists = patch_path.is_file()
            size = patch_path.stat().st_size if exists else 0
            print(f"  Exists: {exists}, size: {size}")

            if not exists:
                print(f"  -> SKIP: no patch")
                results[label] = {"error": "no_patch"}
                continue

            r = run_eval(inst["image"], inst["test_patch"], patch_path,
                        inst["f2p"], inst["p2p"], label)
            results[label] = r

            if "error" in r:
                print(f"  -> ERROR: {r['error']}")
            elif r["resolved"]:
                print(f"  -> RESOLVED")
            elif r.get("partial"):
                print(f"  -> PARTIAL (F2P OK, P2P regressions)")
            else:
                print(f"  -> FAILED")
            f = r.get("f2p", {})
            p = r.get("p2p", {})
            print(f"     F2P: passed={f.get('passed','?')} failed={f.get('failed','?')} error={f.get('error','?')}")
            print(f"     P2P: passed={p.get('passed','?')} failed={p.get('failed','?')} error={p.get('error','?')}")

    elapsed = time.time() - start_time
    print(f"\n{'='*70}")
    print(f"EVAL SUMMARY (elapsed: {elapsed:.0f}s)")
    print(f"{'='*70}")
    header = f"{'instance/baseline':<50s} {'result':<15s} {'F2P':<15s} {'P2P':<15s}"
    print(header)
    print("-" * 100)
    for label, r in sorted(results.items()):
        if "error" in r:
            result_str = f"ERR:{r['error'][:20]}"
            f2p_str = "-"
            p2p_str = "-"
        elif r["resolved"]:
            result_str = "RESOLVED"
            f = r["f2p"]
            p = r["p2p"]
            f2p_str = f"{f['passed']}/{f['passed']+f['failed']}"
            p2p_str = f"{p['passed']}/{p['passed']+p['failed']}"
        elif r.get("partial"):
            result_str = "PARTIAL"
            f = r["f2p"]
            p = r["p2p"]
            f2p_str = f"{f['passed']}/{f['passed']+f['failed']}"
            p2p_str = f"{p['passed']}/{p['passed']+p['failed']}"
        else:
            result_str = "FAILED"
            f = r.get("f2p", {})
            p = r.get("p2p", {})
            f2p_str = f"{f.get('passed','?')}/{f.get('passed',0)+f.get('failed',0)}"
            p2p_str = f"{p.get('passed','?')}/{p.get('passed',0)+p.get('failed',0)}"
        print(f"{label:<50s} {result_str:<15s} {f2p_str:<15s} {p2p_str:<15s}")

    out_path = OUT_DIR / "repair_smoke_eval.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nResults saved to: {out_path}")

    resolved_count = sum(1 for r in results.values() if r.get("resolved"))
    print(f"Resolved: {resolved_count}/{len(results)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
