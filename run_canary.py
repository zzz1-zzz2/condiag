"""ConDiag V2c — Single canary instance runner.

Loads instance data from cached pickle.
Usage: source venv/bin/activate && python3 run_canary.py
"""
from __future__ import annotations
import json, logging, pickle, time
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
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.litellm_model import LitellmModel
    from minisweagent.run.benchmarks.swebench import get_swebench_docker_image_name

    image_name = get_swebench_docker_image_name({"instance_id": instance_id})
    log.info("  Docker image: %s", image_name)

    def factory():
        from condiag.integrated_agent import ConDiagIntegratedAgent
        env = DockerEnvironment(image=image_name, cwd="/testbed", timeout=120)
        model = LitellmModel(
            model_name="openai/deepseek-v4-pro",
            model_kwargs={
                "temperature": 0.0, "max_tokens": 4096,
                "api_base": "https://api.deepseek.com/v1",
                "api_key": "sk-9236ebc647c24f44bbb6fa47b24bd67b",
            },
            cost_tracking="ignore_errors",
        )
        agent = ConDiagIntegratedAgent(
            model=model, env=env,
            system_template="You are a helpful assistant that can interact with a computer shell to solve programming tasks.",
            instance_template="""<pr_description>
Consider the following PR description:
{{task}}
</pr_description>

<instructions>
# Task Instructions

## Overview

You're a software engineer interacting continuously with a computer by submitting commands.
You'll be helping implement necessary changes to meet requirements in the PR description.
Your task is specifically to make changes to non-test files in the current directory in order to fix the issue described in the PR description in a way that is general and consistent with the codebase.
<IMPORTANT>This is an interactive process where you will think and issue AT LEAST ONE command, see the result, then think and issue your next command(s).</important>

## Recommended Workflow

1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. Submit your changes by creating a patch and using the submit command

## Important Boundaries

- MODIFY: Regular source code files in /testbed (this is the working directory)
- DO NOT MODIFY: Tests, configuration files (pyproject.toml, setup.cfg, etc.)

## Submission

When you've completed your work, you MUST submit your changes as a git patch.
Follow these steps IN ORDER, with SEPARATE commands:

Step 1: Create the patch file
Run `git diff -- path/to/file1 path/to/file2 > patch.txt` listing only the source files you modified.

Step 2: Verify your patch
Inspect patch.txt to confirm it only contains your intended changes.

Step 3: Submit
You MUST use this EXACT command to submit:

```bash
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt
```

If the command fails (nonzero exit status), it will not submit.

<CRITICAL>
- Creating/viewing the patch and submitting it MUST be separate commands.
- You CANNOT continue working after submitting.
</CRITICAL>
</instructions>""",
            step_limit=0, cost_limit=5.0, output_path=None,
        )
        return agent
    return factory


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
    agent_factory = make_agent_factory(instance_id)

    result = run_experiment(
        instance_id=instance_id,
        agent_factory=agent_factory,
        harness=harness,
        checkpointer=checkpointer,
        output_dir=ARTIFACTS,
        instance_spec=spec,
        diagnosis_builder_cls=DiagnosisPromptBuilder,
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
