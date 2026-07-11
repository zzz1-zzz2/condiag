"""Check sympy container: test runner, file structure."""
import subprocess

IMG = "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest"
container = "check_sympy"

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
r = subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64", IMG, "sleep", "3600",
], capture_output=True, text=True)
if r.returncode != 0:
    print(f"FAILED to start container: {r.stdout} {r.stderr}")
    exit(1)

print("1. Python version and sympy location")
r = subprocess.run(["docker", "exec", container, "bash", "-lc",
    "python --version && python -c 'import sympy; print(sympy.__file__)' 2>&1"],
    capture_output=True, text=True, timeout=30)
print(r.stdout)

print("2. Find test_sylow_subgroup")
r = subprocess.run(["docker", "exec", container, "bash", "-lc",
    "cd /testbed && grep -rn 'def test_sylow_subgroup' sympy/ 2>&1"],
    capture_output=True, text=True, timeout=30)
print(r.stdout)

print("3. Try running single test with pytest")
r = subprocess.run(["docker", "exec", container, "bash", "-lc",
    "cd /testbed && python -m pytest sympy/combinatorics/tests/test_perm_groups.py::test_sylow_subgroup -v --no-header --tb=short 2>&1"],
    capture_output=True, text=True, timeout=120)
print(r.stdout[-1500:])
if r.stderr:
    print("STDERR:", r.stderr[:300])

print("4. Check installed test tools")
r = subprocess.run(["docker", "exec", container, "bash", "-lc",
    "python -m pytest --version 2>&1; pip list 2>&1 | grep -i pytest"],
    capture_output=True, text=True, timeout=30)
print(r.stdout)

print("5. Check target file context (lines 2195-2220)")
r = subprocess.run(["docker", "exec", container, "bash", "-lc",
    "cd /testbed && sed -n '2195,2220p' sympy/combinatorics/perm_groups.py 2>&1"],
    capture_output=True, text=True, timeout=30)
print(r.stdout)

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("Done.")
