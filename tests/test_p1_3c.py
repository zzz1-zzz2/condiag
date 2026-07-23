"""Tests for P1-3C: DiagnosisHypothesis + grounded SearchContract."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from condiag.diagnosis.hypothesis import (
    DiagnosisHypothesis,
    HypothesisStatus,
    from_subtyped_diagnosis,
    make_evidence_id,
    make_hypothesis_id,
)
from condiag.diagnosis.search_contract import (
    ContractStatus,
    ContractValidator,
    EvidenceItem,
    EvidenceLedger,
    EvidenceSource,
    PlanBudget,
    SearchAction,
    SearchActionType,
    SearchContract,
    SearchTarget,
    SearchTargetKind,
    build_search_contract,
    build_search_plan,
    build_search_contracts,
    is_primitive_literal,
    serialize_plan,
    write_shadow_artifacts,
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
        assert h1 == h2
        assert h1.startswith("H")

    def test_to_dict_round_trip(self):
        h = _make_hyp()
        d = h.to_dict()
        assert d["subtype"] == "FRAME_ATTRIBUTE_PROPAGATION"
        assert d["confidence"] == "high"
        assert d["cluster_ids"] == ["C1"]

    def test_from_subtyped_diagnosis_bridge(self):
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
        assert len(h.supporting_evidence_ids) >= 2


class TestPrimitiveFilter:
    def test_primitive_literals_detected(self):
        for prim in ["str", "int", "float", "list", "dict", "None", "True", "bool"]:
            assert is_primitive_literal(prim), f"{prim} should be primitive"
        for ok in ["Foo", "MyClass", "process_data", "user.email"]:
            assert not is_primitive_literal(ok), f"{ok} should NOT be primitive"


class TestEvidenceLedger:
    def test_add_and_retrieve(self):
        ledger = EvidenceLedger()
        item = EvidenceItem(
            evidence_id="E123",
            cluster_id="C1",
            source=EvidenceSource.STACK_FRAME,
            kind="frame",
            location="src/foo.py:42",
            content="test content",
        )
        ledger.add(item)
        assert ledger.has("E123")
        assert ledger.get("E123") is item

    def test_add_is_idempotent(self):
        ledger = EvidenceLedger()
        item = EvidenceItem(evidence_id="E123", cluster_id="C1")
        ledger.add(item)
        ledger.add(item)
        assert len(ledger.items()) == 1


class TestSearchTarget:
    def test_file_target_resolved(self):
        t = SearchTarget(value="src/foo.py", kind=SearchTargetKind.FILE, resolved=True)
        assert t.resolved
        assert t.kind == SearchTargetKind.FILE

    def test_primitive_target_filtered(self):
        """Targets like 'float', 'Time' filtered out at template level."""
        from condiag.diagnosis.search_contract import _symbol_to_target
        # 'float' is a primitive — should be filtered
        assert _symbol_to_target("float") is None
        # 'Time' is in our primitives list (common type-name confusion)
        assert _symbol_to_target("Time") is None
        # Regular identifier passes through
        t = _symbol_to_target("process_data")
        assert t is not None
        assert not t.resolved

    def test_identifier_shape_required(self):
        from condiag.diagnosis.search_contract import _symbol_to_target
        # Has spaces or weird chars
        assert _symbol_to_target("hello world") is None
        assert _symbol_to_target("") is None
        # Valid identifier
        t = _symbol_to_target("MyClass.process_data")
        assert t is not None


class TestValidator:
    def test_empty_target_rejected(self):
        contract = SearchContract(
            hypothesis_id="H1",
            actions=[
                SearchAction(
                    action_id="A1",
                    action_type=SearchActionType.FIND_DEFINITION,
                    target=SearchTarget(value="", kind=SearchTargetKind.FILE),
                )
            ],
        )
        validator = ContractValidator()
        issues = validator.validate(contract)
        assert any("empty target" in i for i in issues)

    def test_primitive_target_rejected(self):
        contract = SearchContract(
            hypothesis_id="H1",
            actions=[
                SearchAction(
                    action_id="A1",
                    action_type=SearchActionType.FIND_DEFINITION,
                    target=SearchTarget(value="float", kind=SearchTargetKind.SYMBOL),
                )
            ],
        )
        validator = ContractValidator()
        issues = validator.validate(contract)
        assert any("primitive literal" in i for i in issues)

    def test_incompatible_action_kind_rejected(self):
        """FIND_CALLERS must receive SYMBOL — TEST is incompatible."""
        contract = SearchContract(
            hypothesis_id="H1",
            actions=[
                SearchAction(
                    action_id="A1",
                    action_type=SearchActionType.FIND_CALLERS,
                    target=SearchTarget(value="test_foo", kind=SearchTargetKind.TEST),
                )
            ],
        )
        validator = ContractValidator()
        issues = validator.validate(contract)
        assert any("requires target_kind" in i for i in issues)

    def test_compatible_action_kind_passes(self):
        contract = SearchContract(
            hypothesis_id="H1",
            actions=[
                SearchAction(
                    action_id="A1",
                    action_type=SearchActionType.FIND_DEFINITION,
                    target=SearchTarget(value="src/foo.py", kind=SearchTargetKind.FILE),
                )
            ],
        )
        validator = ContractValidator()
        issues = validator.validate(contract)
        assert issues == []

    def test_evidence_target_must_be_in_ledger(self):
        ledger = EvidenceLedger()
        contract = SearchContract(
            hypothesis_id="H1",
            actions=[
                SearchAction(
                    action_id="A1",
                    action_type=SearchActionType.REHYDRATE_VIEWED_EVIDENCE,
                    target=SearchTarget(value="E_missing", kind=SearchTargetKind.EVIDENCE),
                )
            ],
        )
        validator = ContractValidator()
        issues = validator.validate(contract, ledger=ledger)
        assert any("not in ledger" in i for i in issues)


class TestBuildContract:
    def test_frame_attribute_emits_find_definition(self):
        h = _make_hyp(
            subtype="FRAME_ATTRIBUTE_PROPAGATION",
            failure_sites=["src/foo.py:42"],
            retrieval_targets=[],
        )
        contract = build_search_contract(h, max_actions=3)
        assert contract.status == ContractStatus.ACTIONABLE
        assert len(contract.actions) >= 1
        first = contract.actions[0]
        assert first.action_type == SearchActionType.FIND_DEFINITION
        assert first.target.kind == SearchTargetKind.FAILURE_SITE
        assert first.target.value == "src/foo.py:42"

    def test_primitive_targets_filtered(self):
        """Critical regression: ARGUMENT_TYPE_MISMATCH with [Time, float] must
        NOT produce FIND_CALLERS(Time/float) — they are primitives."""
        h = _make_hyp(
            subtype="ARGUMENT_TYPE_MISMATCH",
            retrieval_targets=["Time", "float"],
            failure_sites=[],
        )
        contract = build_search_contract(h, max_actions=3)
        # Either abstained (no grounded targets) or invalid (filtered primitives)
        # MUST NOT have a SYMBOL target with value "Time" or "float"
        for action in contract.actions:
            assert action.target.value not in ("Time", "float"), \
                f"primitive target leaked: {action.target.value}"

    def test_numerical_mismatch_emits_related_tests_or_falls_back(self):
        h = _make_hyp(
            subtype="NUMERICAL_MISMATCH",
            retrieval_targets=[],
            failure_sites=["src/compute.py:50"],
        )
        contract = build_search_contract(h, max_actions=3)
        if contract.status == ContractStatus.ACTIONABLE:
            assert contract.actions[0].action_type == SearchActionType.FIND_RELATED_TESTS

    def test_route_comparison_emits_find_parallel(self):
        h = _make_hyp(
            subtype="ROUTE_COMPARISON_FAILURE",
            failure_sites=["src/path.py:42"],
        )
        contract = build_search_contract(h, max_actions=3)
        assert contract.status == ContractStatus.ACTIONABLE
        assert contract.actions[0].action_type == SearchActionType.FIND_PARALLEL_IMPLEMENTATION

    def test_abstain_on_rejected_hypothesis(self):
        h = _make_hyp(status=HypothesisStatus.REJECTED)
        contract = build_search_contract(h)
        assert contract.status == ContractStatus.ABSTAINED
        assert contract.actions == []
        assert "REJECTED" in contract.abstention_reason

    def test_abstain_when_no_grounded_targets(self):
        h = _make_hyp(
            subtype="UNCLASSIFIED",
            confidence="low",
            retrieval_targets=[],
            failure_sites=[],
        )
        contract = build_search_contract(h)
        assert contract.status == ContractStatus.ABSTAINED

    def test_max_actions_caps(self):
        h = _make_hyp(
            subtype="NUMERICAL_MISMATCH",
            failure_sites=["a.py:1", "b.py:2", "c.py:3", "d.py:4", "e.py:5"],
        )
        contract = build_search_contract(h, max_actions=2)
        assert len(contract.actions) <= 2

    def test_action_ids_unique_within_contract(self):
        h = _make_hyp(
            subtype="NUMERICAL_MISMATCH",
            retrieval_targets=["a", "b", "c"],
        )
        contract = build_search_contract(h, max_actions=5)
        ids = [a.action_id for a in contract.actions]
        assert len(ids) == len(set(ids))

    def test_deterministic_action_ids(self):
        h = _make_hyp(subtype="NUMERICAL_MISMATCH", retrieval_targets=["Foo"])
        c1 = build_search_contract(h)
        c2 = build_search_contract(h)
        assert c1.to_dict() == c2.to_dict()

    def test_noop_abstain_does_not_emit_action(self):
        """A contract with no actions is ABSTAINED, not a NOOP action."""
        h = _make_hyp(
            subtype="UNCLASSIFIED",
            confidence="low",
            retrieval_targets=[],
            failure_sites=[],
        )
        contract = build_search_contract(h)
        assert contract.actions == []
        assert contract.status == ContractStatus.ABSTAINED
        # No NOOP action exists in the enum
        assert not any(
            a.action_type.value == "NOOP"
            for a in contract.actions
        )


class TestPlanLevelBudget:
    def test_plan_caps_total_actions(self):
        h_list = [
            _make_hyp(subtype="NUMERICAL_MISMATCH", retrieval_targets=[f"sym{i}"])
            for i in range(5)
        ]
        budget = PlanBudget(max_total_actions=3, max_total_budget=20)
        contracts = build_search_plan(h_list, budget=budget)
        # Sum of actionable actions across accepted contracts <= 3
        total = sum(
            len(c.actions)
            for c in contracts
            if c.status == ContractStatus.ACTIONABLE
        )
        assert total <= 3

    def test_plan_priority_by_confidence(self):
        h_low = _make_hyp(
            subtype="NUMERICAL_MISMATCH",
            confidence="low",
            retrieval_targets=["a"],
            failure_sites=[],
        )
        h_high = _make_hyp(
            subtype="NUMERICAL_MISMATCH",
            confidence="high",
            retrieval_targets=["b"],
            failure_sites=[],
        )
        budget = PlanBudget(max_total_actions=1, max_total_budget=20)
        contracts = build_search_plan([h_low, h_high], budget=budget)
        # High confidence should be preferred when budget is tight
        actionable = [c for c in contracts if c.status == ContractStatus.ACTIONABLE]
        assert len(actionable) == 1
        assert actionable[0].hypothesis_id == h_high.hypothesis_id


class TestSubtypeCoverage:
    """Every actionable subtype in registry must produce a contract with
    a target whose kind is compatible with the action_type."""

    def test_all_subtypes_have_template(self):
        from condiag.diagnosis.alignment import SUBTYPE_REGISTRY

        env_only = {"MISSING_DATA_FILE", "MISSING_SYSTEM_DEP", "UNCLASSIFIED"}

        for type_name, subtypes in SUBTYPE_REGISTRY.items():
            for subtype in subtypes:
                if subtype in env_only:
                    continue
                h = _make_hyp(
                    subtype=subtype,
                    failure_sites=["src/x.py:1"],
                    retrieval_targets=["X"],
                    deficiency_type=ContextDeficiencyType(type_name),
                )
                contract = build_search_contract(h)
                # Actionable means we have an action + compatible target
                if contract.status != ContractStatus.ACTIONABLE:
                    continue
                action = contract.actions[0]
                from condiag.diagnosis.search_contract import _ACTION_TARGET_KINDS
                assert action.target.kind in _ACTION_TARGET_KINDS[action.action_type], (
                    f"subtype {subtype!r}: action_type={action.action_type.value}, "
                    f"target_kind={action.target.kind.value} not compatible"
                )


class TestSerializePlan:
    def test_serialize_with_status_counts(self):
        h = _make_hyp(subtype="NUMERICAL_MISMATCH", retrieval_targets=["Foo"])
        c = build_search_contract(h)
        plan = serialize_plan([c], [h])
        assert plan["version"] == "2"
        assert "n_actionable" in plan
        assert "n_abstained" in plan
        assert "n_invalid" in plan
        s = json.dumps(plan)
        assert "FRAME_ATTRIBUTE_PROPAGATION" in s or "NUMERICAL_MISMATCH" in s


class TestShadowArtifacts:
    def test_write_shadow_artifacts(self, tmp_path):
        h = _make_hyp(subtype="FRAME_ATTRIBUTE_PROPAGATION", failure_sites=["src/foo.py:42"])
        c = build_search_contract(h)
        ledger = EvidenceLedger()
        ledger.add(EvidenceItem(
            evidence_id="E_test",
            cluster_id="C1",
            source=EvidenceSource.STACK_FRAME,
            location="src/foo.py:42",
            content="frame in foo",
        ))
        paths = write_shadow_artifacts(
            tmp_path,
            contracts=[c],
            hypotheses=[h],
            ledger=ledger,
            validation_report={"n_issues": 0},
        )
        assert Path(paths["evidence_items"]).exists()
        assert Path(paths["diagnosis_hypotheses"]).exists()
        assert Path(paths["search_contracts"]).exists()
        assert Path(paths["contract_validation"]).exists()

        # JSON parses
        evidence_json = json.loads(Path(paths["evidence_items"]).read_text())
        assert evidence_json["n_items"] >= 1
        contract_json = json.loads(Path(paths["search_contracts"]).read_text())
        assert len(contract_json["contracts"]) == 1


class TestRealCanaryWithHypotheses:
    """End-to-end: real canary log → extract → cluster → diagnose → contract.
    The KEY validation: NO action target should be a primitive literal
    (Time, float, etc.) leaking from error message tokens."""

    @pytest.fixture(autouse=True)
    def skip_if_no_log(self):
        from tests.test_p1_3 import REAL_CANARY_LOG
        if REAL_CANARY_LOG is None:
            pytest.skip("no real canary log available")

    def test_real_log_no_primitive_targets(self):
        from condiag.diagnosis.signals.pytest_extractor import extract_test_log
        from condiag.diagnosis.failure_event import (
            reasoner_v2_cluster,
        )
        from condiag.diagnosis.alignment import reasoner_v2_diagnose
        from condiag.diagnosis.hypothesis import from_subtyped_diagnosis
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

        # KEY invariant: no action target is a primitive literal
        for c in contracts:
            for a in c.actions:
                assert not is_primitive_literal(a.target.value), (
                    f"primitive literal leaked: hyp={c.hypothesis_id} "
                    f"target={a.target.value!r}"
                )
