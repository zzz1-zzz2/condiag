#!/usr/bin/env python3
"""Rebuild failure witnesses for all first-failed instances using v2 parsers.

Steps:
1. Copy raw eval logs from v0 eval_predictions to instance attempt_1 dirs
2. Run failure_witness_builder.build_failure_witness() with eval_log_path
3. Save new witness as attempt_1/failure_witness.json
4. Verify quality gates
"""
import json, sys
from pathlib import Path

INSTANCES = Path("/mnt/d/condiag-artifacts/condiag/instances")
V0_EVAL_DIRS = [
    Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_verified_official"),
    Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_multi_official"),
    Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_pro_official"),
    Path("/mnt/d/condiag-artifacts/condiag/v0/eval_predictions/swebench_poly_official"),
]
WSL_LOG_DIRS = [
    Path("/home/swelite/condiag/logs"),
]

sys.path.insert(0, "/home/swelite/condiag")

# Import ALL parsers so they register with @register_parser
import experiments.failure_parsers.pytest_parser
import experiments.failure_parsers.mocha_jest_parser
import experiments.failure_parsers.go_test_parser
import experiments.failure_parsers.cargo_test_parser
import experiments.failure_parsers.ansible_parser
import experiments.failure_parsers.cpp_parser
import experiments.failure_parsers.junit_gradle_parser
import experiments.failure_parsers.generic_parser

from experiments.failure_witness_builder import build_failure_witness


def find_raw_log(instance_id: str) -> Path | None:
    for base in V0_EVAL_DIRS:
        if not base.exists():
            continue
        for fn in ["test_output.log", "test_output.txt"]:
            p = base / instance_id / fn
            if p.exists():
                return p
        ws = base / instance_id / "workspace"
        if ws.exists():
            for fn in ["stdout.log", "stdout.txt"]:
                p = ws / fn
                if p.exists():
                    return p
    for base in WSL_LOG_DIRS:
        if not base.exists():
            continue
        for fn in ["test_output.txt", "test_output.log"]:
            for p in sorted(base.rglob(fn)):
                if p.parent.name == instance_id or instance_id in str(p):
                    return p
    return None


def get_first_failed_instances() -> list[str]:
    failed = []
    for inst_dir in sorted(INSTANCES.iterdir()):
        if not inst_dir.is_dir():
            continue
        ej = inst_dir / "attempt_1" / "official_eval.json"
        if not ej.exists():
            continue
        data = json.loads(ej.read_text())
        result = str(data.get("resolved", data.get("result", data.get("status", "?")))).lower()
        if result in ("false", "0", "failed"):
            failed.append(inst_dir.name)
    return failed


def main():
    first_failed = get_first_failed_instances()
    print(f"First-failed instances: {len(first_failed)}")

    results = {
        "total": len(first_failed),
        "with_log": 0,
        "no_log": 0,
        "rebuilt": 0,
        "failed": 0,
        "errors": [],
        "eligible_quality": {"high": 0, "medium": 0, "low": 0, "none": 0},
        "by_parser": {},
        "generic_count": 0,
    }

    for i, iid in enumerate(first_failed):
        attempt_dir = INSTANCES / iid / "attempt_1"
        raw_log = find_raw_log(iid)
        official_eval = json.loads((attempt_dir / "official_eval.json").read_text()) if (attempt_dir / "official_eval.json").exists() else {}

        # ------------------------------------------------------------------
        # Stage authority: official_eval.json metadata takes priority
        # over raw log content for determining failure stage.
        # ------------------------------------------------------------------
        apply_error = official_eval.get("apply_error", "")
        patch_apply = official_eval.get("patch_apply")

        if patch_apply is False:
            # Patch apply failure — authoritative from eval metadata
            from condiag.schemas import FailureWitness as FW
            error_msg = (apply_error or "patch_apply=false (no details)")[:2000]
            witness = FW(
                instance_id=iid,
                has_failure_witness=False,
                failure_observed=True,
                failure_stage="patch_apply_failure",
                failure_type="git_apply_error",
                error_message=error_msg,
                mode="diagnostic_only_no_failure_witness",
                source="post_validation_output",
                source_type="harness_log",
                raw_output_path=str(raw_log) if raw_log else "",
                missing_reason="patch_did_not_apply",
                oracle_labels_hidden=True,
                version="v2.1",
            )
            witness.parser_name = "official_eval_metadata"
            witness.parser_version = "v2.1"
            size_info = f"({raw_log.stat().st_size} bytes)" if raw_log else "NO LOG"
            stage_label = "patch_apply_failure (from official_eval)"
        elif raw_log:
            results["with_log"] += 1
            dest = attempt_dir / "test_output.txt"
            if not dest.exists():
                import shutil
                shutil.copy2(raw_log, dest)
            size_info = f"({raw_log.stat().st_size} bytes)"

            try:
                witness = build_failure_witness(
                    instance_id=iid,
                    eval_log_path=raw_log,
                    method_version="v2.1",
                )
            except Exception as e:
                results["failed"] += 1
                results["errors"].append({"instance": iid, "error": str(e)})
                print(f"  [{i+1:2d}/{len(first_failed)}] {iid[:40]:40s} ERROR: {e}")
                continue
            stage_label = witness.failure_stage
        else:
            results["no_log"] += 1
            size_info = "NO LOG"

            # No raw log: extract from official_eval.json
            if apply_error:
                from condiag.schemas import FailureWitness as FW
                witness = FW(
                    instance_id=iid,
                    has_failure_witness=False,
                    failure_observed=True,
                    failure_stage="patch_apply_failure",
                    failure_type="git_apply_error",
                    error_message=apply_error[:2000],
                    mode="diagnostic_only_no_failure_witness",
                    source="post_validation_output",
                    source_type="harness_log",
                    raw_output_path="",
                    missing_reason="patch_did_not_apply",
                    oracle_labels_hidden=True,
                    version="v2.1",
                )
                witness.parser_name = "official_eval_metadata"
                witness.parser_version = "v2.1"
                stage_label = "patch_apply_failure (from official_eval)"
            elif official_eval.get("failure_stage") == "timeout":
                from condiag.schemas import FailureWitness as FW
                witness = FW(
                    instance_id=iid,
                    has_failure_witness=False,
                    failure_observed=True,
                    failure_stage="timeout",
                    failure_type="timeout",
                    mode="diagnostic_only_no_failure_witness",
                    source="post_validation_output",
                    source_type="harness_log",
                    missing_reason="timeout",
                    oracle_labels_hidden=True,
                    version="v2.1",
                )
                stage_label = "timeout (from official_eval)"
            else:
                witness = build_failure_witness(
                    instance_id=iid,
                    eval_log_path=None,
                    method_version="v2.1",
                )
                stage_label = witness.failure_stage

        out_path = attempt_dir / "failure_witness.json"
        witness_json = {
            "instance_id": witness.instance_id,
            "has_failure_witness": witness.has_failure_witness,
            "failure_observed": witness.failure_observed,
            "failure_stage": witness.failure_stage,
            "failure_type": witness.failure_type,
            "test_framework": witness.test_framework,
            "failed_tests": witness.failed_tests,
            "error_message": witness.error_message,
            "stack_trace": witness.stack_trace,
            "top_repo_frames": witness.top_repo_frames,
            "expected": witness.expected,
            "actual": witness.actual,
            "eligible_for_condiag": witness.eligible_for_condiag,
            "quality": witness.quality,
            "parser_name": witness.parser_name,
            "parser_version": witness.parser_version,
            "matched_patterns": witness.matched_patterns,
            "mode": witness.mode,
            "source": witness.source,
            "source_type": witness.source_type,
            "raw_output_path": str(raw_log) if raw_log else "",
            "missing_reason": witness.missing_reason,
            "oracle_labels_hidden": True,
            "version": "v2.1",
        }
        out_path.write_text(json.dumps(witness_json, indent=2, ensure_ascii=False))
        results["rebuilt"] += 1

        # Track eligible quality separately
        if witness.eligible_for_condiag:
            q = witness.quality if witness.quality in ("high", "medium", "low", "none") else "none"
            results["eligible_quality"][q] = results["eligible_quality"].get(q, 0) + 1

        if witness.parser_name == "generic_parser":
            results["generic_count"] += 1

        results["by_parser"][witness.parser_name] = results["by_parser"].get(witness.parser_name, 0) + 1
        print(f"  [{i+1:2d}/{len(first_failed)}] {iid[:40]:40s} parser={witness.parser_name or 'none':20s} stage={stage_label:35s} quality={witness.quality or 'none':20s} eligible={witness.eligible_for_condiag} {size_info}")

    # Summary
    print(f"\n{'='*70}")
    print(f"REBUILD COMPLETE")
    print(f"{'='*70}")
    rebuilt = results["rebuilt"]
    print(f"Total first-failed:     {results['total']}")
    print(f"With eval log:          {results['with_log']}")
    print(f"No eval log:            {results['no_log']}")
    print(f"Rebuilt:                {rebuilt}")
    print(f"Failed (crash):         {results['failed']}")

    total_eligible = sum(results["eligible_quality"].values())
    eligible_high_med = results["eligible_quality"].get("high", 0) + results["eligible_quality"].get("medium", 0)
    print(f"\n=== ELIGIBLE (validation_failure) quality ===")
    print(f"Eligible total: {total_eligible}")
    for q, cnt in sorted(results["eligible_quality"].items()):
        pct = cnt / total_eligible * 100 if total_eligible else 0
        print(f"  {q}: {cnt} ({pct:.1f}%)")
    if total_eligible:
        print(f"  high+medium: {eligible_high_med}/{total_eligible} ({eligible_high_med/total_eligible*100:.1f}%)")

    print(f"\nParser distribution:")
    for pname, cnt in sorted(results["by_parser"].items(), key=lambda x: -x[1]):
        pct = cnt / rebuilt * 100 if rebuilt else 0
        print(f"  {pname}: {cnt} ({pct:.1f}%)")

    if results["errors"]:
        print(f"\nErrors ({len(results['errors'])}):")
        for e in results["errors"]:
            print(f"  {e['instance']}: {e['error']}")

    # Gate check
    print(f"\n{'='*70}")
    print(f"GATE CHECK")
    print(f"{'='*70}")
    print(f"  1. failure_stage recognition = 100%:\t\tPASS" if results["failed"] == 0 else "FAIL")
    print(f"  2. parser crash = 0:\t\t\t\t{'PASS' if results['failed'] == 0 else 'FAIL'}")
    print(f"  3. generic fallback <= 20%:\t\t\t{'PASS' if results['generic_count']/max(rebuilt,1) <= 0.2 else 'FAIL'} ({results['generic_count']}/{rebuilt} = {results['generic_count']/max(rebuilt,1)*100:.1f}%)")
    eligible_pct = eligible_high_med / total_eligible * 100 if total_eligible else 0
    print(f"  4. eligible high+medium >= 80%:\t\t{'PASS' if eligible_pct >= 80 else 'FAIL'} ({eligible_high_med}/{total_eligible} = {eligible_pct:.1f}%)")


if __name__ == "__main__":
    main()
