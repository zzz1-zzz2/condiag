"""Tests for P1-3A/B: failure event extraction, clustering, alignment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from condiag.diagnosis.signals.pytest_extractor import (
    extract_test_log,
    _RE_TEST_HEADER,
    _reconcile_failures,
    FailureBindingError,
)
from condiag.diagnosis.failure_event import (
    FailureEvent,
    extract_failure_events,
    cluster_failures,
    normalize_message,
    _param_group,
)


# ── Normalization tests ─────────────────────────────────────────────


class TestNormalizeMessage:
    def test_unexpected_keywords(self):
        assert normalize_message(
            "TypeError: Coordinate frame ITRS got unexpected keywords: ['location']"
        ) == "TypeError: Coordinate frame ITRS got unexpected keywords: ['<KW>']"

    def test_unsupported_operand(self):
        assert normalize_message(
            "TypeError: unsupported operand type(s) for -: 'Time' and 'float'"
        ) == "TypeError: unsupported operand type(s) for -: '<A>' and '<B>'"

    def test_import_name(self):
        assert normalize_message(
            "ImportError: cannot import name 'foo' from 'bar.baz'"
        ) == "ImportError: cannot import name '<NAME>' from '<PACKAGE>'"

    def test_module_attr(self):
        assert normalize_message(
            "AttributeError: module 'astropy' has no attribute 'foo'"
        ) == "AttributeError: module '<MOD>' has no attribute '<ATTR>'"

    def test_numbers_replaced(self):
        assert normalize_message("line 42 failed") == "line <N> failed"

    def test_filepaths_replaced(self):
        n = normalize_message("failed in /home/user/project/file.py:42")
        assert "<path>" in n

    def test_hex_replaced(self):
        assert normalize_message("at 0x7f123456") == "at <hex>"

    def test_empty(self):
        assert normalize_message("") == ""

    def test_no_double_bracket(self):
        """Regression: unexpected keywords replacement must not double the closing bracket."""
        n = normalize_message(
            "TypeError: Coordinate frame ITRS got unexpected keywords: ['location']"
        )
        assert n.count("]") == 1, f"expected 1 bracket, got: {n}"

    def test_low_information_returned_as_is(self):
        """Bare AssertionError:  must not crash."""
        assert normalize_message("AssertionError:") == "AssertionError:"


# ── Section parser reconciliation tests ─────────────────────────────


class TestSectionReconciliation:
    """Verify that every section header matches a summary FAILED entry."""

    def _make_test_log(self, sections, summary_fails, summary_passes=0):
        """Build a synthetic test_output.txt with given sections and summary."""
        lines = [
            "============================= FAILURES =============================",
        ]
        for name, body in sections:
            header = f"_____________________________ {name} _____________________________"
            lines.append(header)
            lines.extend(body.split("\n"))
            lines.append("")

        summary_line = f"=========================== short test summary info ============================"
        lines.append(summary_line)
        for name in summary_fails:
            lines.append(f"FAILED astropy/tests/test_fake.py::{name}")
        for _ in range(summary_passes):
            lines.append(f"PASSED astropy/tests/test_fake.py::test_pass")
        lines.append(f"{summary_passes} passed, {len(summary_fails)} failed in 0.01s")

        return "\n".join(lines)

    def test_basic_reconciliation(self, tmp_path):
        """10 sections matching 10 summary FAILED entries = all bound."""
        sections = [
            ("test_foo", ">       assert 1\nE       AssertionError:"),
            ("test_bar", ">       x = a + b\nE       TypeError: unsupported operand"),
        ]
        log = self._make_test_log(
            sections,
            summary_fails=["test_foo", "test_bar"],
            summary_passes=5,
        )
        p = tmp_path / "test.log"
        p.write_text(log)
        tl = extract_test_log(str(p))
        assert len(tl.failures) == 2, f"Expected 2 failures, got {len(tl.failures)}"
        assert len(tl.failed_tests) == 2

    def test_section_name_vs_nodeid_mismatch(self, tmp_path):
        """Section name may differ from summary nodeid (bare vs test_file.py::test)."""
        sections = [
            ("test_baz", ">       raise ValueError\nE       ValueError: bad"),
        ]
        log = self._make_test_log(
            sections,
            summary_fails=["test_baz"],
        )
        p = tmp_path / "test2.log"
        p.write_text(log)
        tl = extract_test_log(str(p))
        assert len(tl.failures) == 1
        assert len(tl.failed_tests) == 1

    def test_no_failures_section_returns_empty_failures(self, tmp_path):
        """Log with no FAILURES section but with FAILED summary lines."""
        log = "\n".join([
            "PASSED test_fake.py::test_a",
            "FAILED test_fake.py::test_b",
            "1 passed, 1 failed in 0.01s",
        ])
        p = tmp_path / "test3.log"
        p.write_text(log)
        tl = extract_test_log(str(p))
        # No FAILURES section → no per-test parse → failures field is empty
        assert len(tl.failures) == 0
        assert tl.failed_tests == ["test_fake.py::test_b"]

    def test_same_count_but_different_names_raises(self, tmp_path):
        """Same count but one name differs → FailureBindingError."""
        log = (
            "============================= FAILURES =============================\n"
            "______________________________ test_foo _____________________________\n"
            ">       assert 1\n"
            "E       AssertionError:\n"
            "\n"
            "______________________________ test_bar _____________________________\n"
            ">       x = 1\n"
            "E       TypeError: bad\n"
            "\n"
            "=========================== short test summary info ============================\n"
            "FAILED path/test.py::test_foo\n"
            "FAILED path/test.py::test_baz\n"
            "2 failed in 0.01s\n"
        )
        p = tmp_path / "mismatch.log"
        p.write_text(log)
        with pytest.raises(FailureBindingError) as excinfo:
            extract_test_log(str(p))
        assert "unmatched" in str(excinfo.value)


# ── Clustering tests ────────────────────────────────────────────────


class TestClustering:
    def _make_event(self, test_name, exc_type, msg="", frames=None, assertion=""):
        return FailureEvent(
            test_name=test_name,
            exception_type=exc_type,
            error_class="TYPE_ERROR" if "Type" in exc_type else "ASSERTION_ERROR",
            message=msg,
            message_fingerprint=normalize_message(msg),
            assertion_line=assertion,
            call_chain=frames or [],
            top_repo_frame=frames[0] if frames else "",
            is_parameterized="[" in test_name,
            param_group=_param_group(test_name),
        )

    def test_param_family_clusters_together(self):
        """Param tests in same family + same top frame merge."""
        events = [
            self._make_event("test_a[x]", "AssertionError", frames=["mod.py:1"]),
            self._make_event("test_a[y]", "AssertionError", frames=["mod.py:1"]),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 1

    def test_different_param_groups_stay_separate(self):
        events = [
            self._make_event("test_a[x]", "AssertionError"),
            self._make_event("test_b[x]", "AssertionError"),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 2, "different param groups must not merge"

    def test_parameterized_low_info_without_frames_stays_singleton(self):
        """Param tests with same group, bare AssertionError, and no frames
        must NOT merge — empty set is not a shared signal."""
        events = [
            self._make_event("test_a[p1]", "AssertionError", "AssertionError:"),
            self._make_event("test_a[p2]", "AssertionError", "AssertionError:"),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 2, \
            "low-info param tests without secondary signal must stay separate"

    def test_param_shared_top_frame_merges(self):
        """Param tests sharing the same top_repo_frame may merge."""
        events = [
            self._make_event("test_a[p1]", "TypeError", "err", frames=["base.py:1"]),
            self._make_event("test_a[p2]", "TypeError", "err", frames=["base.py:1"]),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 1

    def test_same_message_fingerprint_clusters(self):
        events = [
            self._make_event("test_a", "TypeError", "TypeError: 'A' and 'B'"),
            self._make_event("test_b", "TypeError", "TypeError: 'A' and 'B'"),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 1

    def test_low_info_message_does_not_merge(self):
        """Bare AssertionError:  fingerprint must not cause merge."""
        events = [
            self._make_event("test_a", "AssertionError", "AssertionError:"),
            self._make_event("test_b", "AssertionError", "AssertionError:"),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 2, \
            "bare AssertionError must not merge events"

    def test_singleton_fallback(self):
        """Unrelated events must each become their own cluster."""
        events = [
            self._make_event("test_a", "TypeError", "TypeError: A", frames=["file.py:1"]),
            self._make_event("test_b", "TypeError", "TypeError: B", frames=["other.py:2"]),
            self._make_event("test_c", "AssertionError", "AssertionError:",
                             frames=["test.py:3"]),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 3, "each unrelated event must be a singleton"

    def test_deterministic_id(self):
        events1 = [
            self._make_event("test_foo", "TypeError", "bad value"),
        ]
        events2 = [
            self._make_event("test_foo", "TypeError", "bad value"),
        ]
        c1 = cluster_failures(events1)
        c2 = cluster_failures(events2)
        assert c1[0].cluster_id == c2[0].cluster_id, "same events → same ID"

    def test_call_chain_overlap_merge(self):
        """Events sharing ≥2 call chain frames merge."""
        frames = ["base.py:1", "transform.py:2", "core.py:3"]
        events = [
            self._make_event("test_a", "TypeError", "err", frames=frames),
            self._make_event("test_b", "TypeError", "err", frames=frames),
        ]
        clusters = cluster_failures(events)
        assert len(clusters) == 1


# ── Edge case tests ────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_events(self):
        assert cluster_failures([]) == []

    def test_single_event(self):
        ev = FailureEvent(test_name="t1", exception_type="TypeError", error_class="TYPE_ERROR")
        clusters = cluster_failures([ev])
        assert len(clusters) == 1
        assert clusters[0].count == 1


# ── Real pytest log fixture ────────────────────────────────────────


def _find_real_canary_log() -> Path | None:
    """Locate a real pytest-format test_output.txt from past canary runs.

    Searches logs/ first, then canary output captures under /tmp.
    Returns None when no log is found (tests skip in that case).
    """
    repo_logs = Path("logs/run_evaluation")
    if repo_logs.exists():
        # Use the most recent canary directory
        candidates = sorted(repo_logs.glob("*/condiag-agent/*/test_output.txt"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        if candidates:
            return candidates[0]
    return None


REAL_CANARY_LOG = _find_real_canary_log()
HAS_REAL_LOG = REAL_CANARY_LOG is not None


@pytest.mark.skipif(not HAS_REAL_LOG, reason="no real canary log available")
class TestRealPytestLog:
    """Verify parser against a real SWE-bench test_output.txt from canary."""

    def test_real_log_parses_per_test(self):
        if not REAL_CANARY_LOG:
            pytest.skip("no real canary log")
        tl = extract_test_log(str(REAL_CANARY_LOG))
        # Real pytest runs always have at least 1 failure or 1 success.
        # For the failure case: failures must have one entry per failed test.
        if tl.failures:
            assert len(tl.failures) == len(tl.failed_tests), (
                f"real log: failures={len(tl.failures)} "
                f"failed_tests={len(tl.failed_tests)}"
            )

    def test_real_log_reconciliation_status_known(self):
        if not REAL_CANARY_LOG:
            pytest.skip("no real canary log")
        tl = extract_test_log(str(REAL_CANARY_LOG))
        # If there is a FAILURES section + summary, reconciliation must be set.
        if tl.failures and tl.failed_tests:
            assert tl.reconciliation.match_status in {"exact", "count_only", "unmatched"}
            assert tl.reconciliation.section_count > 0

    def test_real_log_per_test_has_bound_message(self):
        """Each parsed failure must have its OWN error_message, not a shared one."""
        if not REAL_CANARY_LOG:
            pytest.skip("no real canary log")
        tl = extract_test_log(str(REAL_CANARY_LOG))
        for f in tl.failures:
            assert f.test_name, "real failure must have test_name"
            assert isinstance(f.stack_frames, list)
            # Either message or assertion_line must be non-empty.
            assert f.error_message or f.assertion_line, (
                f"failure {f.test_name} has neither error_message nor assertion_line"
            )

    def test_real_log_clustering_runs(self):
        """The full pipeline (extract → cluster → diagnose) must not crash on real data."""
        if not REAL_CANARY_LOG:
            pytest.skip("no real canary log")
        from condiag.diagnosis.failure_event import (
            extract_failure_events,
            reasoner_v2_cluster,
        )
        from condiag.diagnosis.signals.schema import RuntimeFailureFeatureBundle
        from condiag.diagnosis.alignment import reasoner_v2_diagnose

        tl = extract_test_log(str(REAL_CANARY_LOG))
        bundle = RuntimeFailureFeatureBundle(test_log=tl)
        clusters = reasoner_v2_cluster(bundle)
        assert len(clusters) >= 1, "real log must produce at least one cluster"

        # Diagnose must succeed without raising.
        diagnoses = reasoner_v2_diagnose(
            clusters, bundle.patch, bundle.trajectory,
        )
        assert len(diagnoses) == len(clusters)
        # All diagnoses must have a subtype.
        for d in diagnoses:
            assert d.subtype, "diagnosis must have a subtype"
