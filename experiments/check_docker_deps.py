#!/usr/bin/env python3
"""Check Docker images for testbed contents."""
import subprocess
import sys

images = [
    "swebench/sweb.eval.x86_64.django_1776_django-11820:latest",
    "swebench/sweb.eval.x86_64.sympy_1776_sympy-20428:latest",
]

for img in images:
    print(f"=== {img} ===")
    # Check /testbed structure
    cmd = "docker run --rm " + img + ' bash -c "ls /testbed/setup.py /testbed/setup.cfg /testbed/pyproject.toml /testbed/requirements.txt 2>/dev/null; echo ---; head -3 /testbed/setup.cfg 2>/dev/null; head -3 /testbed/pyproject.toml 2>/dev/null"'
    result = subprocess.run(cmd, shell=True, capture_output=True, timeout=30, text=True)
    print("Files in /testbed:")
    print(result.stdout[:800])
    if result.stderr:
        print("ERR:", result.stderr[:200])

    # Check python imports
    for mod in ["asgiref", "django", "mpmath", "sympy"]:
        cmd = "docker run --rm " + img + ' python3 -c "import ' + mod + '; print(' + mod + '.__version__)" 2>&1'
        result = subprocess.run(cmd, shell=True, capture_output=True, timeout=15, text=True)
        out = (result.stdout or result.stderr or "").strip()[:100]
        print(f"  import {mod}: {out}")
    print()
