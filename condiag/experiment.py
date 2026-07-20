"""Experiment: thin orchestration of R1 → eval → fork → eval → comparison."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
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


def _sha256_full(d) -> str:
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


@dataclass
class ComparisonOutput:
    instance_id: str = ""
    episode_run_id: str = ""
    evaluation_mode: str = "official"
    # Config identity
    config_sha: str = ""
    config_sha_full: str = ""
    revision_protocol_sha: str = ""
    source_yaml_sha: str = ""
    # Fairness
    fairness_ok: bool = False
    r1_workspace_sha: str = ""
    sf_preflight_sha: str = ""
    cd_preflight_sha: str = ""
    # R1
    r1_messages_sha: str = ""
    r1_workspace_sha: str = ""
    r1_patch_sha: str = ""
    failure_witness_sha: str = ""
    round1: dict = field(default_factory=dict)
    round1_resolved: bool = False
    # SF
    sf_workspace_sha: str = ""
    sf_messages_sha: str = ""
    sf: dict = field(default_factory=dict)
    sf_resolved: bool = False
    sf_run: bool = False
    # CD
    cd_workspace_sha: str = ""
    cd_messages_sha: str = ""
    cd: dict = field(default_factory=dict)
    cd_resolved: bool = False
    cd_run: bool = False
    # Overall
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
    run_cd: bool = True,
    agent_config: Any = None,      # AgentConfig instance — for config_sha tracking
    revision_config: Any = None,   # RevisionProtocolConfig instance
) -> ComparisonOutput:
    """Full experiment: R1 → eval → fork SF/CD → eval → comparison.

    Args:
        run_cd: If False, skip CD branch entirely. Used for --no-condiag mode.
        agent_config: AgentConfig instance (for logging config_sha to manifest).
        revision_config: RevisionProtocolConfig instance.
    """
    out = ComparisonOutput(instance_id=instance_id)

    # Save config identity
    episode_run_id = f"run_{time.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    out.episode_run_id = episode_run_id
    if agent_config:
        out.config_sha = getattr(agent_config, "config_sha", "")
        # config_sha_full is the 64-char SHA of the full config content, not a hash of the short SHA
        raw = (
            f"protocol={getattr(agent_config, 'protocol_name', '')}:{getattr(agent_config, 'protocol_version', '')}"
            f"|model={getattr(agent_config, 'model_name', '')}"
            f"|temp={getattr(agent_config, 'temperature', '')}"
            f"|maxtok={getattr(agent_config, 'max_tokens', '')}"
            f"|step={getattr(agent_config, 'step_limit', '')}"
            f"|cost={getattr(agent_config, 'cost_limit', '')}"
            f"|yaml={getattr(agent_config, 'source_yaml_sha', '')}"
            f"|rev={getattr(revision_config, 'sha', '') if revision_config else ''}"
        )
        out.config_sha_full = hashlib.sha256(raw.encode()).hexdigest()
        out.source_yaml_sha = getattr(agent_config, "source_yaml_sha", "")
    if revision_config:
        out.revision_protocol_sha = getattr(revision_config, "sha", "")

    # Use episode_run_id in output directory
    out_dir = output_dir / instance_id
    inst_dir = out_dir / episode_run_id
    inst_dir.mkdir(parents=True, exist_ok=True)

    # Write run_manifest.json
    _write_json(inst_dir / "run_manifest.json", {
        "instance_id": instance_id,
        "episode_run_id": episode_run_id,
        "git_commit": _get_git_commit(),
        "config_sha": out.config_sha,
        "config_sha_full": out.config_sha_full,
        "revision_protocol_sha": out.revision_protocol_sha,
        "source_yaml_sha": out.source_yaml_sha,
        "api_base_host": os.environ.get("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1"),
        "model_name": getattr(agent_config, "model_name", "") if agent_config else "",
        "start_time_epoch": time.time(),
        "evaluation_mode": out.evaluation_mode,
    })

    task = instance_spec.problem_statement if hasattr(instance_spec, "problem_statement") else ""
    base_commit = instance_spec.base_commit if hasattr(instance_spec, "base_commit") else ""

    try:
        # ═══════ Round 1 ═══════
        logger.info("[%s] Round 1 begin", instance_id)
        r1 = run_round1(agent_factory=agent_factory, task=task, base_commit=base_commit,
                        protocol_config=revision_config,
                        snapshot_dir=inst_dir / "round1")
        _write_patch(inst_dir / "round1" / "patch.diff", r1.patch_text)
        _write_trajectory(inst_dir / "round1" / "trajectory.json", r1.trajectory)

        out.round1 = asdict_skip(r1, ["messages", "trajectory", "workspace_snapshot"])

        if r1.termination_reason != "submitted":
            logger.info("[%s] R1 not submitted (%s) — abort", instance_id, r1.termination_reason)
            out.verdict = "r1_not_submitted"
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
            logger.info("[%s] R1 resolved — episode valid, skipping SF/CD", instance_id)
            out.verdict = "r1_resolved"
            return out

        # ═══════ FW + Checkpoint ═══════
        fw = harness.extract_witness(r1_eval).to_dict()
        fw_preview = fw.get("failed_tests", [])[:3] or [fw.get("error_message", "")[:50]]
        logger.info("[%s] FW: %s", instance_id, fw_preview)

        out.r1_messages_sha = _sha(r1.messages)
        out.r1_patch_sha = _sha(r1.patch_text)
        out.failure_witness_sha = _sha(fw)
        _save_checkpoint(checkpointer, r1, base_commit)

        # ═══════ Workspace Snapshot ═══════
        # Captured in run_round1() from the live container
        r1_snapshot = getattr(r1, "workspace_snapshot", None)
        if r1_snapshot:
            _write_json(inst_dir / "round1" / "workspace_snapshot.json", r1_snapshot.to_dict())
            logger.info("[%s] Workspace snapshot: state_sha=%s tracked=%s",
                         instance_id, r1_snapshot.workspace_state_sha,
                         r1_snapshot.tracked_diff_sha)
        else:
            logger.warning("[%s] No workspace snapshot from R1", instance_id)

        if r1_snapshot is None:
            logger.error("[%s] R1 snapshot capture failed — episode blocked before SF/CD", instance_id)
            out.verdict = "invalid_snapshot"
            return out

        # ═══════ Diagnosis ═══════
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
        out.sf_run = True
        sf = run_branch(
            agent_factory=agent_factory,
            checkpoint_messages=compressed_messages,
            base_commit=base_commit, task=task,
            r1_n_calls=r1.n_calls, r1_cost=r1.cost,
            failure_witness=fw, diagnosis=None, mode="sf",
            protocol_config=revision_config,
            workspace_snapshot=r1_snapshot,
        )
        _write_patch(inst_dir / "sf" / "patch.diff", sf.patch_text)
        _write_trajectory(inst_dir / "sf" / "trajectory.json", sf.trajectory)
        if sf.termination_reason == "submitted":
            _eval_and_save(harness, instance_spec, sf.patch_text, inst_dir / "sf" / "harness_eval.json",
                           "sf", sf)
        else:
            logger.info("[%s] SF not submitted (%s) — skip eval", instance_id, sf.termination_reason)
        out.sf = asdict_skip(sf, ["messages", "trajectory"])
        out.sf_messages_sha = _sha([m for m in sf.messages if m.get("role") != "exit"])
        out.sf_resolved = (sf.termination_reason == "submitted" and
                           out.sf.get("harness_status") == "RESOLVED")

        # ═══════ Fork: CD (optional) ═══════
        if run_cd:
            logger.info("[%s] CD branch begin", instance_id)
            out.cd_run = True
            cd = run_branch(
                agent_factory=agent_factory,
                checkpoint_messages=compressed_messages,
                base_commit=base_commit, task=task,
                r1_n_calls=r1.n_calls, r1_cost=r1.cost,
                failure_witness=fw, diagnosis=diag, mode="condiag",
                protocol_config=revision_config,
                workspace_snapshot=r1_snapshot,
            )
            _write_patch(inst_dir / "cd" / "patch.diff", cd.patch_text)
            _write_trajectory(inst_dir / "cd" / "trajectory.json", cd.trajectory)
            if cd.termination_reason == "submitted":
                _eval_and_save(harness, instance_spec, cd.patch_text, inst_dir / "cd" / "harness_eval.json",
                               "cd", cd)
            else:
                logger.info("[%s] CD not submitted (%s) — skip eval", instance_id, cd.termination_reason)
            out.cd = asdict_skip(cd, ["messages", "trajectory"])
            out.cd_messages_sha = _sha([m for m in cd.messages if m.get("role") != "exit"])
            out.cd_resolved = (cd.termination_reason == "submitted" and
                               out.cd.get("harness_status") == "RESOLVED")
        else:
            logger.info("[%s] CD branch skipped (--no-condiag)", instance_id)
            out.cd["termination_reason"] = "not_run_disabled"
            out.cd_run = False

        # ═══════ Fairness Check ═══════
        sf_ws = sf.workspace_sha_before_first_step if hasattr(sf, "workspace_sha_before_first_step") else ""
        cd_ws = cd.workspace_sha_before_first_step if (out.cd_run and hasattr(cd, "workspace_sha_before_first_step")) else ""
        r1_ws = r1_snapshot.workspace_state_sha if r1_snapshot else "no_snapshot"
        cd_restore_ok = cd.restore_result.ok if out.cd_run else True
        cd_ws_ok = bool(cd_ws) and cd_ws == r1_ws if out.cd_run else True
        fairness_ok = (
            r1_snapshot is not None
            and sf.restore_result.ok
            and cd_restore_ok
            and sf_ws == r1_ws
            and cd_ws_ok
        )
        out.fairness_ok = fairness_ok
        out.r1_workspace_sha = r1_ws
        out.sf_preflight_sha = sf_ws
        out.cd_preflight_sha = cd_ws
        logger.info("[%s] fairness: r1_ws=%s sf_ws=%s cd_ws=%s ok=%s",
                     instance_id, r1_ws, sf_ws, cd_ws, fairness_ok)

        # ═══════ Verdict ═══════
        if not fairness_ok:
            out.verdict = "invalid_fairness"
        elif not out.cd_run:
            out.verdict = "cd_disabled"
        elif out.sf_resolved and out.cd_resolved:
            out.verdict = "both_succeed"
        elif out.sf_resolved and not out.cd_resolved:
            out.verdict = "sf_wins"
        elif not out.sf_resolved and out.cd_resolved:
            out.verdict = "condiag_wins"
        else:
            out.verdict = "both_fail"

        logger.info("[%s] verdict=%s", instance_id, out.verdict)

    except Exception as e:
        logger.exception("[%s] Experiment failed", instance_id)
        out.error = str(e)
        out.verdict = "error"
    finally:
        _write_comparison(inst_dir, out)

    return out


# ─── Helpers ───────────────────────────────────────────────────────

def asdict_skip(obj, skip_keys):
    """Convert dataclass to dict, recursively handling nested dataclasses."""
    import dataclasses
    d = {}
    for k, v in vars(obj).items():
        if k in skip_keys or k.startswith("_"):
            continue
        if dataclasses.is_dataclass(v):
            d[k] = dataclasses.asdict(v)
        elif isinstance(v, (list, tuple)) and v and dataclasses.is_dataclass(v[0]):
            d[k] = [dataclasses.asdict(item) for item in v]
        else:
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


def _get_git_commit() -> str:
    """Return short git commit hash, or 'unknown' if not in a repo."""
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


