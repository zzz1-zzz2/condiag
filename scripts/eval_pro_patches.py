"""Custom eval for SWE-bench Pro and Multi instances using Docker images."""
import json, os, re, subprocess, sys, time, hashlib
from pathlib import Path


def clean_patch(text: str) -> str:
    text = text.strip()
    lines = text.split("\n")
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def find_testbed(container: str) -> str:
    """Find the repo root inside the container."""
    for d in ["/app", "/testbed", "/home"]:
        r = subprocess.run(["docker", "exec", container, "test", "-d", d],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            # verify it has git
            r2 = subprocess.run(["docker", "exec", container, "test", "-d", d + "/.git"],
                               capture_output=True, timeout=5)
            if r2.returncode == 0:
                return d
    # fallback: search for .git
    r = subprocess.run(["docker", "exec", container, "bash", "-c",
                        "find / -name .git -type d -maxdepth 4 2>/dev/null | head -1"],
                       capture_output=True, text=True, timeout=15)
    git_dir = r.stdout.strip()
    if git_dir:
        return str(Path(git_dir).parent)
    return "/app"  # default guess


def get_test_command(tests: list, language: str, testbed: str) -> str:
    """Build test command based on language."""
    if not tests:
        return "echo no_tests"
    if language == "python":
        # Extract unique test files
        files = list(set(t.split("::")[0] for t in tests if "::" in t))
        if not files:
            files = tests[:5]  # limit
        files_str = " ".join(files[:20])  # limit to 20 files
        return f"cd {testbed} && python -m pytest {files_str} -v --no-header -x 2>&1"
    elif language == "go":
        # Extract packages
        pkgs = set()
        for t in tests:
            parts = t.split("/")
            if len(parts) > 1:
                pkgs.add("/".join(parts[:-1]))
        if not pkgs:
            pkgs.add(".")
        pkgs_str = " ".join(f"{testbed}/{p}" for p in sorted(pkgs)[:5])
        return f"cd {testbed} && go test {pkgs_str} -v -count=1 -timeout=300s 2>&1"
    elif language == "js":
        files = list(set(t.split()[0] if " " in t else t for t in tests))[:10]
        files_str = " ".join(files)
        return f"cd {testbed} && npx jest {files_str} --no-coverage 2>&1"
    else:
        return f"echo unknown_language:{language}"


def run_tests(container: str, cmd: str, timeout: int = 600) -> dict:
    """Run test command in container and parse results."""
    r = subprocess.run(
        ["docker", "exec", container, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=timeout
    )
    output = r.stdout + r.stderr
    passed = failed = error = 0

    for m in re.finditer(r"(\d+) passed", output):
        passed = max(passed, int(m.group(1)))
    for m in re.finditer(r"(\d+) failed", output):
        failed = max(failed, int(m.group(1)))
    for m in re.finditer(r"(\d+) errors?", output):
        error = max(error, int(m.group(1)))

    if failed == 0:
        m = re.search(r"FAIL\s+(.*?)\s", output)
        if m:
            failed = 1

    return {
        "passed": passed, "failed": failed, "error": error,
        "output": output[-8000:], "returncode": r.returncode,
    }


def eval_instance(inst_id: str, model_patch: str, info: dict, timeout: int = 1800) -> dict:
    """Evaluate one Pro instance."""
    tag = info.get("dockerhub_tag", "")
    if not tag:
        return {"error": "no_dockerhub_tag"}
    image = "jefzda/sweap-images:" + tag
    test_patch = info.get("test_patch", "")
    f2p = info.get("fail_to_pass", [])
    p2p = info.get("pass_to_pass", [])
    language = info.get("repo_language", "")

    container = "eval_" + hashlib.md5(inst_id.encode()).hexdigest()[:8]
    subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)

    # Write patches
    pid = hashlib.md5((inst_id + "_p").encode()).hexdigest()[:8]
    test_patch_file = Path(f"/tmp/_tp_{pid}.diff")
    model_patch_file = Path(f"/tmp/_mp_{pid}.diff")
    test_patch_file.write_text(test_patch)
    model_patch_file.write_text(model_patch)

    # Start container (entrypoint is /bin/bash, pass -c)
    r = subprocess.run(["docker", "run", "-d", "--name", container,
                        "-v", f"{test_patch_file}:/tmp/test_patch.diff:ro",
                        "-v", f"{model_patch_file}:/tmp/model_patch.diff:ro",
                        "--platform", "linux/amd64", image,
                        "-c", "sleep 3600"],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        for p in [test_patch_file, model_patch_file]:
            try: p.unlink()
            except: pass
        return {"error": "container_start_failed", "stderr": r.stderr[:300]}

    try:
        testbed = find_testbed(container)
        if not testbed:
            return {"error": "no_testbed_found"}

        # Reset and apply patches
        cmds = [
            f"cd {testbed} && git reset --hard HEAD 2>/dev/null",
            f"cd {testbed} && git checkout -- . 2>/dev/null",
            f"cd {testbed} && git clean -fd 2>/dev/null",
            f"cd {testbed} && git apply --whitespace=nowarn /tmp/test_patch.diff 2>&1",
            f"cd {testbed} && git apply --whitespace=nowarn /tmp/model_patch.diff 2>&1",
        ]
        for cmd in cmds:
            r = subprocess.run(["docker", "exec", container, "bash", "-c", cmd],
                              capture_output=True, text=True, timeout=60)
            if r.returncode != 0 and "apply" in cmd:
                # Try patch as fallback
                r2 = subprocess.run(
                    ["docker", "exec", container, "bash", "-c",
                     f"cd {testbed} && patch -p1 --force < /tmp/model_patch.diff 2>&1"],
                    capture_output=True, text=True, timeout=60)
                if r2.returncode != 0:
                    return {"error": "patch_apply_failed",
                            "apply_stderr": (r.stdout[:500] + r2.stdout[:500])}

        f2p_cmd = get_test_command(f2p, language, testbed)
        p2p_cmd = get_test_command(p2p, language, testbed)
        f2p_result = run_tests(container, f2p_cmd, timeout)
        p2p_result = run_tests(container, p2p_cmd, timeout)

        f2p_ok = f2p_result["failed"] == 0 and f2p_result["error"] == 0
        p2p_ok = p2p_result["failed"] == 0 and p2p_result["error"] == 0

        return {
            "resolved": f2p_ok and p2p_ok,
            "f2p": {"passed": f2p_result["passed"], "failed": f2p_result["failed"]},
            "p2p": {"passed": p2p_result["passed"], "failed": p2p_result["failed"]},
            "n_f2p": len(f2p), "n_p2p": len(p2p),
            "language": language, "testbed": testbed,
        }
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True, timeout=10)
        for p in [test_patch_file, model_patch_file]:
            try: p.unlink()
            except: pass


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", default="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/deduped/predictions_Pro.jsonl")
    parser.add_argument("--metadata", default="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/pro_instances_v2.json")
    parser.add_argument("--output", default="/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_pro_custom")
    parser.add_argument("--max-workers", type=int, default=2)
    parser.add_argument("--instance", type=str, default="", help="Single instance to eval")
    args = parser.parse_args()

    predictions = {}
    with open(args.predictions) as f:
        for line in f:
            d = json.loads(line)
            predictions[d["instance_id"]] = d["model_patch"]

    instances = json.load(open(args.metadata))
    if args.instance:
        instances = {k: v for k, v in instances.items() if args.instance in k}

    to_eval = {iid: info for iid, info in instances.items() if iid in predictions}
    print(f"Instances to evaluate: {len(to_eval)}")

    os.makedirs(args.output, exist_ok=True)
    results = {}

    for iid, info in sorted(to_eval.items()):
        model_patch = clean_patch(predictions[iid])
        if len(model_patch) < 20:
            results[iid] = {"error": "patch_too_small", "chars": len(model_patch)}
            continue

        print(f"\n[{iid[:55]}] eval...", end=" ")
        sys.stdout.flush()
        t0 = time.time()
        r = eval_instance(iid, model_patch, info)
        elapsed = time.time() - t0
        results[iid] = r

        if "error" in r:
            print(f"ERROR ({elapsed:.0f}s): {r['error']}")
        elif r["resolved"]:
            print(f"RESOLVED ({elapsed:.0f}s)")
        else:
            print(f"FAILED ({elapsed:.0f}s)")
        f2p = r.get("f2p", {})
        p2p = r.get("p2p", {})
        print(f"  F2P: {f2p.get('passed',0)}/{r.get('n_f2p',0)} passed, {f2p.get('failed',0)} failed")
        print(f"  P2P: {p2p.get('passed',0)}/{r.get('n_p2p',0)} passed, {p2p.get('failed',0)} failed")

    resolved = sum(1 for r in results.values() if r.get("resolved"))
    errors = sum(1 for r in results.values() if "error" in r)
    total = len(results)
    print(f"\nSUMMARY: {resolved}/{total} resolved, {errors} errors")

    out = {"n_total": total, "n_resolved": resolved,
           "n_failed": total - resolved - errors, "n_errors": errors,
           "results": {k: {"resolved": v.get("resolved", False),
                           "error": v.get("error"),
                           "f2p_passed": v.get("f2p", {}).get("passed", 0),
                           "f2p_failed": v.get("f2p", {}).get("failed", 0),
                           "p2p_passed": v.get("p2p", {}).get("passed", 0),
                           "p2p_failed": v.get("p2p", {}).get("failed", 0)}
                       for k, v in results.items()}}
    json.dump(out, open(os.path.join(args.output, "pro_eval_results.json"), "w"), indent=2)
    print(f"Results saved to {args.output}/pro_eval_results.json")


if __name__ == "__main__":
    main()
