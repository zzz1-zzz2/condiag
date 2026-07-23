"""Tests for P1-3C: DiagnosisHypothesis + SearchContract."""
from __future__ import annotations

import json

import pytest

from condiag.diagnosis.hypothesis import (
    DiagnosisHypothesis,
    HypothesisStatus,
    EvidenceReference,
    from_subtyped_diagnosis,
    make_evidence_id,
    make_hypothesis_id,
)
from condiag.diagnosis.search_contract import (
    SearchAction,
    SearchActionType,
    SearchContract,
    build_search_contract,
    build_search_contracts,
    serialize_plan,
)
from condiag.diagnosis.taxonomy import ContextDeficiencyType


def _make_hyp(
    subtype: str = "FRAME_ATTRIBUTE_PROPAGATION",
    confidence: str = "high",
    retrieval_targets: list[str] | None = None,
    failure_sites: list[str] | None = None,
    status: HypothesisStatus = HypothesisStatus.PROPOSED,
    test_names: list[str] | None = None,
    deficiency_type: ContextDeficiencyType = ContextDeficiencyType.API_DEFINITION,
) -> DiagnosisHypothesis:
    uncertainty = {"high": 0.2, "medium": 0.5, "low": 0.9}.get(confidence, 0.7)
    return DiagnosisHypothesis(
        hypothesis_id=make_hypothesis_id(["C1"], deficiency_type, subtype),
        cluster_ids=["C1"],
        deficiency_type=deficiency_type,
        subtype=subtype,
        confidence=confidence,
        uncertainty=uncertainty,
        status=status,
        failure_sites=failure_sites if failure_sites is not None else ["src/foo.py:42"],
        test_names=test_names or ["test_foo"],
        retrieval_targets=retrieval_targets if retrieval_targets is not None else ["Foo", "Bar"],
        candidate_edit_targets=["src/foo.py"],
        supporting_evidence_ids=[
            make_evidence_id("target_symbol", "src/foo.py:42", "Foo"),
        ],
        statement="test statement",
        reason="test reason",
    )


class TestDiagnosisHypothesis:
    def test_make_evidence_id_stable(self):
        eid1 = make_evidence_id("test_failure", "test_foo", "assertion line")
        eid2 = make_evidence_id("test_failure", "test_foo", "assertion line")
        assert eid1 == eid2
        assert eid1.startswith("E")

    def test_make_hypothesis_id_stable(self):
        h1 = make_hypothesis_id(["C1", "C2"], ContextDeficiencyType.API_DEFINITION, "X")
        h2 = make_hypothesis_id(["C2", "C1"], ContextDeficiencyType.API_DEFINITION, "X")
        # Order-invariant
        assert h1 == h2
        assert h1.startswith("H")

    def test_to_dict_round_trip(self):
        h = _make_hyp()
        d = h.to_dict()
        assert d["subtype"] == "FRAME_ATTRIBUTE_PROPAGATION"
        assert d["confidence"] == "high"
        assert d["cluster_ids"] == ["C1"]
        assert isinstance(d["retrieval_targets"], list)

    def test_from_subtyped_diagnosis_bridge(self):
        """Verify that the legacy SubtypedDiagnosis still maps to the new schema."""

        class FakeSub:
            type = ContextDeficiencyType.API_DEFINITION
            subtype = "FRAME_ATTRIBUTE_PROPAGATION"
            confidence = "high"
            key_location = "src/foo.py:42"
            target_symbols = ["Foo", "Bar"]
            reason = "test reason"

        h = from_subtyped_diagnosis(FakeSub(), "C1", ["test_foo"])
        assert h.subtype == "FRAME_ATTRIBUTE_PROPAGATION"
        assert h.confidence == "high"
        assert h.failure_sites == ["src/foo.py:42"]
        assert h.retrieval_targets == ["Foo", "Bar"]
        assert len(h.supporting_evidence_ids) >= 2  # one per target + failure_site

    def test_uncertainty_from_confidence(self):
        high = _make_hyp(confidence="high")
        med = _make_hyp(confidence="medium")
        low = _make_hyp(confidence="low")
        assert high.uncertainty < med.uncertainty < low.uncertainty


class TestSearchContract:
    def test_frame_attribute_emits_find_definition(self):
        h = _make_hyp(
            subtype="FRAME_ATTRIBUTE_PROPAGATION",
            failure_sites=["src/foo.py:42"],
            retrieval_targets=["Foo"],
        )
        contract = build_search_contract(h, global_budget=4)
        assert not contract.abstained
        assert len(contract.actions) >= 1
        first = contract.actions[0]
        assert first.action_type == SearchActionType.FIND_DEFINITION
        assert first.target == "src/foo.py"
        assert first.budget >= 1

    def test_numerical_mismatch_emits_find_related_tests(self):
        h = _make_hyp(
            subtype="NUMERICAL_MISMATCH",
            retrieval_targets=["compute_value"],
        )
        contract = build_search_contract(h)
        types = [a.action_type for a in contract.actions]
        assert SearchActionType.FIND_RELATED_TESTS in types

    def test_route_comparison_emits_find_parallel(self):
        h = _make_hyp(
            subtype="ROUTE_COMPARISON_FAILURE",
            failure_sites=["src/path.py:42"],
        )
        contract = build_search_contract(h)
        types = [a.action_type for a in contract.actions]
        assert SearchActionType.FIND_PARALLEL_IMPLEMENTATION in types

    def test_abstain_on_rejected_hypothesis(self):
        h = _make_hyp(status=HypothesisStatus.REJECTED)
        contract = build_search_contract(h)
        assert contract.abstained
        assert contract.actions == []
        assert "REJECTED" in contract.abstention_reason

    def test_abstain_on_low_confidence_no_targets(self):
        h = _make_hyp(
            subtype="UNCLASSIFIED",
            confidence="low",
            retrieval_targets=[],
            failure_sites=[],
        )
        contract = build_search_contract(h)
        assert contract.abstained

    def test_global_budget_caps_actions(self):
        h = _make_hyp(
            subtype="FRAME_ATTRIBUTE_PROPAGATION",
            failure_sites=["a.py:1", "b.py:2", "c.py:3", "d.py:4", "e.py:5"],
        )
        contract = build_search_contract(h, global_budget=2)
        assert len(contract.actions) <= 2

    def test_action_ids_unique_within_contract(self):
        h = _make_hyp(
            subtype="NUMERICAL_MISMATCH",
            retrieval_targets=["a", "b", "c"],
        )
        contract = build_search_contract(h, global_budget=5)
        ids = [a.action_id for a in contract.actions]
        assert len(ids) == len(set(ids)), "duplicate action IDs"

    def test_deterministic_action_ids(self):
        h = _make_hyp(subtype="NUMERICAL_MISMATCH", retrieval_targets=["Foo"])
        c1 = build_search_contract(h)
        c2 = build_search_contract(h)
        assert c1.to_dict() == c2.to_dict()

    def test_serialize_plan(self):
        h = _make_hyp()
        c = build_search_contract(h)
        plan = serialize_plan([c], [h])
        assert plan["version"] == "1"
        assert "hypotheses" in plan
        assert "contracts" in plan
        assert "n_actions" in plan
        assert "n_abstained" in plan
        # Round-trip via JSON
        s = json.dumps(plan)
        assert "FRAME_ATTRIBUTE_PROPAGATION" in s


class TestSubtypeCoverage:
    """Every subtype in the registry must dispatch to SOME template
    (NOT default to NOOP) unless it is explicitly an environment-only subtype."""

    def test_every_template_dispatch_returns_something(self):
        from condiag.diagnosis.alignment import SUBTYPE_REGISTRY

        # Subtypes that are expected to return NOOP (no actionable retrieval)
        env_subtypes = {"MISSING_DATA_FILE", "MISSING_SYSTEM_DEP", "UNCLASSIFIED"}

        for type_name, subtypes in SUBTYPE_REGISTRY.items():
            for subtype in subtypes:
                if subtype in env_subtypes:
                    continue
                h = _make_hyp(
                    subtype=subtype,
                    failure_sites=["src/x.py:1"],
                    retrieval_targets=["X"],
                    deficiency_type=ContextDeficiencyType(type_name),
                )
                contract = build_search_contract(h)
                assert not contract.abstained, \
                    f"subtype {subtype!r} unexpectedly abstained; no template?"


class TestRealCanaryWithHypotheses:
    """End-to-end: real canary log → extract → cluster → diagnose → contract."""

    @pytest.fixture(autouse=True)
    def skip_if_no_log(self):
        from tests.test_p1_3 import REAL_CANARY_LOG
        if REAL_CANARY_LOG is None:
            pytest.skip("no real canary log available")

    def test_real_log_full_pipeline(self):
        from condiag.diagnosis.signals.pytest_extractor import extract_test_log
        from condiag.diagnosis.failure_event import (
            extract_failure_events,
            reasoner_v2_cluster,
        )
        from condiag.diagnosis.alignment import reasoner_v2_diagnose
        from condiag.diagnosis.hypothesis import from_subtyped_diagnosis
        from condiag.diagnosis.search_contract import build_search_contracts
        from condiag.diagnosis.signals.schema import RuntimeFailureFeatureBundle
        from tests.test_p1_3 import REAL_CANARY_LOG

        tl = extract_test_log(str(REAL_CANARY_LOG))
        bundle = RuntimeFailureFeatureBundle(test_log=tl)
        clusters = reasoner_v2_cluster(bundle)
        diagnoses = reasoner_v2_diagnose(clusters, bundle.patch, bundle.trajectory)

        hypotheses = [
            from_subtyped_diagnosis(d, c.cluster_id, c.test_names)
            for c, d in zip(clusters, diagnoses)
        ]
        contracts = build_search_contracts(hypotheses)
        # Real canary must produce at least one actionable contract
        any_action = any(
            a.action_type != SearchActionType.NOOP
            for c in contracts for a in c.actions
        )
        assert any_action, "real canary pipeline produced no actionable actions"
