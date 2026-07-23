"""Tests for P0-5/6 closure: serialization, capture, fairness pre-step gate."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from condiag.experiment import ComparisonOutput, asdict_skip
from condiag.workspace import (
    CaptureResult,
    WorkspaceSnapshot,
    UntrackedFile,
    check_workspace_fairness,
)


class TestComparisonSerialization:
    def test_branch_result_with_restore_result_serializable(self):
        """branch_result containing RestoreResult must survive json.dumps()."""
        out = ComparisonOutput(instance_id="test")

        # Simulate what experiment.py does after assigning asdict_skip(sf, ...)
        sf_dict = {
            "termination_reason": "submitted",
            "restore_result": {"ok": True, "workspace_sha": "abc123", "reason": ""},
            "workspace_sha_before_first_step": "abc123",
            "n_calls_total": 37,
        }
        out.sf = sf_dict

        cd_dict = {
            "termination_reason": "submitted",
            "restore_result": {"ok": True, "workspace_sha": "abc123", "reason": ""},
            "workspace_sha_before_first_step": "abc123",
            "n_calls_total": 42,
        }
        out.cd = cd_dict
        out.fairness_ok = True

        # Must not raise TypeError
        dumped = json.dumps(out.to_dict(), indent=2)
        assert '"fairness_ok": true' in dumped
        assert '"restore_result"' in dumped

    def test_asdict_skip_handles_nested_dataclass(self):
        """asdict_skip must recursively convert dataclass fields to dicts."""

        @dataclass
        class Inner:
            ok: bool = True
            sha: str = "abc"

        @dataclass
        class Outer:
            name: str = "test"
            inner: Inner | None = None

        result = asdict_skip(Outer(inner=Inner()), skip_keys=[])
        assert isinstance(result["inner"], dict)
        assert result["inner"]["ok"] is True
        # Verify JSON serializable
        json.dumps(result)


class TestCaptureResult:
    def test_capture_ok(self):
        cr = CaptureResult(ok=True, snapshot=WorkspaceSnapshot(), reason="")
        assert cr.ok
        assert cr.snapshot is not None

    def test_capture_failed(self):
        cr = CaptureResult(ok=False, reason="git rev-parse failed")
        assert not cr.ok
        assert cr.snapshot is None


class TestFairnessGate:
    def test_equal_workspaces_pass(self):
        ws = WorkspaceSnapshot(tracked_diff="same", base_commit_sha="abc")
        assert check_workspace_fairness(ws, ws, ws)["all_ok"]

    def test_mismatched_tracked_fails(self):
        r1 = WorkspaceSnapshot(tracked_diff="diff a", base_commit_sha="abc")
        sf = WorkspaceSnapshot(tracked_diff="diff b", base_commit_sha="abc")
        fairness = check_workspace_fairness(r1, sf, sf)
        assert not fairness["all_ok"]
        assert not fairness["r1_vs_sf_tracked_ok"]

    def test_mismatched_untracked_fails(self):
        r1 = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile("x.py", 10, "aaa")],
            base_commit_sha="abc",
        )
        sf = WorkspaceSnapshot(
            tracked_diff="same",
            untracked_manifest=[UntrackedFile("x.py", 20, "bbb")],
            base_commit_sha="abc",
        )
        fairness = check_workspace_fairness(r1, sf, sf)
        assert not fairness["all_ok"]
        assert not fairness["r1_vs_sf_state_ok"]


# ── P1-3C Shadow Integration Tests ──────────────────────────────────


class TestP13CShadowIntegration:
    """Verify shadow artifacts are produced and well-formed."""

    def _build_bundle(self, tmp_path):
        """Construct a minimal bundle from real canary log + a fake patch."""
        from tests.test_p1_3 import REAL_CANARY_LOG
        if REAL_CANARY_LOG is None:
            return None
        from condiag.diagnosis.signals.pytest_extractor import extract_test_log
        from condiag.diagnosis.signals.schema import (
            PatchSignals,
            RuntimeFailureFeatureBundle,
            RuntimeInstanceSignals,
            TrajectorySignals,
        )

        tl = extract_test_log(str(REAL_CANARY_LOG))
        bundle = RuntimeFailureFeatureBundle(
            test_log=tl,
            instance=RuntimeInstanceSignals(
                instance_id="astropy__astropy-13398",
                repo="astropy/astropy",
            ),
            patch=PatchSignals(
                edited_files=["astropy/coordinates/builtin_frames/itrs_observed_transforms.py"],
                added_lines=121, deleted_lines=0, changed_lines=121,
            ),
            trajectory=TrajectorySignals(
                total_tool_calls=66, viewed_files=["src/foo.py"],
            ),
        )
        return bundle

    def test_shadow_artifacts_written_when_cd_enabled(self, tmp_path):
        """Real bundle → shadow JSONs exist on disk."""
        from condiag.diagnosis.failure_event import reasoner_v2_cluster
        from condiag.diagnosis.alignment import reasoner_v2_diagnose
        from condiag.diagnosis.hypothesis import from_subtyped_diagnosis
        from condiag.diagnosis.search_contract import (
            PlanBudget,
            build_evidence_ledger,
            build_search_plan,
            write_shadow_artifacts,
        )

        bundle = self._build_bundle(tmp_path)
        if bundle is None:
            pytest.skip("no real canary log available")

        clusters = reasoner_v2_cluster(bundle)
        diagnoses = reasoner_v2_diagnose(
            clusters, bundle.patch, bundle.trajectory,
        )
        hyps = [
            from_subtyped_diagnosis(d, c.cluster_id, c.test_names)
            for c, d in zip(clusters, diagnoses)
        ]
        ledger = build_evidence_ledger(clusters, diagnoses, bundle=bundle)
        contracts = build_search_plan(
            hyps,
            budget=PlanBudget(max_total_actions=3, max_total_budget=8),
            ledger=ledger,
        )
        shadow_dir = tmp_path / "cd" / "p1_3c_shadow"
        paths = write_shadow_artifacts(
            shadow_dir,
            contracts=contracts,
            hypotheses=hyps,
            ledger=ledger,
            validation_report={
                "run_id": "test_run",
                "instance_id": bundle.instance.instance_id,
                "schema_version": "1",
            },
        )
        # All four artifact files must exist.
        for name in ("evidence_items", "diagnosis_hypotheses",
                     "search_contracts", "contract_validation"):
            assert Path(paths[name]).exists(), f"missing {name}"
        # JSON parses
        import json
        ev = json.loads(Path(paths["evidence_items"]).read_text())
        assert ev["n_items"] >= 1

    def test_evidence_ids_are_dereferenceable(self, tmp_path):
        """Every supporting_evidence_id in hypotheses must resolve to a real
        EvidenceItem in the ledger."""
        from condiag.diagnosis.failure_event import reasoner_v2_cluster
        from condiag.diagnosis.alignment import reasoner_v2_diagnose
        from condiag.diagnosis.hypothesis import from_subtyped_diagnosis
        from condiag.diagnosis.search_contract import (
            PlanBudget,
            build_evidence_ledger,
            build_search_plan,
        )

        bundle = self._build_bundle(tmp_path)
        if bundle is None:
            pytest.skip("no real canary log available")

        clusters = reasoner_v2_cluster(bundle)
        diagnoses = reasoner_v2_diagnose(
            clusters, bundle.patch, bundle.trajectory,
        )
        hyps = [
            from_subtyped_diagnosis(d, c.cluster_id, c.test_names)
            for c, d in zip(clusters, diagnoses)
        ]
        ledger = build_evidence_ledger(clusters, diagnoses, bundle=bundle)

        # Every evidence_id in any hypothesis must be present in the ledger.
        all_ids = {eid for h in hyps for eid in h.supporting_evidence_ids}
        for eid in all_ids:
            assert ledger.has(eid), f"evidence_id {eid!r} not in ledger"

    def test_evidence_kinds_are_real_not_guessed(self, tmp_path):
        """No evidence kind should be inferred from hash-id strings."""
        from condiag.diagnosis.search_contract import build_evidence_ledger

        bundle = self._build_bundle(tmp_path)
        if bundle is None:
            pytest.skip("no real canary log available")

        from condiag.diagnosis.failure_event import reasoner_v2_cluster
        from condiag.diagnosis.alignment import reasoner_v2_diagnose

        clusters = reasoner_v2_cluster(bundle)
        diagnoses = reasoner_v2_diagnose(
            clusters, bundle.patch, bundle.trajectory,
        )
        ledger = build_evidence_ledger(clusters, diagnoses, bundle=bundle)

        # Every item must carry a non-empty kind from the allowed set.
        allowed_kinds = {
            "failure_site", "target_symbol", "test_failure",
            "assertion", "patch_edit",
        }
        for item in ledger.items():
            assert item.kind in allowed_kinds, (
                f"unexpected kind {item.kind!r} (forbidden hash-guessed kinds)"
            )

    def test_action_targets_no_primitive_literals(self, tmp_path):
        """Critically: every SearchAction.target.value must not be a Python
        primitive (Time, float, etc.)."""
        from condiag.diagnosis.failure_event import reasoner_v2_cluster
        from condiag.diagnosis.alignment import reasoner_v2_diagnose
        from condiag.diagnosis.hypothesis import from_subtyped_diagnosis
        from condiag.diagnosis.search_contract import (
            PlanBudget,
            build_evidence_ledger,
            build_search_plan,
            is_primitive_literal,
        )

        bundle = self._build_bundle(tmp_path)
        if bundle is None:
            pytest.skip("no real canary log available")

        clusters = reasoner_v2_cluster(bundle)
        diagnoses = reasoner_v2_diagnose(
            clusters, bundle.patch, bundle.trajectory,
        )
        hyps = [
            from_subtyped_diagnosis(d, c.cluster_id, c.test_names)
            for c, d in zip(clusters, diagnoses)
        ]
        ledger = build_evidence_ledger(clusters, diagnoses, bundle=bundle)
        contracts = build_search_plan(
            hyps,
            budget=PlanBudget(max_total_actions=3, max_total_budget=8),
            ledger=ledger,
        )
        for contract in contracts:
            for action in contract.actions:
                assert not is_primitive_literal(action.target.value), (
                    f"primitive literal leaked: target={action.target.value!r}"
                )
