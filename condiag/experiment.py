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
    sf_preflight_sha: str = ""
    cd_preflight_sha: str = ""
    fairness_tracked: dict = field(default_factory=dict)
    untracked_audit: dict = field(default_factory=dict)
    # Diagnosis
    diagnosis_abstained: bool | None = None
    diagnosis_type: str = ""
    diagnosis_confidence: str = ""
    intervention_applied: bool = False
    # R1
    r1_messages_sha: str = ""
    r1_preflight_workspace_sha: str = ""
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

        # ═══════ P0-4a: Patch Integrity Gate (R1) ═══════
        from condiag.integrity import check_patch_integrity
        r1_integrity = check_patch_integrity(
            termination_reason=r1.termination_reason,
            agent_submission=r1.agent_submission,
            workspace_patch=r1.patch_text,
            evaluation_patch=r1.evaluation_patch,
        )
        _write_json(inst_dir / "round1" / "integrity_report.json", r1_integrity.to_dict())
        logger.info("[%s] R1 integrity: ok=%s status=%s",
                     instance_id, r1_integrity.ok, r1_integrity.status)
        out.round1["integrity_ok"] = r1_integrity.ok
        out.round1["integrity_status"] = r1_integrity.status
        if not r1_integrity.ok:
            logger.info("[%s] R1 patch failed integrity — episode invalid", instance_id)
            out.verdict = f"invalid_r1_patch:{r1_integrity.status}"
            return out

        # ═══════ Official eval R1 ═══════
        logger.info("[%s] Eval R1 patch", instance_id)
        r1_eval = harness.evaluate(instance_spec, model_patch=r1.evaluation_patch,
                                   run_id=f"r1_{_sha(r1.evaluation_patch)}_{int(time.time())}")
        _write_json(inst_dir / "round1" / "harness_eval.json",
                    {"status": r1_eval.status, "duration": r1_eval.duration_seconds,
                     "report": r1_eval.report, "test_log_path": r1_eval.test_log_path})

        # Save P0-3 artifacts
        if r1.agent_submission:
            _write_json(inst_dir / "round1" / "agent_submission.json",
                        r1.agent_submission.to_dict())
        _write_patch(inst_dir / "round1" / "agent_submitted.patch",
                     getattr(r1.agent_submission, "selected_patch", "") or r1.patch_text)
        _write_patch(inst_dir / "round1" / "workspace.patch", r1.patch_text)
        _write_patch(inst_dir / "round1" / "evaluation.patch", r1.evaluation_patch)
        out.round1["harness_status"] = r1_eval.status
        out.round1_resolved = (r1_eval.status == "RESOLVED")
        if out.round1_resolved:
            logger.info("[%s] R1 resolved — episode valid, skipping SF/CD", instance_id)
            out.verdict = "r1_resolved"
            return out

        # ═══════ P0-4b: Harness Eligibility Gate ═══════
        from condiag.integrity import check_episode_eligibility
        # Note: fw is needed for eligibility; extract it first
        fw_temp = harness.extract_witness(r1_eval).to_dict()
        eligibility = check_episode_eligibility(r1_eval, fw_temp)
        _write_json(inst_dir / "round1" / "eligibility_report.json", eligibility.to_dict())
        logger.info("[%s] R1 eligibility: ok=%s status=%s",
                     instance_id, eligibility.ok, eligibility.status)
        if not eligibility.ok:
            logger.info("[%s] Episode ineligible: %s", instance_id, eligibility.status)
            out.verdict = f"ineligible:{eligibility.status}"
            out.round1["eligibility_ok"] = eligibility.ok
            out.round1["eligibility_status"] = eligibility.status
            return out
        out.round1["eligibility_ok"] = eligibility.ok
        out.round1["eligibility_status"] = eligibility.status

        # ═══════ FW + Checkpoint ═══════
        fw = fw_temp
        fw_preview = fw.get("failed_tests", [])[:3] or [fw.get("error_message", "")[:50]]
        logger.info("[%s] FW: %s", instance_id, fw_preview)

        out.r1_messages_sha = _sha(r1.messages)
        out.r1_patch_sha = _sha(r1.patch_text)
        out.failure_witness_sha = _sha(fw)
        _write_json(inst_dir / "round1" / "failure_witness.json", fw)
        _save_checkpoint(inst_dir, instance_id, episode_run_id, r1, base_commit)

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

        # ═══════ P1: Build FailureFeatureBundle (shared R1 artifact) ═══════
        from condiag.diagnosis.bundle_builder import build_failure_feature_bundle
        from condiag.diagnosis.diagnoser_core import DiagnoserCore
        from condiag.diagnosis.signals import extract_test_log
        from condiag.patch_artifacts import sha256_full

        test_log = None
        if getattr(r1_eval, "test_log_path", ""):
            try:
                test_log = extract_test_log(r1_eval.test_log_path)
            except Exception as e:
                logger.warning("[%s] test_log extraction failed: %s", instance_id, e)

        bundle = build_failure_feature_bundle(
            failure_witness=fw,
            evaluation_patch=r1.evaluation_patch,
            workspace_patch=r1.patch_text,
            trajectory=getattr(r1, "trajectory", None),
            instance_spec=instance_spec,
            test_log=test_log,
        )
        _write_json(inst_dir / "round1" / "failure_feature_bundle.json", bundle.model_dump())

        # ═══════ Diagnosis (CD only) ═══════
        diagnosis = None
        diag_text = None
        if run_cd:
            from condiag.diagnosis.taxonomy import ContextDeficiencyType
            diagnosis = DiagnoserCore().diagnose(bundle)
            _write_json(inst_dir / "cd" / "diagnosis.json", diagnosis.model_dump())
            logger.info("[%s] Diagnosis: primary=%s confidence=%s",
                         instance_id, diagnosis.primary.type.value, diagnosis.primary.confidence.value)
            # Behavioral abstention: NO_RELIABLE_DEFICIENCY -> skip diagnosis injection
            abstained = (diagnosis.primary.type == ContextDeficiencyType.NO_RELIABLE_DEFICIENCY)
            diag_text = None if abstained else _render_diagnosis_prompt(diagnosis)
            if abstained:
                logger.info("[%s] Diagnosis abstained (NO_RELIABLE_DEFICIENCY)", instance_id)
            # Save diagnosis stats to ComparisonOutput (not out.cd - that gets overwritten)
            out.diagnosis_abstained = abstained
            out.diagnosis_type = diagnosis.primary.type.value
            out.diagnosis_confidence = diagnosis.primary.confidence.value
            out.intervention_applied = not abstained

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
            fairness_debug_dir=str(inst_dir / "fairness_debug" / "sf"),
        )
        _write_patch(inst_dir / "sf" / "patch.diff", sf.patch_text)
        _write_patch(inst_dir / "sf" / "final_evaluation.patch", sf.final_evaluation_patch)
        _write_trajectory(inst_dir / "sf" / "trajectory.json", sf.trajectory)

        # P0-4a: SF integrity gate
        sf_integrity = check_patch_integrity(
            termination_reason=sf.termination_reason,
            agent_submission=getattr(sf, "agent_submission", None),
            workspace_patch=sf.patch_text,
            evaluation_patch=sf.final_evaluation_patch,
        )
        _write_json(inst_dir / "sf" / "integrity_report.json", sf_integrity.to_dict())
        # Save P0-3 SF artifacts
        if getattr(sf, "agent_submission", None):
            _write_json(inst_dir / "sf" / "agent_submission.json",
                        sf.agent_submission.to_dict())
        _write_patch(inst_dir / "sf" / "agent_submitted.patch",
                     getattr(sf.agent_submission, "selected_patch", "") or sf.patch_text)
        _write_patch(inst_dir / "sf" / "workspace.patch", sf.patch_text)

        if sf_integrity.ok and sf.termination_reason == "submitted":
            _eval_and_save(harness, instance_spec, sf.final_evaluation_patch, inst_dir / "sf" / "harness_eval.json",
                           "sf", sf)
        else:
            logger.info("[%s] SF not submitted or invalid (%s) — skip eval",
                        instance_id, sf_integrity.status)
        out.sf = asdict_skip(sf, ["messages", "trajectory"])
        out.sf["integrity_ok"] = sf_integrity.ok
        out.sf["integrity_status"] = sf_integrity.status
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
                failure_witness=fw, diagnosis=diag_text, mode="condiag",
                protocol_config=revision_config,
                workspace_snapshot=r1_snapshot,
                fairness_debug_dir=str(inst_dir / "fairness_debug" / "cd"),
            )
            _write_patch(inst_dir / "cd" / "patch.diff", cd.patch_text)
            _write_patch(inst_dir / "cd" / "final_evaluation.patch", cd.final_evaluation_patch)
            _write_trajectory(inst_dir / "cd" / "trajectory.json", cd.trajectory)

            # P0-4a: CD integrity gate
            cd_integrity = check_patch_integrity(
                termination_reason=cd.termination_reason,
                agent_submission=getattr(cd, "agent_submission", None),
                workspace_patch=cd.patch_text,
                evaluation_patch=cd.final_evaluation_patch,
            )
            _write_json(inst_dir / "cd" / "integrity_report.json", cd_integrity.to_dict())
            if getattr(cd, "agent_submission", None):
                _write_json(inst_dir / "cd" / "agent_submission.json",
                            cd.agent_submission.to_dict())
            _write_patch(inst_dir / "cd" / "agent_submitted.patch",
                         getattr(cd.agent_submission, "selected_patch", "") or cd.patch_text)
            _write_patch(inst_dir / "cd" / "workspace.patch", cd.patch_text)

            if cd_integrity.ok and cd.termination_reason == "submitted":
                _eval_and_save(harness, instance_spec, cd.final_evaluation_patch, inst_dir / "cd" / "harness_eval.json",
                               "cd", cd)
            else:
                logger.info("[%s] CD not submitted or invalid (%s) — skip eval",
                            instance_id, cd_integrity.status)
            out.cd = asdict_skip(cd, ["messages", "trajectory"])
            out.cd["integrity_ok"] = cd_integrity.ok
            out.cd["integrity_status"] = cd_integrity.status
            out.cd_messages_sha = _sha([m for m in cd.messages if m.get("role") != "exit"])
            out.cd_resolved = (cd.termination_reason == "submitted" and
                               out.cd.get("harness_status") == "RESOLVED")
        else:
            logger.info("[%s] CD branch skipped (--no-condiag)", instance_id)
            out.cd["termination_reason"] = "not_run_disabled"
            out.cd_run = False

        # ═══════ Fairness Check ═══════
        # BLOCKING tracked-code gate: SHAs must match across R1, SF, CD.
        # Restore success itself is tracked via each branch's RestoreResult.
        # Untracked manifest gaps are recorded but do not block.
        sf_ws = sf.workspace_sha_before_first_step or ""
        cd_ws = (cd.workspace_sha_before_first_step
                 if (out.cd_run and hasattr(cd, "workspace_sha_before_first_step"))
                 else "")

        r1_sha = r1_snapshot.tracked_diff_sha if r1_snapshot else ""
        sf_ok = bool(r1_sha) and bool(sf_ws) and (sf_ws == r1_sha)
        cd_ok = (bool(r1_sha) and bool(cd_ws) and (cd_ws == r1_sha)) if out.cd_run else True
        tracked_gate = {
            "r1_vs_sf_tracked_ok": sf_ok,
            "r1_vs_cd_tracked_ok": cd_ok if out.cd_run else None,
            "all_ok": sf_ok and cd_ok,
        }

        cd_restore_ok = cd.restore_result.ok if out.cd_run else True
        fairness_ok = bool(tracked_gate["all_ok"]) and sf.restore_result.ok and cd_restore_ok

        out.fairness_ok = fairness_ok
        out.r1_preflight_workspace_sha = r1_sha or "no_snapshot"
        out.sf_preflight_sha = sf_ws
        out.cd_preflight_sha = cd_ws
        out.fairness_tracked = {
            "r1_vs_sf_ok": tracked_gate["r1_vs_sf_tracked_ok"],
            "r1_vs_cd_ok": tracked_gate["r1_vs_cd_tracked_ok"],
        }
        # Untracked audit info per branch (audit-only, not blocking).
        out.untracked_audit = {
            "sf": (sf.restore_result.to_dict()
                   if hasattr(sf.restore_result, "to_dict") else {}),
        }
        if out.cd_run:
            out.untracked_audit["cd"] = (
                cd.restore_result.to_dict()
                if hasattr(cd.restore_result, "to_dict") else {}
            )

        logger.info(
            "[%s] fairness: r1_ws=%s sf_ws=%s cd_ws=%s tracked_ok=%s gate_ok=%s",
            instance_id,
            out.r1_preflight_workspace_sha, out.sf_preflight_sha, out.cd_preflight_sha,
            tracked_gate["all_ok"], fairness_ok,
        )

        # ═══════ Branch Eval Validity ═══════
        VALID_BRANCH_STATUSES = {"RESOLVED", "UNRESOLVED"}
        sf_hs = out.sf.get("harness_status", "")
        sf_eval_valid = sf_integrity.ok and (sf_hs in VALID_BRANCH_STATUSES)
        out.sf["eval_valid"] = sf_eval_valid
        if out.cd_run:
            cd_hs = out.cd.get("harness_status", "")
            cd_eval_valid = cd_integrity.ok and (cd_hs in VALID_BRANCH_STATUSES)
            out.cd["eval_valid"] = cd_eval_valid
        else:
            cd_eval_valid = True

        # ═══════ Verdict ═══════
        if not fairness_ok:
            out.verdict = "invalid_fairness"
        elif not out.cd_run:
            out.verdict = "cd_disabled"
        elif not sf_eval_valid and not cd_eval_valid:
            out.verdict = "invalid_both_branches"
        elif not sf_eval_valid:
            out.verdict = "invalid_branch_sf"
        elif not cd_eval_valid:
            out.verdict = "invalid_branch_cd"
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


def _save_checkpoint(inst_dir: Path, instance_id: str, episode_run_id: str,
                    r1: Round1Result, base_commit: str):
    """Save episode checkpoint manifest from R1 artifacts.

    This is NOT a full agent state checkpoint — it's an audit manifest
    that records the R1 outcome and provides links to all R1 artifacts.
    Full messages, patches, and snapshots are saved as adjacent files.
    """
    import hashlib
    fm = hashlib.sha256(json.dumps(r1.messages, sort_keys=True, default=str).encode()).hexdigest()[:16]
    checkpoint = {
        "episode_run_id": episode_run_id,
        "instance_id": instance_id,
        "base_commit": base_commit,
        "n_calls": r1.n_calls,
        "cost": r1.cost,
        "duration_seconds": r1.duration_seconds,
        "termination_reason": r1.termination_reason,
        "evaluation_patch_sha": hashlib.sha256(r1.evaluation_patch.encode()).hexdigest()[:16] if r1.evaluation_patch else "",
        "workspace_state_sha": r1.workspace_snapshot.workspace_state_sha if r1.workspace_snapshot else "",
        "messages_sha": fm,
        "snapshot_path": "../workspace_snapshot.json",
        "failure_witness_path": "../failure_witness.json",
        "evaluation_patch_path": "../evaluation.patch",
    }
    cp_dir = inst_dir / "round1" / "checkpoint"
    cp_dir.mkdir(parents=True, exist_ok=True)
    _write_json(cp_dir / "checkpoint.json", checkpoint)


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


def _render_diagnosis_prompt(diagnosis) -> str:
    """Render a DiagnosisResult into a natural-language prompt for CD branch."""
    from condiag.diagnosis.taxonomy import ContextDeficiencyType
    parts = [
        "## Diagnosis - Targeted Repair Guidance",
        "",
        "Your patch did not pass validation. Below is a structured analysis",
        "of what the failure signals suggest about potentially missing context.",
        "",
        f"### Primary Deficiency: {diagnosis.primary.type.value}",
        f"Confidence: {diagnosis.primary.confidence.value}",
    ]
    if diagnosis.primary.evidence:
        parts.append("Evidence:")
        for e in diagnosis.primary.evidence:
            parts.append(f"  - {e}")
    if diagnosis.primary.key_location:
        parts.append(f"Key Location: {diagnosis.primary.key_location}")
    if diagnosis.secondary:
        parts.append(f"\nSecondary considerations ({len(diagnosis.secondary)}):")
        for s in diagnosis.secondary:
            parts.append(f"  - {s.type.value} ({s.confidence.value})")
    if diagnosis.rejected_assumptions:
        parts.append("\nRejected assumptions:")
        for r in diagnosis.rejected_assumptions:
            parts.append(f"  - {r}")
    parts.append("")
    parts.append("Please investigate and revise your patch.")
    return "\n".join(parts)


def _get_git_commit() -> str:
    """Return short git commit hash, or 'unknown' if not in a repo."""
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else "unknown"
    except Exception:
        return "unknown"


