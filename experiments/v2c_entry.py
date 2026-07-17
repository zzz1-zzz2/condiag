"""ConDiag v4 V2c Entry — Run a complete paired comparison episode.

Usage:
  python3 -m experiments.v2c_entry --instance django__django-11820
  python3 -m experiments.v2c_entry --instance sympy__sympy-20428 --force
  python3 -m experiments.v2c_entry --pilot
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, "/home/swelite/condiag")
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
log = logging.getLogger("v2c_entry")

V2C_ARTIFACTS = Path("/home/swelite/condiag/artifacts/v2c")


def build_agent_factory(instance_id: str, model_name: str = "deepseek/deepseek-v4-pro",
                        temperature: float = 0.0):
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.litellm_model import LitellmModel
    from minisweagent.run.benchmarks.swebench import get_swebench_docker_image_name

    pred = {"instance_id": instance_id}
    image_name = get_swebench_docker_image_name(pred)
    log.info("  Docker image: %s", image_name)

    def factory():
        from condiag.integrated_agent import ConDiagIntegratedAgent
        env = DockerEnvironment(image=image_name, cwd="/testbed", timeout=120)
        model = LitellmModel(model_name=model_name, model_kwargs={"temperature": temperature, "max_tokens": 1024})
        agent = ConDiagIntegratedAgent(
            model=model, env=env,
            system_template="You are a software engineer. You can run bash commands. "
                            "Read files with cat, edit with sed or python. When done, "
                            "run `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.",
            instance_template="{{task}}",
            step_limit=0, cost_limit=5.0, output_path=None,
        )
        return agent
    return factory


def run_single(instance_id: str, args: argparse.Namespace) -> dict:
    from condiag.instance_registry import InstanceRegistry
    from condiag.evaluators.official_harness import OfficialHarnessGateway
    from condiag.checkpoint import CheckpointManager
    from condiag.experiment import run_experiment

    reg = InstanceRegistry()
    spec = reg.get_instance(instance_id)
    if not spec:
        return {"error": f"Instance {instance_id} not found"}

    log.info("=" * 70)
    log.info("V2c: %s | repo=%s version=%s f2p=%d", instance_id, spec.repo, spec.version, len(spec.fail_to_pass))
    log.info("=" * 70)

    harness = OfficialHarnessGateway(run_id=f"v2c_{instance_id}", rm_image=False, force_rebuild=False, timeout=600)
    checkpointer = CheckpointManager(V2C_ARTIFACTS / instance_id / "round1")
    agent_factory = build_agent_factory(instance_id=instance_id, model_name=args.model, temperature=args.temperature)

    from condiag.diagnosis_prompt_builder import DiagnosisPromptBuilder
    result = run_experiment(
        instance_id=instance_id,
        agent_factory=agent_factory,
        harness=harness,
        checkpointer=checkpointer,
        output_dir=V2C_ARTIFACTS,
        instance_spec=spec,
        diagnosis_builder_cls=DiagnosisPromptBuilder if not args.no_condiag else None,
    )
    return {"instance_id": instance_id, "result": result.to_dict()}


def dry_run_harness(instance_id: str):
    from condiag.instance_registry import InstanceRegistry
    from condiag.evaluators.official_harness import OfficialHarnessGateway
    reg = InstanceRegistry()
    spec = reg.get_instance(instance_id)
    if not spec: return
    gw = OfficialHarnessGateway(run_id=f"dry_{instance_id}")
    r = gw.evaluate(spec, model_patch="")
    log.info("Empty -> %s (%.1fs) mode=%s", r.status, r.duration_seconds, r.mode)
    if spec.gold_patch:
        r2 = gw.evaluate(spec, model_patch=spec.gold_patch)
        log.info("Gold  -> %s (%.1fs) mode=%s", r2.status, r2.duration_seconds, r2.mode)


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
        r1 = r.get("round1", {}).get("status", "?")
        sf = r.get("stateful_feedback", {}).get("status", "?")
        cd = r.get("condiag", {}).get("status", "?")
        print(f"  {iid:45s} R1={r1:12s} SF={sf:12s} CD={cd:12s}")
    (V2C_ARTIFACTS / "pilot_summary.json").write_text(json.dumps(results, indent=2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--instance", default=None)
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true")
    p.add_argument("--model", default="deepseek/deepseek-v4-pro")
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--no-condiag", action="store_true")

    args = p.parse_args()
    V2C_ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if args.dry_run:
        if not args.instance: log.error("--dry-run needs --instance"); sys.exit(1)
        dry_run_harness(args.instance)
    elif args.pilot: run_pilot(args)
    elif args.instance:
        r = run_single(args.instance, args)
        res = r.get("result", {})
        r1, sf, cd = res.get("round1", {}).get("status", "?"), res.get("stateful_feedback", {}).get("status", "?"), res.get("condiag", {}).get("status", "?")
        print(f"\n=== {args.instance} ===\n  R1={r1} SF={sf} CD={cd}")
    else: p.print_help()


if __name__ == "__main__":
    main()
