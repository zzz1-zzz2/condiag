"""Shadow smoke test: Router against real astropy-like repo structure.

Builds a minimal but realistic astropy directory tree with the files
that the real astropy-13398 canary touches. Runs the full Router shadow
pipeline and validates acquisition artifacts.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from condiag.acquisition.artifact_writer import write_acquisition_artifacts
from condiag.acquisition.router import AcquisitionRouter
from condiag.diagnosis.search_contract import (
    ContractStatus,
    SearchAction,
    SearchActionType,
    SearchContract,
    SearchTarget,
    SearchTargetKind,
)


@pytest.fixture(scope="module")
def astropy_like_repo(tmp_path_factory) -> Path:
    """Build an astropy-like Python package focused on coordinates.

    Mirrors the structure affected by astropy-13398's patch:
      builtin_frames/__init__.py
      itrs_observed_transforms.py (what the agent added)
      itrs.py, altaz.py, cirs_observed_transforms.py, baseframe.py
      test_intermediate_transformations.py
    """
    root = tmp_path_factory.mktemp("astropy_repo")
    bf = root / "astropy" / "coordinates" / "builtin_frames"
    bf.mkdir(parents=True)
    (bf / "__init__.py").write_text(
        "from . import cirs_observed_transforms\n"
        "from . import icrs_observed_transforms\n"
        "from . import intermediate_rotation_transforms\n"
        "from . import itrs_observed_transforms\n"
        "from . import ecliptic_transforms\n"
    )
    (bf / "itrs_observed_transforms.py").write_text(
        "from astropy.coordinates.baseframe import frame_transform_graph\n"
        "from astropy.coordinates.transformations import FunctionTransformWithFiniteDifference\n"
        "from .itrs import ITRS\n"
        "from .altaz import AltAz\n"
        "from .hadec import HADec\n"
        "\n"
        "def itrs_to_observed_mat(observed_frame):\n"
        "    lon, lat, height = observed_frame.location.to_geodetic('WGS84')\n"
        "    elong = lon.to_value(u.radian)\n"
        "    if isinstance(observed_frame, AltAz):\n"
        "        elat = lat.to_value(u.radian)\n"
        "        minus_x = np.eye(3)\n"
        "        minus_x[0][0] = -1.0\n"
        "        mat = (minus_x @ rotation_matrix(PIOVER2 - elat, 'y', unit=u.radian)\n"
        "               @ rotation_matrix(elong, 'z', unit=u.radian))\n"
        "    return mat\n"
        "\n"
        "@frame_transform_graph.transform(FunctionTransformWithFiniteDifference, ITRS, AltAz)\n"
        "def itrs_to_observed(itrs_coo, observed_frame):\n"
        "    topocentric_itrs_repr = (itrs_coo.cartesian\n"
        "                             - observed_frame.location.get_itrs().cartesian)\n"
        "    rep = topocentric_itrs_repr.transform(itrs_to_observed_mat(observed_frame))\n"
        "    return observed_frame.realize_frame(rep)\n"
    )
    (bf / "itrs.py").write_text(
        "class ITRS:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        pass\n"
    )
    (bf / "altaz.py").write_text(
        "class AltAz:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        pass\n"
    )
    (bf / "hadec.py").write_text(
        "class HADec:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        pass\n"
    )
    (bf / "cirs_observed_transforms.py").write_text(
        "from .itrs import ITRS\n"
        "from .altaz import AltAz\n"
        "\n"
        "def cirs_to_altaz(cirs_coo, altaz_frame):\n"
        "    return altaz_frame.realize_frame(cirs_coo.cartesian)\n"
    )
    (bf / "utils.py").write_text("PIOVER2 = 1.5707963267948966\n")
    (root / "astropy" / "coordinates" / "baseframe.py").write_text(
        "class BaseCoordinateFrame:\n"
        "    def __init__(self, *args, **kwargs):\n"
        "        for key in kwargs:\n"
        "            if not hasattr(self, key):\n"
        "                raise TypeError(\n"
        "                    f'Coordinate frame {self.__class__.__name__} got unexpected '\n"
        "                    f'keywords: {list(kwargs)}')\n"
        "        pass\n"
    )
    (root / "astropy" / "coordinates" / "transformations.py").write_text(
        "class FunctionTransformWithFiniteDifference:\n"
        "    pass\n"
        "class frame_transform_graph:\n"
        "    @staticmethod\n"
        "    def transform(func_type, src, dst):\n"
        "        return lambda f: f\n"
    )
    # Test file
    tdir = root / "astropy" / "coordinates" / "tests"
    tdir.mkdir(parents=True)
    (tdir / "test_intermediate_transformations.py").write_text(
        "from ..builtin_frames.itrs import ITRS\n"
        "from ..builtin_frames.altaz import AltAz\n"
        "from ..builtin_frames.cirs_observed_transforms import cirs_to_altaz\n"
        "\n"
        "def test_icrs_cirs():\n"
        "    assert True\n"
        "def test_itrs_topo_to_altaz_with_refraction():\n"
        "    assert True\n"
        "def test_gcrs_altaz_bothroutes():\n"
        "    # Two routes: via ICRS vs via ITRS\n"
        "    assert True\n"
        "def test_itrs_straight_overhead():\n"
        "    assert True\n"
        "def test_cirs_itrs_topo():\n"
        "    assert True\n"
        "def test_itrs_topo_to_hadec_with_refraction():\n"
        "    assert True\n"
    )
    return root


class TestAstropySmoke:
    """Router shadow smoke against a real astropy-like repo."""

    def _check_provenance(self, action: SearchAction, result) -> None:
        """Verify action_id and action_type survive through dispatch."""
        assert result.action_id == action.action_id, \
            f"provenance: expected {action.action_id}, got {result.action_id}"
        assert result.action_type == action.action_type, \
            f"provenance: expected {action.action_type}, got {result.action_type}"
        for hit in result.hits:
            assert hit.action_id == action.action_id, \
                f"hit provenance: expected {action.action_id}, got {hit.action_id}"

    def test_find_definition_failure_site(self, astropy_like_repo):
        """FAILURE_SITE target inside itrs_to_observed → returns AST-enclosing function."""
        router = AcquisitionRouter(astropy_like_repo)
        action = SearchAction(
            action_id="A_astropy_1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=SearchTarget(
                value="astropy/coordinates/builtin_frames/itrs_observed_transforms.py:21",
                kind=SearchTargetKind.FAILURE_SITE,
            ),
            budget=2,
        )
        result = router.dispatch(action)
        self._check_provenance(action, result)
        assert result.status.value == "FOUND"
        assert result.hits[0].symbol in ("itrs_to_observed", "itrs_to_observed_mat")

    def test_find_definition_budget_limit_set(self, astropy_like_repo):
        """Result must set budget_limit and not exceed it."""
        router = AcquisitionRouter(astropy_like_repo)
        action = SearchAction(
            action_id="A_budget_1",
            action_type=SearchActionType.FIND_DEFINITION,
            target=SearchTarget(value="ITRS", kind=SearchTargetKind.SYMBOL),
            budget=2,
        )
        result = router.dispatch(action)
        assert result.budget_limit == 2, f"expected budget_limit=2, got {result.budget_limit}"
        assert result.budget_used <= result.budget_limit

    def test_related_tests_budget_limit_set(self, astropy_like_repo):
        """Result sets scan_limit and restricts budget_used."""
        router = AcquisitionRouter(astropy_like_repo)
        action = SearchAction(
            action_id="A_budget_2",
            action_type=SearchActionType.FIND_RELATED_TESTS,
            target=SearchTarget(value="ITRS", kind=SearchTargetKind.SYMBOL),
            budget=3,
        )
        result = router.dispatch(action)
        # scan_limit should be at least 200
        assert result.scan_limit >= 200
        assert result.budget_used <= result.budget_limit

    def test_find_definition_symbol(self, astropy_like_repo):
        """SYMBOL target 'ITRS' → finds the class definition."""
        router = AcquisitionRouter(astropy_like_repo)
        action = SearchAction(
            action_id="A_astropy_2",
            action_type=SearchActionType.FIND_DEFINITION,
            target=SearchTarget(
                value="ITRS",
                kind=SearchTargetKind.SYMBOL,
            ),
            budget=3,
        )
        result = router.dispatch(action)
        assert result.status.value == "FOUND"
        assert any("class ITRS" in h.content for h in result.hits)

    def test_find_related_tests_returns_test_file(self, astropy_like_repo):
        """SYMBOL target 'cirs_to_altaz' → returns test_intermediate_transformations."""
        router = AcquisitionRouter(astropy_like_repo)
        action = SearchAction(
            action_id="A_astropy_3",
            action_type=SearchActionType.FIND_RELATED_TESTS,
            target=SearchTarget(
                value="cirs_to_altaz",
                kind=SearchTargetKind.SYMBOL,
            ),
            budget=3,
        )
        result = router.dispatch(action)
        assert result.status.value == "FOUND"
        assert any("test_intermediate_transformations" in h.file_path for h in result.hits)

    def test_find_related_tests_marks_already_seen(self, astropy_like_repo):
        """Hits must be marked already_seen when in r1_viewed_files."""
        router = AcquisitionRouter(
            astropy_like_repo,
            r1_viewed_files={"astropy/coordinates/tests/test_intermediate_transformations.py"},
        )
        action = SearchAction(
            action_id="A_astropy_4",
            action_type=SearchActionType.FIND_RELATED_TESTS,
            target=SearchTarget(
                value="ITRS",
                kind=SearchTargetKind.SYMBOL,
            ),
            budget=3,
        )
        result = router.dispatch(action)
        for hit in result.hits:
            if "test_intermediate_transformations" in hit.file_path:
                assert "already_seen=True" in hit.relevance_reason

    def test_shadow_artifacts_written(self, astropy_like_repo, tmp_path):
        """Full pipeline → acquisition_results.json + router_validation.json."""
        router = AcquisitionRouter(astropy_like_repo)
        actions = [
            SearchAction(
                action_id="A_st_1",
                action_type=SearchActionType.FIND_DEFINITION,
                target=SearchTarget(value="ITRS", kind=SearchTargetKind.SYMBOL),
            ),
            SearchAction(
                action_id="A_st_2",
                action_type=SearchActionType.FIND_RELATED_TESTS,
                target=SearchTarget(
                    value="cirs_to_altaz",
                    kind=SearchTargetKind.SYMBOL,
                ),
            ),
        ]
        contract = SearchContract(
            hypothesis_id="H_smoke",
            status=ContractStatus.ACTIONABLE,
            actions=actions,
        )
        results = router.dispatch_contract(contract)
        paths = write_acquisition_artifacts(
            tmp_path / "shadow_smoke",
            results,
            astropy_like_repo,
            run_id="smoke_astropy",
        )
        assert Path(paths["acquisition_results"]).exists()
        assert Path(paths["router_validation"]).exists()
        data = json.loads(Path(paths["acquisition_results"]).read_text())
        assert data["n_results"] == 2
        # No out-of-bounds or budget violations
        val = json.loads(Path(paths["router_validation"]).read_text())
        assert val["out_of_bounds_files"] == []
        assert val["budget_violations"] == []
