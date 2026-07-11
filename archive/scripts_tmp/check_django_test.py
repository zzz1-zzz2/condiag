"""Check how Django tests are configured in the SWE-bench image."""
import subprocess

container = "test_dj"
subprocess.run(["docker", "rm", "-f", container], capture_output=True)
subprocess.run([
    "docker", "run", "--rm", "-d", "--name", container,
    "--platform", "linux/amd64",
    "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13513:latest",
    "sleep", "3600",
], capture_output=True)

# Check for Django test settings
print("=" * 60)
print("1. Check for test settings modules")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "ls /testbed/tests/test_sqlite* /testbed/tests/test_runner* /testbed/tests/settings* 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("2. Check if there's a setup.py or conftest or pytest.ini")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "ls /testbed/pytest.ini /testbed/setup.cfg /testbed/tox.ini /testbed/conftest.py /testbed/tests/conftest.py 2>&1"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("3. Check how SWE-bench runs tests (look for eval script)")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "find / -name 'eval.sh' -o -name 'run_test.sh' -o -name 'test_runner.py' 2>/dev/null | head -10"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

print("=" * 60)
print("4. Try running Django test with settings")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "cd /testbed && DJANGO_SETTINGS_MODULE=tests.test_sqlite python -m pytest tests/view_tests/tests/test_debug.py::ExceptionReporterTests::test_innermost_exception_without_traceback -v --no-header --tb=short 2>&1"
], capture_output=True, text=True, timeout=300)
print(r.stdout[-1500:])

print("=" * 60)
print("5. Check if 'test_sqlite' exists or try alternate names")
r = subprocess.run([
    "docker", "exec", container, "bash", "-lc",
    "ls /testbed/tests/*.py 2>&1 | head -20"
], capture_output=True, text=True, timeout=30)
print(r.stdout)

subprocess.run(["docker", "rm", "-f", container], capture_output=True)
print("\nDone.")
