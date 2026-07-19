"""Experiment: thin orchestration of R1 → eval → fork → eval → comparison."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from condiag.round1_runner import run_round1, Round1Result
from condiag.branch_runner import run_branch, BranchResult
from condiag.branch_builder import build_branch_messages
from condiag.checkpoint import CheckpointManager
from condiag.compression.strategy import compress_messages, estimate_tokens, get_message_stats

logger = logging.getLogger("condiag.experiment")


def _sha(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


@dataclass
class ComparisonOutput:
    instance_id: str = ""
    evaluation_mode: str = "official"
    checkpoint_id: str = ""
    r1_messages_sha: str = ""
    r1_workspace_sha: str = ""
    r1_patch_sha: str = ""
    failure_witness_sha: str = ""
    sf_workspace_sha: str = ""
    cd_workspace_sha: str = ""
    sf_messages_sha: str = ""
    cd_messages_sha: str = ""
    round1: dict = field(default_factory=dict)
    sf: dict = field(default_factory=dict)
    cd: dict = field(default_factory=dict)
    round1_resolved: bool = False
    sf_resolved: bool = False
    cd_resolved: bool = False
    verdict: str = "tie"
    error: str = ""

    def to_dict(self):
        import dataclasses
        def _convert(v):
            if hasattr(v, '__dataclass_fields__'):
                return dataclasses.asdict(v)
            return v
        return {k: _convert(v) for k, v in self.__dict__.items()
                if not k.startswith('_') and not callable(v)}


def run_experiment(
    instance_id: str,
    *,
    agent_factory: Callable,
    harness: Any,
    checkpointer: CheckpointManager,
    output_dir: Path,
    instance_spec: Any,
    diagnosis_builder_cls: type | None = None,
) -> ComparisonOutput:
    """Full experiment: R1 → eval → fork SF/CD → eval → comparison."""
    out = ComparisonOutput(instance_id=instance_id)
    inst_dir = output_dir / instance_id
    for d in ("round1", "sf", "cd"):
        (inst_dir / d).mkdir(parents=True, exist_ok=True)

    task = instance_spec.problem_statement if hasattr(instance_spec, "problem_statement") else ""
    base_commit = instance_spec.base_commit if hasattr(instance_spec, "base_commit") else ""

    try:
        # ═══════ Round 1 ═══════
        logger.info("[%s] Round 1 begin", instance_id)
        r1 = run_round1(agent_factory=agent_factory, task=task, base_commit=base_commit)
        _write_patch(inst_dir / "round1" / "patch.diff", r1.patch_text)
        _write_trajectory(inst_dir / "round1" / "trajectory.json", r1.trajectory)

        out.round1 = asdict_skip(r1, ["messages", "trajectory"])

        if r1.termination_reason != "submitted":
            logger.info("[%s] R1 not submitted (%s) — abort", instance_id, r1.termination_reason)
            out.verdict = "both_fail"
            return out

        # ═══════ Official eval R1 ═══════
        logger.info("[%s] Eval R1 patch", instance_id)
        r1_eval = harness.evaluate(instance_spec, model_patch=r1.patch_text,
                                   run_id=f"r1_{_sha(r1.patch_text)}_{int(time.time())}")
        _write_json(inst_dir / "round1" / "harness_eval.json",
                    {"status": r1_eval.status, "duration": r1_eval.duration_seconds,
                     "report": r1_eval.report, "test_log_path": r1_eval.test_log_path})
        out.round1["harness_status"] = r1_eval.status
        out.round1_resolved = (r1_eval.status == "RESOLVED")
        if out.round1_resolved:
            out.verdict = "both_succeed"
            return out

        # ═══════ Checkpoint + FW ═══════
        fw = harness.extract_witness(r1_eval).to_dict()
        fw_preview = fw.get("failed_tests", [])[:3] or [fw.get("error_message", "")[:50]]
        logger.info("[%s] FW: %s", instance_id, fw_preview)

        out.checkpoint_id = _sha({"t": time.time(), "i": instance_id, "n": r1.n_calls})
        out.r1_messages_sha = _sha(r1.messages)
        out.r1_workspace_sha = _sha(r1.patch_text)
        out.r1_patch_sha = _sha(r1.patch_text)
        out.failure_witness_sha = _sha(fw)

        # Save checkpoint via CheckpointManager
        # (We need a mini-agent-like object with the right attrs — reconstruct from r1 data)
        _save_checkpoint(checkpointer, r1, base_commit)

        diag = None
        if diagnosis_builder_cls and fw.get("failed_tests"):
            from condiag.diagnosis_prompt_builder import DiagnosisPromptBuilder, TrajectorySnapshot
            diag = DiagnosisPromptBuilder().build(fw, TrajectorySnapshot())

        # ═══════ Compression ═══════
        compressed_messages = compress_messages(
            r1.messages,
            max_tool_output=500,
            max_turns=20,
            max_total_chars=80000,
        )
        pre_tok = estimate_tokens(r1.messages)
        post_tok = estimate_tokens(compressed_messages)
        logger.info("[%s] Compression: %s tok → %s tok (%.0f%% reduction)",
                     instance_id, pre_tok, post_tok,
                     (1 - post_tok / max(pre_tok, 1)) * 100)

        # ═══════ Fork: SF ═══════
        logger.info("[%s] SF branch begin", instance_id)
        sf = run_branch(
            agent_factory=agent_factory,
            checkpoint_messages=compressed_messages,
            base_commit=base_commit, task=task,
            patch_to_apply=r1.patch_text,
            r1_n_calls=r1.n_calls, r1_cost=r1.cost,
            failure_witness=fw, diagnosis=None, mode="sf",
        )
        _write_patch(inst_dir / "sf" / "patch.diff", sf.patch_text)
        _write_trajectory(inst_dir / "sf" / "trajectory.json", sf.trajectory)
        _eval_and_save(harness, instance_spec, sf.patch_text, inst_dir / "sf" / "harness_eval.json",
                       "sf", sf)

        out.sf = asdict_skip(sf, ["messages", "trajectory"])
        out.sf_workspace_sha = _sha(r1.patch_text)
        out.sf_messages_sha = _sha([m for m in sf.messages if m.get("role") != "exit"])
        out.sf_resolved = (sf.termination_reason == "submitted" and
                           out.sf.get("harness_status") == "RESOLVED")

        # ═══════ Fork: CD ═══════
        logger.info("[%s] CD branch begin", instance_id)
        cd = run_branch(
            agent_factory=agent_factory,
            checkpoint_messages=compressed_messages,
            base_commit=base_commit, task=task,
            patch_to_apply=r1.patch_text,
            r1_n_calls=r1.n_calls, r1_cost=r1.cost,
            failure_witness=fw, diagnosis=diag, mode="condiag",
        )
        _write_patch(inst_dir / "cd" / "patch.diff", cd.patch_text)
        _write_trajectory(inst_dir / "cd" / "trajectory.json", cd.trajectory)
        _eval_and_save(harness, instance_spec, cd.patch_text, inst_dir / "cd" / "harness_eval.json",
                       "cd", cd)

        out.cd = asdict_skip(cd, ["messages", "trajectory"])
        out.cd_workspace_sha = _sha(r1.patch_text)
        out.cd_messages_sha = _sha([m for m in cd.messages if m.get("role") != "exit"])
        out.cd_resolved = (cd.termination_reason == "submitted" and
                           out.cd.get("harness_status") == "RESOLVED")

        # ═══════ Verdict ═══════
        if out.sf_resolved and out.cd_resolved:
            out.verdict = "both_succeed"
        elif out.sf_resolved and not out.cd_resolved:
            out.verdict = "sf_wins"
        elif not out.sf_resolved and out.cd_resolved:
            out.verdict = "condiag_wins"
        else:
            out.verdict = "both_fail"

        ws_ok = out.sf_workspace_sha == out.cd_workspace_sha
        logger.info("[%s] verdict=%s fairness_ws=%s", instance_id, out.verdict, "OK" if ws_ok else "⚠️")

    except Exception as e:
        logger.exception("[%s] Experiment failed", instance_id)
        out.error = str(e)
        out.verdict = "error"
    finally:
        _write_comparison(inst_dir, out)

    return out


# ─── Helpers ───────────────────────────────────────────────────────────

def asdict_skip(obj, skip_keys):
    d = {}
    for k, v in vars(obj).items():
        if k not in skip_keys and not k.startswith("_"):
            d[k] = v
    return d


def _write_patch(p: Path, content: str):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)


def _write_json(p: Path, data: dict):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(data, indent=2))


def _write_trajectory(p: Path, data: dict):
    try: _write_json(p, data)
    except Exception: pass


def _save_checkpoint(checkpointer, r1: Round1Result, base_commit: str):
    class _Cfg:
        def model_dump(self, mode="json"):
            return {}
    class _Mod:
        model_name = ""
        model_kwargs = {}
    class _Env:
        container_id = ""
    class _Stub:
        messages = r1.messages
        phase = "round1"
        cost = r1.cost
        n_calls = r1.n_calls
        n_consecutive_format_errors = 0
        _start_time = time.time() - r1.duration_seconds
        extra_template_vars = {}
        config = _Cfg()
        model = _Mod()
        env = _Env()
    checkpointer.capture(_Stub(), base_commit=base_commit, round1_patch=r1.patch_text)

def _eval_and_save(harness, spec, patch, json_path, label, result):
    try:
        r = harness.evaluate(spec, model_patch=patch,
                             run_id=f"{label}_{_sha(patch)}_{int(time.time())}")
        _write_json(json_path, {"status": r.status, "duration": r.duration_seconds,
                                 "report": r.report, "test_log_path": r.test_log_path})
        result.__dict__["harness_status"] = r.status
    except Exception as e:
        result.__dict__["harness_status"] = f"ERROR:{e}"


def _write_comparison(inst_dir: Path, out: ComparisonOutput):
    p = inst_dir / "comparison.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out.to_dict(), indent=2))
