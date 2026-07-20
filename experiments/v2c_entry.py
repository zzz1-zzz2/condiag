"""ConDiag V2c Entry — Run a complete paired comparison episode.

Usage:
  DEEPSEEK_API_KEY=sk-xxx python3 -m experiments.v2c_entry --instance astropy__astropy-13398
  DEEPSEEK_API_KEY=sk-xxx python3 -m experiments.v2c_entry --pilot
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import sys; sys.path.insert(0, "/home/swelite/condiag")  # noqa: E702
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
log = logging.getLogger("v2c_entry")

V2C_ARTIFACTS = Path("/home/swelite/condiag/artifacts/v2c")


def run_single(instance_id: str, args: argparse.Namespace) -> dict:
    from condiag.instance_registry import InstanceRegistry
    from condiag.evaluators.official_harness import OfficialHarnessGateway
    from condiag.checkpoint import CheckpointManager
    from condiag.experiment import run_experiment
    from condiag.agent.config import AgentConfig, build_agent_factory, require_api_key

    require_api_key()

    reg = InstanceRegistry()
    spec = reg.get_instance(instance_id)
    if not spec:
        return {"error": f"Instance {instance_id} not found"}

    log.info("=" * 70)
    log.info("V2c: %s | repo=%s version=%s f2p=%d", instance_id, spec.repo, spec.version, len(spec.fail_to_pass))
    log.info("=" * 70)

    harness = OfficialHarnessGateway(run_id=f"v2c_{instance_id}", rm_image=False, force_rebuild=False, timeout=600)
    checkpointer = CheckpointManager(V2C_ARTIFACTS / instance_id / "round1")

    config = AgentConfig(
        model_name=args.model,
        temperature=args.temperature,
        max_tokens=4096,
        step_limit=0,
        cost_limit=5.0,
    )
    agent_factory = build_agent_factory(config, instance_id)

    # TODO(P1-5): Replace with condiag.diagnosis.diagnoser_core.DiagnoserCore
    from condiag.diagnosis_prompt_builder import DiagnosisPromptBuilder
    diagnosis_builder_cls = None if args.no_condiag else DiagnosisPromptBuilder

    result = run_experiment(
        instance_id=instance_id,
        agent_factory=agent_factory,
        harness=harness,
        checkpointer=checkpointer,
        output_dir=V2C_ARTIFACTS,
        instance_spec=spec,
        diagnosis_builder_cls=diagnosis_builder_cls,
    )
    return {"instance_id": instance_id, "result": result.to_dict()}


def dry_run_harness(instance_id: str):
    """Run a dry evaluation (empty patch + gold patch) to verify harness setup."""
    from condiag.instance_registry import InstanceRegistry
    from condiag.evaluators.official_harness import OfficialHarnessGateway
    from condiag.agent.config import require_api_key
    require_api_key()
    reg = InstanceRegistry()
    spec = reg.get_instance(instance_id)
    if not spec:
        log.error("Instance %s not found", instance_id)
        return
    gw = OfficialHarnessGateway(run_id=f"dry_{instance_id}")
    r = gw.evaluate(spec, model_patch="")
    log.info("Empty -> %s (%.1fs)", r.status, r.duration_seconds)
    if spec.gold_patch:
        r2 = gw.evaluate(spec, model_patch=spec.gold_patch)
        log.info("Gold  -> %s (%.1fs)", r2.status, r2.duration_seconds)
def run_pilot(args):
    from condiag.instance_registry import InstanceRegistry
    reg = InstanceRegistry()
    results = {}
    for i, spec in enumerate(reg.list_pilot()):
        log.info("\nPilot %s (%d/%d)", spec.instance_id, i + 1, 16)
        try:
            r = run_single(spec.instance_id, args)
            results[spec.instance_id] = r.get("result", {})
        except Exception as e:
            log.exception("Failed: %s", e)
            results[spec.instance_id] = {"error": str(e)}
    print("\n\n" + "=" * 70 + "\nPILOT SUMMARY\n" + "=" * 70)
    for iid, r in results.items():
        r1 = r.get("round1", {}).get("termination_reason", "?")
        sf = r.get("sf", {}).get("termination_reason", "?")
        cd = r.get("cd", {}).get("termination_reason", "?")
        print(f"  {iid:45s} R1={r1:12s} SF={sf:12s} CD={cd:12s}")
    (V2C_ARTIFACTS / "pilot_summary.json").write_text(json.dumps(results, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--instance", default=None)
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--model", default="openai/deepseek-v4-pro")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--no-condiag", action="store_true",
                   help="Skip CD branch entirely (only run R1 + SF)")

    args = p.parse_args()
    V2C_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        if not args.instance: log.error("--dry-run needs --instance"); sys.exit(1)
        dry_run_harness(args.instance)
    elif args.pilot: run_pilot(args)
    elif args.instance:
        r = run_single(args.instance, args)
        res = r.get("result", {})
        r1_reason = res.get("round1", {}).get("termination_reason", "?")
        sf_reason = res.get("sf", {}).get("termination_reason", "?")
        cd_reason = res.get("cd", {}).get("termination_reason", "?")
        print(f"\n=== {args.instance} ===\n  R1={r1_reason} SF={sf_reason} CD={cd_reason}")
    else: p.print_help()


if __name__ == "__main__":
    main()
