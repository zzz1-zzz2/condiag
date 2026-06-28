"""D4-3 acceptance smoke test — verifies the 10 acceptance criteria.

Run:
    python3 -m experiments.test_baseline_runner_smoke
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from experiments.baseline_runner import (
    main as runner_main,
    run_one,
    _ensure_run_dir_skeleton,
    _load_instances,
    SUPPORTED_BASELINES,
)
from experiments.baseline_handlers import BASELINE_HANDLERS
from experiments.artifact_validator import validate_run, assert_no_leakage_in_text
from condiag.adapters import list_adapters


TMP = Path("/mnt/d/condiag-artifacts/condiag/v0/smoke_d4_3_runner")
INSTANCES_FILE = TMP / "instances.txt"


def _setup() -> None:
    if TMP.exists():
        shutil.rmtree(TMP)
    TMP.mkdir(parents=True)
    INSTANCES_FILE.write_text(
        "django__django-11400\n"
        "django__django-13195\n"
        "# comment line ignored\n"
        "\n"
        "sympy__sympy-16597\n",
        encoding="utf-8",
    )


def check(label: str, ok: bool, detail: str = "") -> bool:
    marker = "OK " if ok else "FAIL"
    print(f"[{marker}] {label}" + (f"  ({detail})" if detail else ""))
    return ok


def test_acceptance() -> bool:
    results = []

    # 1. baseline_runner.py importable
    results.append(check("1. baseline_runner importable",
                         True, "imported experiments.baseline_runner"))

    # 2. supports --agent / --baseline / --instances / --out / --mode
    import argparse
    import experiments.baseline_runner as br
    parser = argparse.ArgumentParser()
    # fake parse to verify flags exist
    fake_argv = ["--agent", "miniswe", "--baseline", "base_miniswe",
                 "--instances", str(INSTANCES_FILE),
                 "--out", str(TMP / "runs"), "--mode", "dry-run"]
    rc = runner_main(fake_argv)
    results.append(check("2. CLI flags work (dry-run end-to-end)", rc == 0, f"rc={rc}"))

    # 3. invalid baseline errors clearly
    rc = runner_main(["--agent", "miniswe", "--baseline", "nope",
                      "--instances", str(INSTANCES_FILE),
                      "--out", str(TMP / "x"), "--mode", "dry-run"])
    results.append(check("3. invalid baseline errors (rc=2)", rc == 2, f"rc={rc}"))

    # 4. invalid agent errors clearly
    rc = runner_main(["--agent", "nope", "--baseline", "base_miniswe",
                      "--instances", str(INSTANCES_FILE),
                      "--out", str(TMP / "x"), "--mode", "dry-run"])
    results.append(check("4. invalid agent errors (rc=2)", rc == 2, f"rc={rc}"))

    # 5. dry-run creates standard dirs per instance
    instance_dir = TMP / "runs" / "miniswe" / "base_miniswe" / "django__django-11400"
    subdirs = {p.name for p in instance_dir.iterdir() if p.is_dir()}
    expected = {"attempt_1", "final"}  # base_miniswe: no intervention, no attempt_2
    results.append(check("5. dry-run creates attempt_1 + final for base_miniswe",
                         expected.issubset(subdirs), f"got={sorted(subdirs)}"))

    # 6. each instance has run_report.json
    rr = instance_dir / "run_report.json"
    rr_data = json.loads(rr.read_text()) if rr.is_file() else {}
    results.append(check("6. run_report.json present + valid JSON",
                         rr.is_file() and "schema_version" in rr_data,
                         f"keys={list(rr_data.keys())[:5]}"))

    # 7. 5 baselines registered
    results.append(check("7. 5 baselines registered",
                         set(SUPPORTED_BASELINES) == {
                             "base_miniswe", "feedback_retry",
                             "broad_expansion", "condiag_packet_only", "condiag_retry"
                         }, f"baselines={SUPPORTED_BASELINES}"))

    # 8. agent=miniswe implemented; others planned
    agents = list_adapters()
    results.append(check("8. miniswe=implemented, others=planned",
                         agents["miniswe"]["status"] == "implemented"
                         and agents["agentless"]["status"] == "planned"
                         and agents["openhands"]["status"] == "planned"
                         and agents["swe_agent"]["status"] == "planned",
                         f"agents={agents}"))

    # 9. no gold/contextbench_metrics/official_eval in baseline_runner source
    src = Path(__file__).parent / "baseline_runner.py"
    src_text = src.read_text(encoding="utf-8")
    forbidden_in_source = ["contextbench_metrics", "gold_check", "official_eval",
                           "fail_to_pass", "pass_to_pass"]
    hits = [k for k in forbidden_in_source if k in src_text]
    results.append(check("9. baseline_runner.py does not read gold/eval fields",
                         not hits, f"hits={hits}"))

    # 10. validator hook callable + dry-run returns skipped_dry_run
    val = validate_run(instance_dir, "base_miniswe", "miniswe", mode="dry-run")
    results.append(check("10. validator hook returns skipped_dry_run in dry-run",
                         val["status"] == "skipped_dry_run", f"status={val['status']}"))

    # Extra: feedback_retry (packet_only) creates intervention/ but NOT attempt_2/
    rc = runner_main(["--agent", "miniswe", "--baseline", "feedback_retry",
                      "--instances", str(INSTANCES_FILE),
                      "--out", str(TMP / "runs_fr"), "--mode", "dry-run"])
    fr_dir = TMP / "runs_fr" / "miniswe" / "feedback_retry" / "django__django-11400"
    fr_subs = {p.name for p in fr_dir.iterdir() if p.is_dir()} if fr_dir.is_dir() else set()
    results.append(check("extra. feedback_retry (packet_only) creates attempt_1+intervention+final (NO attempt_2)",
                         {"attempt_1", "intervention", "final"}.issubset(fr_subs)
                         and "attempt_2" not in fr_subs,
                         f"got={sorted(fr_subs)}"))

    # Extra: validator detects gold_leakage when keyword present
    fake_run = TMP / "fake_leak" / "attempt_1"
    fake_run.mkdir(parents=True)
    (fake_run / "raw_trajectory.json").write_text(
        '{"oops": "contextbench_metrics leaked here"}', encoding="utf-8")
    val2 = validate_run(fake_run.parent, "base_miniswe", "miniswe", mode="smoke")
    results.append(check("extra. validator catches gold_leakage in smoke mode",
                         val2["status"] == "gold_leakage",
                         f"status={val2['status']}"))

    passed = sum(1 for ok in results if ok)
    total = len(results)
    print(f"\n=== {passed}/{total} acceptance checks passed ===")
    return passed == total


if __name__ == "__main__":
    _setup()
    ok = test_acceptance()
    sys.exit(0 if ok else 1)
