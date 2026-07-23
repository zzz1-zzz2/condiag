"""Tests for offline Shadow Replay (condiag.acquisition.replay)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from condiag.acquisition.replay import ReplaySummary, run_replay
from condiag.diagnosis.search_contract import PlanBudget
from condiag.diagnosis.signals.schema import (
    PatchSignals,
    RuntimeFailureFeatureBundle,
    RuntimeInstanceSignals,
    TestFailureSignal,
    TestLogSignals,
    TrajectorySignals,
)
from tests.test_p1_3 import REAL_CANARY_LOG


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def astropy_like_repo(tmp_path: Path) -> Path:
    """Minimal astropy-like package with source + tests."""
    bf = tmp_path / "astropy" / "coordinates" / "builtin_frames"
    bf.mkdir(parents=True)
    (bf / "__init__.py").write_text("from . import itrs_observed_transforms\n")
    (bf / "itrs_observed_transforms.py").write_text(
        "from .itrs import ITRS\n"
        "from .altaz import AltAz\n"
        "def itrs_to_observed(itrs_coo, observed_frame):\n"
        "    return observed_frame\n"
    )
    (bf / "itrs.py").write_text("class ITRS:\n    pass\n")
    (bf / "altaz.py").write_text("class AltAz:\n    pass\n")
    (bf / "hadec.py").write_text("class HADec:\n    pass\n")
    tdir = tmp_path / "tests"
    tdir.mkdir()
    (tdir / "test_itrs.py").write_text(
        "from astropy.coordinates.builtin_frames.itrs import ITRS\n"
        "def test_itrs():\n    assert True\n"
    )
    # Init git repo so pre/post clean checks work
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, timeout=10)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, timeout=10)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, timeout=10)
    subprocess.run(["git", "add", "."], cwd=tmp_path, timeout=10)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, timeout=10)
    return tmp_path


def _build_minimal_bundle(tmp_path: Path) -> Path:
    """Create a valid bundle JSON file for testing."""
    p = tmp_path / "bundle.json"
    bundle = RuntimeFailureFeatureBundle(
        test_log=TestLogSignals(
            framework="pytest",
            failed_tests=["test_foo", "test_bar"],
            failures=[
                TestFailureSignal(
                    test_name="test_foo",
                    exception_type="TypeError",
                    error_message="TypeError: bad value",
                    assertion_line=">       assert False",
                    stack_frames=[],
                ),
                TestFailureSignal(
                    test_name="test_bar",
                    exception_type="AssertionError",
                    error_message="AssertionError:",
                    assertion_line=">       assert True",
                    stack_frames=[],
                ),
            ],
        ),
        patch=PatchSignals(
            edited_files=["src/foo.py"],
            added_lines=10, deleted_lines=0,
        ),
        trajectory=TrajectorySignals(
            total_tool_calls=10,
            viewed_files=["src/foo.py", "tests/test_foo.py"],
        ),
        instance=RuntimeInstanceSignals(
            instance_id="test__test_example",
        ),
    )
    p.write_text(bundle.model_dump_json(indent=2))
    return p


# ── Tests ───────────────────────────────────────────────────────────


class TestReplayBasic:
    def test_replay_produces_both_shadow_dirs(self, tmp_path):
        """Valid bundle + repo → p1_3c_shadow/ and p1_3d_shadow/ both created."""
        bundle_path = _build_minimal_bundle(tmp_path)
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, timeout=10)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, timeout=10)
        (repo / "src").mkdir()
        (repo / "src" / "foo.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=repo, timeout=10)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, timeout=10)

        out = tmp_path / "out"
        summary = run_replay(
            bundle_path=bundle_path,
            repo_root=repo,
            output_dir=out,
            run_id="test_replay",
        )
        assert (out / "p1_3c_shadow" / "evidence_items.json").exists()
        assert (out / "p1_3c_shadow" / "diagnosis_hypotheses.json").exists()
        assert (out / "p1_3c_shadow" / "search_contracts.json").exists()
        assert (out / "p1_3d_shadow" / "acquisition_results.json").exists()
        assert (out / "p1_3d_shadow" / "router_validation.json").exists()

    def test_replay_manifest_written(self, tmp_path):
        """Manifest JSON contains all required fields."""
        bundle_path = _build_minimal_bundle(tmp_path)
        repo = tmp_path / "repo2"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, timeout=10)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, timeout=10)
        subprocess.run(["git", "add", "."], cwd=repo, timeout=10)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, timeout=10)

        out = tmp_path / "out2"
        run_replay(
            bundle_path=bundle_path,
            repo_root=repo,
            output_dir=out,
            run_id="test_manifest",
        )
        manifest = json.loads((out / "replay_manifest.json").read_text())
        for field in ("run_id", "instance_id", "bundle_sha256", "gold_accessed",
                       "repo_modified_by_replay", "repo_head_sha"):
            assert field in manifest, f"missing field: {field}"
        assert manifest["gold_accessed"] is False
        assert manifest["repo_modified_by_replay"] is False

    def test_repo_not_modified(self, tmp_path):
        """Replay must not modify any repo file."""
        bundle_path = _build_minimal_bundle(tmp_path)
        repo = tmp_path / "repo3"
        repo.mkdir()
        (repo / "keep.py").write_text("# keep\n")
        subprocess.run(["git", "init", "-q"], cwd=repo, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, timeout=10)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, timeout=10)
        subprocess.run(["git", "add", "."], cwd=repo, timeout=10)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, timeout=10)
        pre = (repo / "keep.py").read_text()

        out = tmp_path / "out3"
        run_replay(
            bundle_path=bundle_path,
            repo_root=repo,
            output_dir=out,
            run_id="test_clean",
        )
        assert (repo / "keep.py").read_text() == "# keep\n"

    def test_missing_bundle_exits(self, tmp_path):
        """Missing bundle Path should raise FileNotFoundError."""
        fake = tmp_path / "no_such_bundle.json"
        repo = tmp_path / "repo4"
        repo.mkdir()
        out = tmp_path / "out4"
        with pytest.raises(FileNotFoundError):
            run_replay(bundle_path=fake, repo_root=repo, output_dir=out, run_id="test")

    def test_provenance_on_router_hits(self, astropy_like_repo, tmp_path):
        """Every Router hit must have non-empty action_id matching its action."""
        bundle_path = _build_minimal_bundle(tmp_path)
        out = tmp_path / "out5"
        summary = run_replay(
            bundle_path=bundle_path,
            repo_root=astropy_like_repo,
            output_dir=out,
            run_id="provenance_check",
        )
        results = json.loads(
            (out / "p1_3d_shadow" / "acquisition_results.json").read_text()
        )["results"]
        for r in results:
            for h in r.get("hits", []):
                assert h.get("action_id"), f"hit missing action_id: {h}"
                # action_id in the hit should match the result-level action_id
                assert h["action_id"] == r["action_id"], \
                    f"hit action_id {h['action_id']} != result action_id {r['action_id']}"


# ── Real canary bundle integration ──────────────────────────────────


class TestReplayRealCanary:
    """Full Shadow replay using an actual canary bundle (when available)."""

    @pytest.fixture(autouse=True)
    def skip_if_no_canary_log(self):
        if REAL_CANARY_LOG is None:
            pytest.skip("no real canary log available")

    def test_replay_real_canary(self, astropy_like_repo, tmp_path):
        """Full pipeline on a real canary log must not crash."""
        if not REAL_CANARY_LOG:
            pytest.skip("no real canary log")
        from condiag.diagnosis.signals.pytest_extractor import extract_test_log

        tl = extract_test_log(str(REAL_CANARY_LOG))
        bundle = RuntimeFailureFeatureBundle(
            test_log=tl,
            patch=PatchSignals(
                edited_files=["astropy/coordinates/builtin_frames/itrs_observed_transforms.py"],
                added_lines=121, deleted_lines=0,
            ),
            trajectory=TrajectorySignals(
                total_tool_calls=66,
                viewed_files=["src/foo.py", "tests/test_foo.py"],
            ),
            instance=RuntimeInstanceSignals(instance_id="astropy__astropy-13398"),
        )
        bundle_path = tmp_path / "real_bundle.json"
        bundle_path.write_text(bundle.model_dump_json())
        out = tmp_path / "real_out"
        summary = run_replay(
            bundle_path=bundle_path,
            repo_root=astropy_like_repo,
            output_dir=out,
            run_id="real_canary_replay",
        )
        assert summary.n_clusters >= 0
        assert (out / "p1_3c_shadow" / "evidence_items.json").exists()
        assert (out / "p1_3d_shadow" / "acquisition_results.json").exists()
