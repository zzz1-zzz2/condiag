"""ConDiag baseline runner CLI (D4-3 skeleton).

Creates the standard artifact directory tree under
    <out>/<agent>/<baseline>/<instance_id>/
and dispatches to the per-baseline handler (currently all stubs, D4-4~D4-7
will fill them in).

Run examples:

    # Dry-run (creates skeleton, no real agent invocation)
    python3 -m experiments.baseline_runner \\
        --agent miniswe \\
        --baseline base_miniswe \\
        --instances instances.txt \\
        --out /mnt/d/condiag-artifacts/condiag/v0/pilot50/runs \\
        --mode dry-run

    # Smoke (real run, 1-2 instances)
    python3 -m experiments.baseline_runner \\
        --agent miniswe --baseline base_miniswe \\
        --instances instances.txt --out ... --mode smoke --limit 2
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from condiag.adapters import get_adapter, list_adapters

from .baseline_handlers import BASELINE_HANDLERS, get_handler
from .artifact_validator import validate_run


SUPPORTED_BASELINES = sorted(BASELINE_HANDLERS.keys())
SUPPORTED_MODES = ["dry-run", "smoke", "full"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_instances(instances_arg: str, limit: Optional[int] = None) -> list[str]:
    """Load instance list from a file (one per line) or a comma-separated string."""
    p = Path(instances_arg)
    if p.is_file():
        ids = [line.strip() for line in p.read_text(encoding="utf-8").splitlines()
               if line.strip() and not line.strip().startswith("#")]
    else:
        ids = [s.strip() for s in instances_arg.split(",") if s.strip()]
    if limit:
        ids = ids[:limit]
    return ids


def _make_run_report_skeleton(
    agent: str,
    baseline: str,
    instance_id: str,
    mode: str,
) -> dict:
    return {
        "schema_version": "condiag.run_report.v0",
        "artifact_schema_version": "condiag.baseline_artifacts.v0",
        "agent": agent,
        "baseline": baseline,
        "instance_id": instance_id,
        "mode": mode,
        "status": "planned",
        "started_at": _now_iso(),
        "finished_at": None,
        "has_attempt_1": False,
        "has_intervention": False,
        "has_attempt_2": False,
        "has_final": False,
        "attempt_1_status": None,           # filled by handler: completed | failed | skipped
        "attempt_2_status": None,
        "final_source": None,               # which attempt was selected: "attempt_1" / "attempt_2"
        "handler_result": None,
        "validator_status": None,
        "validator_details": None,
        "errors": [],
        "warnings": [],
    }


def _ensure_run_dir_skeleton(run_dir: Path, baseline: str) -> dict:
    """Create the standard subdir tree. Returns has_* flags actually created.

    Subdirs that are NOT applicable to the baseline are not created.
    """
    run_dir.mkdir(parents=True, exist_ok=True)

    applicable = {
        "attempt_1": True,                          # every baseline has attempt_1
        "intervention": baseline != "base_miniswe", # base has no intervention
        # attempt_2: only created in real retry baselines (D4-5b+).
        # feedback_retry / broad_expansion / condiag_packet_only in v0 are
        # packet_only (no attempt_2). When D4-5b adds real retry, the
        # baseline name will be feedback_retry_full / condiag_retry.
        "attempt_2": baseline in {
            "condiag_retry",
        },
        "final": True,
    }
    for sub, needed in applicable.items():
        if needed:
            (run_dir / sub).mkdir(exist_ok=True)
    return {
        "has_attempt_1": applicable["attempt_1"],
        "has_intervention": applicable["intervention"],
        "has_attempt_2": applicable["attempt_2"],
        "has_final": applicable["final"],
    }


def run_one(
    agent: str,
    baseline: str,
    instance_id: str,
    out_root: Path,
    mode: str,
    config: Optional[dict] = None,
) -> dict:
    """Process a single instance. Writes run_report.json under the run dir.

    Returns the run_report dict.
    """
    adapter = get_adapter(agent)
    if adapter.status != "implemented":
        raise SystemExit(
            f"agent '{agent}' status is '{adapter.status}', not 'implemented'; "
            f"cannot run baseline (only miniswe is implemented in v0)."
        )

    handler = get_handler(baseline)
    run_dir = out_root / agent / baseline / instance_id
    has_flags = _ensure_run_dir_skeleton(run_dir, baseline)

    report = _make_run_report_skeleton(agent, baseline, instance_id, mode)
    report.update(has_flags)

    try:
        handler_result = handler(
            run_dir=run_dir,
            instance_id=instance_id,
            mode=mode,
            adapter=adapter,
            config=config or {},
        )
        report["handler_result"] = handler_result
        report["status"] = "completed" if handler_result.get("handled") else "stub_completed"
        # handler can drive attempt_status / final_source via handler_result
        for k in ("attempt_1_status", "attempt_2_status", "final_source"):
            if k in handler_result:
                report[k] = handler_result[k]
    except Exception as e:
        report["status"] = "aborted"
        report["errors"].append(f"handler_exception: {type(e).__name__}: {e}")
        report["handler_result"] = {"handled": False, "reason": "exception"}

    # write run_report.json BEFORE validator runs, so validator can see it
    # (run_report.json is in the required artifacts list)
    report["finished_at"] = _now_iso()
    (run_dir / "run_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # validator hook (skipped in dry-run)
    validator_result = validate_run(run_dir, baseline, agent, mode=mode)
    report["validator_status"] = validator_result["status"]
    report["validator_details"] = validator_result

    # re-write run_report.json with validator result
    (run_dir / "run_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="experiments.baseline_runner",
        description="ConDiag baseline runner (D4-3 skeleton)",
    )
    parser.add_argument("--agent", required=True,
                        help=f"agent name; one of: {sorted(list_adapters().keys())}")
    parser.add_argument("--baseline", required=True,
                        help=f"baseline name; one of: {SUPPORTED_BASELINES}")
    parser.add_argument("--instances", required=True,
                        help="path to instance list file (one per line) OR comma-separated ids")
    parser.add_argument("--out", required=True,
                        help="root dir; final layout: <out>/<agent>/<baseline>/<instance>/")
    parser.add_argument("--mode", default="dry-run", choices=SUPPORTED_MODES,
                        help="dry-run = skeleton only; smoke = real run limited; full = real run all")
    parser.add_argument("--limit", type=int, default=None,
                        help="cap instances processed (smoke testing)")
    parser.add_argument("--manifest", default=None,
                        help="path to manifest CSV (built by manifest_builder); required for "
                             "smoke/full mode in from_existing_traj baselines like base_miniswe")
    parser.add_argument("--base-run-root", default=None,
                        help="root dir containing miniswe/base_miniswe/<instance>/; "
                             "feedback_retry / condiag_packet_only read attempt_1 from here. "
                             "Default: same as --out (baselines run as siblings).")
    args = parser.parse_args(argv)

    # arg validation
    if args.agent not in list_adapters():
        print(f"[ERROR] unknown agent: {args.agent!r}; registered: {sorted(list_adapters().keys())}",
              file=sys.stderr)
        return 2
    if args.baseline not in BASELINE_HANDLERS:
        print(f"[ERROR] unknown baseline: {args.baseline!r}; supported: {SUPPORTED_BASELINES}",
              file=sys.stderr)
        return 2

    instances = _load_instances(args.instances, args.limit)
    if not instances:
        print(f"[ERROR] no instances loaded from {args.instances!r}", file=sys.stderr)
        return 2

    # Load manifest if provided (passed to handlers via config["manifest"])
    manifest_dict: dict[str, dict] = {}
    if args.manifest:
        from .manifest_builder import load_manifest
        if not Path(args.manifest).is_file():
            print(f"[ERROR] --manifest file not found: {args.manifest}", file=sys.stderr)
            return 2
        manifest_dict = load_manifest(Path(args.manifest))
        missing_in_manifest = [i for i in instances if i not in manifest_dict]
        if missing_in_manifest and args.mode != "dry-run":
            print(f"[WARN] {len(missing_in_manifest)} instance(s) not in manifest (will be skipped):",
                  file=sys.stderr)
            for iid in missing_in_manifest[:5]:
                print(f"         {iid}", file=sys.stderr)

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"=== baseline_runner ===")
    print(f"  agent:     {args.agent}")
    print(f"  baseline:  {args.baseline}")
    print(f"  mode:      {args.mode}")
    print(f"  instances: {len(instances)}")
    print(f"  out_root:  {out_root}")
    if manifest_dict:
        print(f"  manifest:  {args.manifest} ({len(manifest_dict)} rows)")
    if args.base_run_root:
        print(f"  base_run_root: {args.base_run_root}")
    print()

    base_run_root = Path(args.base_run_root) if args.base_run_root else out_root

    results = []
    for i, instance_id in enumerate(instances, 1):
        print(f"[{i}/{len(instances)}] {instance_id}")
        report = run_one(
            agent=args.agent,
            baseline=args.baseline,
            instance_id=instance_id,
            out_root=out_root,
            mode=args.mode,
            config={
                "manifest": manifest_dict,
                "base_run_root": base_run_root,
            },
        )
        results.append(report)
        print(f"        status={report['status']}  validator={report['validator_status']}")

    # summary
    print()
    print(f"=== summary: {len(results)} runs ===")
    by_status: dict[str, int] = {}
    for r in results:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    for status, count in sorted(by_status.items()):
        print(f"  {status:20s} {count}")
    print(f"  {'validator_ok':20s} "
          f"{sum(1 for r in results if r['validator_status'] in ('ok', 'skipped_dry_run'))}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
