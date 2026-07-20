"""ConDiag V2c — Single canary instance runner.

Loads instance data from cached pickle.
Usage: DEEPSEEK_API_KEY=sk-xxx python3 run_canary.py
"""
from __future__ import annotations
import json, logging, os, pickle
from pathlib import Path
from typing import Any

import sys; sys.path.insert(0, "/home/zz/桌面/condiag")
logging.basicConfig(level=logging.INFO, format="%(name)s | %(message)s")
log = logging.getLogger("run_canary")

ARTIFACTS = Path("/home/zz/桌面/condiag/artifacts/v2c_canary")
ARTIFACTS.mkdir(parents=True, exist_ok=True)


def make_instance_spec(inst: dict) -> Any:
    from condiag.instance_registry import InstanceSpec
    f2p = inst.get("FAIL_TO_PASS", "[]")
    p2p = inst.get("PASS_TO_PASS", "[]")
    if isinstance(f2p, str): f2p = json.loads(f2p)
    if isinstance(p2p, str): p2p = json.loads(p2p)
    return InstanceSpec(
        instance_id=inst["instance_id"],
        repo=inst.get("repo", ""),
        base_commit=inst.get("base_commit", ""),
        problem_statement=inst.get("problem_statement", ""),
        gold_patch=inst.get("patch", ""),
        gold_context="",
        test_patch=inst.get("test_patch", ""),
        fail_to_pass=f2p, pass_to_pass=p2p,
        version=inst.get("version", ""),
        environment_setup_commit=inst.get("environment_setup_commit", ""),
        source="Verified", language="python",
        cb_instance_id=inst["instance_id"], pool="first_failed",
        _swebench_row=dict(inst),
    )


def load_instance(instance_id: str = "astropy__astropy-13398"):
    pkl = Path("/tmp/astropy_inst.pkl")
    if pkl.exists():
        with open(pkl, "rb") as f:
            return pickle.load(f)
    log.error("Cache not found. Run: HF_ENDPOINT=https://hf-mirror.com python3 -c '...'")
    return None


def make_agent_factory(instance_id: str):
    from condiag.agent.config import AgentConfig, RevisionProtocolConfig, build_agent_factory, require_api_key
    require_api_key()

    config = AgentConfig(
        protocol_name="persistent_revision",
        protocol_version="1.0",
        model_name="openai/deepseek-v4-pro",
        temperature=0.0,
        max_tokens=4096,
        step_limit=0,
        cost_limit=5.0,
    )
    log.info("  AgentConfig: protocol=%s/%s model=%s sha=%s",
             config.protocol_name, config.protocol_version, config.model_name, config.config_sha)
    return build_agent_factory(config, instance_id), config, RevisionProtocolConfig()


def run_canary(instance_id: str = "astropy__astropy-13398"):
    log.info("=" * 60)
    log.info("Canary: %s", instance_id)
    log.info("=" * 60)

    data = load_instance(instance_id)
    if data is None:
        return
    spec = make_instance_spec(data)
    log.info("Instance: repo=%s version=%s f2p=%d", spec.repo, spec.version, len(spec.fail_to_pass))

    from condiag.evaluators.official_harness import OfficialHarnessGateway
    from condiag.checkpoint import CheckpointManager
    from condiag.experiment import run_experiment
    from condiag.diagnosis_prompt_builder import DiagnosisPromptBuilder

    harness = OfficialHarnessGateway(
        run_id=f"canary_{instance_id}", rm_image=False, force_rebuild=False, timeout=600,
    )
    checkpointer = CheckpointManager(ARTIFACTS / instance_id / "round1")
    agent_factory, agent_config, rev_config = make_agent_factory(instance_id)

    result = run_experiment(
        instance_id=instance_id,
        agent_factory=agent_factory,
        harness=harness,
        checkpointer=checkpointer,
        output_dir=ARTIFACTS,
        instance_spec=spec,
        diagnosis_builder_cls=DiagnosisPromptBuilder,
        run_cd=True,
        agent_config=agent_config,
        revision_config=rev_config,
    )

    print("\n" + "=" * 60)
    print(f"RESULTS: {instance_id}")
    print(f"  R1:     {result.round1.get('termination_reason','?')} resolved={result.round1_resolved}")
    print(f"  SF:     {result.sf.get('termination_reason','?')} resolved={result.sf_resolved}")
    print(f"  CD:     {result.cd.get('termination_reason','?')} resolved={result.cd_resolved}")
    print(f"  Verdict: {result.verdict}")
    if result.error:
        print(f"  Error: {result.error}")
    print("=" * 60)
    return result


if __name__ == "__main__":
    run_canary()
