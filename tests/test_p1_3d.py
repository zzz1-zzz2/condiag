"""Tests for P1-3D: AcquisitionRouter + executors."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from condiag.acquisition.artifact_writer import (
    RouterValidationReport,
    validate_results,
    write_acquisition_artifacts,
)
from condiag.acquisition.definition_search import find_definition
from condiag.acquisition.related_test_search import find_related_tests
from condiag.acquisition.router import AcquisitionRouter
from condiag.acquisition.schema import (
    AcquisitionHit,
    AcquisitionResult,
    AcquisitionStatus,
)
from condiag.diagnosis.search_contract import (
    ContractStatus,
    SearchAction,
    SearchActionType,
    SearchContract,
    SearchTarget,
    SearchTargetKind,
)


def _make_repo(tmp_path: Path) -> Path:
    """Create a tiny Python project with one source file, one test."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "geometry.py").write_text(
        'def perimeter(width, height):\n'
        '    return 2 * (width + height)\n'
        '\n'
        'def area(width, height):\n'
        '    return width * height\n'
        '\n'
        'class Circle:\n'
        '    def __init__(self, radius):\n'
        '        self.radius = radius\n'
        '    def circumference(self):\n'
        '        return 2 * 3.14 * self.radius\n'
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "__init__.py").write_text("")
    (tests / "test_geometry.py").write_text(
        'from pkg.geometry import perimeter, area, Circle\n'
        'def test_perimeter():\n'
        '    assert perimeter(2, 3) == 10\n'
        'def test_circle_circumference():\n'
        '    c = Circle(1.0)\n'
        '    assert c.circumference() > 0\n'
    )
    (tests / "test_other.py").write_text(
        'def test_unrelated():\n'
        '    assert 1 + 1 == 2\n'
    )
    return tmp_path


# ── FIND_DEFINITION executor ────────────────────────────────────────


class TestFindDefinition:
    def test_failure_site_returns_enclosing_function(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="pkg/geometry.py:2",  # inside `perimeter`
            kind=SearchTargetKind.FAILURE_SITE,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=target,
            budget=2,
        )
        result = find_definition(action, repo)
        assert result.status == AcquisitionStatus.FOUND
        assert len(result.hits) == 1
        h = result.hits[0]
        assert h.symbol == "perimeter"
        assert "perimeter(width, height)" in h.content

    def test_failure_site_class_method(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="pkg/geometry.py:11",  # inside circumference method
            kind=SearchTargetKind.FAILURE_SITE,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=target,
        )
        result = find_definition(action, repo)
        assert result.status == AcquisitionStatus.FOUND
        # The enclosing function is `circumference`
        assert result.hits[0].symbol == "circumference"

    def test_failure_site_outside_repo(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="/etc/passwd:5",
            kind=SearchTargetKind.FAILURE_SITE,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=target,
        )
        result = find_definition(action, repo)
        assert result.status == AcquisitionStatus.NOT_FOUND
        assert "not in repo" in result.stop_reason

    def test_symbol_target_finds_definition(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="perimeter",
            kind=SearchTargetKind.SYMBOL,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=target,
            budget=3,
        )
        result = find_definition(action, repo)
        assert result.status == AcquisitionStatus.FOUND
        assert any(
            h.symbol == "perimeter" for h in result.hits
        )

    def test_symbol_target_caps_at_budget(self, tmp_path):
        repo = _make_repo(tmp_path)
        # Use a name that appears in multiple files
        (repo / "tests" / "test_geometry.py").write_text(
            'from pkg.geometry import perimeter\n'
            'def test_a():\n'
            '    assert perimeter(1, 1) == 4\n'
            'def test_b():\n'
            '    assert perimeter(2, 2) == 8\n'
        )
        (repo / "tests" / "test_geometry2.py").write_text(
            'from pkg.geometry import perimeter\n'
            'def test_c():\n'
            '    assert perimeter(3, 3) == 12\n'
        )
        target = SearchTarget(
            value="perimeter",
            kind=SearchTargetKind.SYMBOL,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=target,
            budget=1,
        )
        result = find_definition(action, repo)
        assert len(result.hits) <= 1

    def test_invalid_target_line(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="pkg/geometry.py:not_a_line",
            kind=SearchTargetKind.FAILURE_SITE,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=target,
        )
        result = find_definition(action, repo)
        assert result.status == AcquisitionStatus.INVALID_TARGET


# ── FIND_RELATED_TESTS executor ─────────────────────────────────────


class TestFindRelatedTests:
    def test_finds_test_with_symbol_in_body(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="perimeter",
            kind=SearchTargetKind.SYMBOL,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_RELATED_TESTS,
            target=target,
            budget=5,
        )
        result = find_related_tests(action, repo)
        assert result.status == AcquisitionStatus.FOUND
        # test_geometry.py mentions perimeter → should be top hit
        assert any(
            "test_geometry" in h.file_path for h in result.hits
        )

    def test_unrelated_tests_get_low_score_or_filtered(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="Circle",
            kind=SearchTargetKind.SYMBOL,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_RELATED_TESTS,
            target=target,
            budget=5,
        )
        result = find_related_tests(action, repo)
        # test_other.py should NOT be top hit (no symbol match)
        top = result.hits[0] if result.hits else None
        if top:
            assert "test_geometry" in top.file_path

    def test_already_seen_marking(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="perimeter",
            kind=SearchTargetKind.SYMBOL,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_RELATED_TESTS,
            target=target,
        )
        # Mark test_geometry.py as already seen
        result = find_related_tests(
            action, repo,
            r1_viewed_files={"tests/test_geometry.py"},
        )
        for h in result.hits:
            if "test_geometry" in h.file_path:
                assert "already_seen=True" in h.relevance_reason

    def test_no_matches_returns_not_found(self, tmp_path):
        repo = _make_repo(tmp_path)
        target = SearchTarget(
            value="NonexistentClassXyz",
            kind=SearchTargetKind.SYMBOL,
        )
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_RELATED_TESTS,
            target=target,
        )
        result = find_related_tests(action, repo)
        assert result.status == AcquisitionStatus.NOT_FOUND


# ── Router dispatcher ──────────────────────────────────────────────


class TestRouterDispatch:
    def test_unsupported_action_returns_unsupported(self, tmp_path):
        router = AcquisitionRouter(tmp_path)
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_CALLERS,  # not in v1
            target=SearchTarget(
                value="perimeter",
                kind=SearchTargetKind.SYMBOL,
            ),
        )
        result = router.dispatch(action)
        assert result.status == AcquisitionStatus.UNSUPPORTED

    def test_dispatch_contract(self, tmp_path):
        router = AcquisitionRouter(tmp_path)
        actions = [
            SearchAction(
                action_id="A1",
                action_type=SearchActionType.FIND_DEFINITION,
                target=SearchTarget(value="pkg/geometry.py:2", kind=SearchTargetKind.FAILURE_SITE),
            ),
            SearchAction(
                action_id="A2",
                action_type=SearchActionType.FIND_RELATED_TESTS,
                target=SearchTarget(value="perimeter", kind=SearchTargetKind.SYMBOL),
            ),
        ]
        contract = SearchContract(
            hypothesis_id="H1",
            status=ContractStatus.ACTIONABLE,
            actions=actions,
        )
        results = router.dispatch_contract(contract)
        assert len(results) == 2
        assert results[0].action_id == "A1"
        assert results[1].action_id == "A2"


# ── Artifact writer invariants ──────────────────────────────────────


class TestArtifactWriter:
    def test_no_out_of_bounds_in_artifact(self, tmp_path):
        repo = _make_repo(tmp_path)
        router = AcquisitionRouter(repo)
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=SearchTarget(value="pkg/geometry.py:2", kind=SearchTargetKind.FAILURE_SITE),
        )
        result = router.dispatch(action)
        paths = write_acquisition_artifacts(
            tmp_path / "shadow",
            [result],
            repo,
            run_id="run1",
        )
        # All four expected files written
        assert Path(paths["acquisition_results"]).exists()
        assert Path(paths["router_validation"]).exists()
        rep = json.loads(Path(paths["router_validation"]).read_text())
        # Out-of-bounds file paths must be reported (none expected here)
        assert rep["out_of_bounds_files"] == []
        assert rep["n_actions"] == 1
        assert rep["n_found"] == 1

    def test_unsupported_actions_still_recorded(self, tmp_path):
        repo = _make_repo(tmp_path)
        router = AcquisitionRouter(repo)
        action = SearchAction(
            action_id="A1",
            action_type=SearchActionType.FIND_CALLEES,
            target=SearchTarget(value="perimeter", kind=SearchTargetKind.SYMBOL),
        )
        result = router.dispatch(action)
        paths = write_acquisition_artifacts(
            tmp_path / "shadow",
            [result],
            repo,
        )
        rep = json.loads(Path(paths["router_validation"]).read_text())
        assert rep["n_unsupported"] == 1
