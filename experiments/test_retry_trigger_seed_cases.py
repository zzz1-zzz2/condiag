"""Seed-case regression for experiments/retry_trigger.py (D4-2).

Runs retry_trigger.classify() against the 4 available seed case_bundles and
asserts each one matches its expected trigger_type. django-13195 has no
case_bundle yet (only manual_diagnosis), so it's a TODO rather than a passing
test.

Run:
    python3 -m experiments.test_retry_trigger_seed_cases
or:
    python3 ~/condiag/experiments/test_retry_trigger_seed_cases.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from experiments.retry_trigger import classify, assert_no_leakage


REPO_ROOT = Path(__file__).resolve().parent.parent
CASE_BUNDLE_ROOT = Path("/mnt/d/condiag-artifacts/condiag/v0/case_bundles")


SEED_EXPECTATIONS = [
    ("sympy__sympy-16597",   "RUNTIME_VALIDATION_FAILURE"),
    ("sympy__sympy-13877",   "PATCH_SHAPE_ANOMALY"),
    ("astropy__astropy-13398", "EVIDENCE_EDIT_MISMATCH"),
    ("django__django-11400", "PARTIAL_FIX_SUSPICION"),
    # django__django-13195 expected NO_TRIGGER but has no case_bundle yet
]


LEAKAGE_NEGATIVE_TEST = {
    "instance_id": "synthetic_leak",
    "exit_status": "Submitted",
    "test_failures_count": 0,
    "contextbench_metrics": {"file_coverage": 1.0},   # FORBIDDEN
    "fail_to_pass": ["fake_test_id"],                  # FORBIDDEN
}


def _load_runtime_signals(instance_id: str) -> dict:
    p = CASE_BUNDLE_ROOT / instance_id / "runtime_signals.json"
    if not p.is_file():
        raise FileNotFoundError(f"case_bundle missing: {p}")
    return json.loads(p.read_text())


def test_seed_cases() -> None:
    results = []
    for instance_id, expected in SEED_EXPECTATIONS:
        try:
            rs = _load_runtime_signals(instance_id)
        except FileNotFoundError as e:
            print(f"[SKIP] {instance_id}: {e}")
            continue

        result = classify(rs)
        ok = result.trigger_type == expected
        results.append((instance_id, expected, result.trigger_type, ok))

        marker = "OK " if ok else "FAIL"
        print(f"[{marker}] {instance_id}")
        print(f"        expected: {expected}")
        print(f"        actual:   {result.trigger_type}")
        print(f"        gap:      {result.runtime_gap_status}")
        print(f"        conf:     {result.confidence}")
        if result.alternative_trigger_types:
            print(f"        alts:     {result.alternative_trigger_types}")
        print(f"        reasons:  {result.trigger_reason[:2]}")
        print()

    passed = sum(1 for *_, ok in results if ok)
    total = len(results)
    print(f"=== seed cases: {passed}/{total} passed ===")
    if passed != total:
        for instance_id, expected, actual, ok in results:
            if not ok:
                print(f"  MISMATCH: {instance_id} expected={expected} actual={actual}")
        sys.exit(1)


def test_leakage_guard() -> None:
    print("=== leakage guard (negative) ===")
    try:
        assert_no_leakage(LEAKAGE_NEGATIVE_TEST)
    except ValueError as e:
        print(f"[OK ] leak guard caught forbidden fields: {e}")
        return
    print("[FAIL] leak guard did NOT catch forbidden fields")
    sys.exit(1)


def test_django_13195_placeholder() -> None:
    """TODO: django-13195 has no case_bundle; once built, expect NO_TRIGGER."""
    p = CASE_BUNDLE_ROOT / "django__django-13195" / "runtime_signals.json"
    if not p.is_file():
        print(f"[TODO] django-13195 case_bundle missing; cannot verify NO_TRIGGER yet.")
        print(f"        Path checked: {p}")
        return
    rs = json.loads(p.read_text())
    result = classify(rs)
    expected = "NO_TRIGGER"
    ok = result.trigger_type == expected
    marker = "OK " if ok else "FAIL"
    print(f"[{marker}] django__django-13195 expected={expected} actual={result.trigger_type}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    test_leakage_guard()
    print()
    test_seed_cases()
    print()
    test_django_13195_placeholder()
