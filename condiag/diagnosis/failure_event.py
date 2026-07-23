"""P1-3A: FailureEvent — per-test normalized failure + deterministic clustering.

Each Failed test from the harness produces one FailureEvent.
Events are grouped into FailureClusters via deterministic rules:

  1. exact error_type match
  2. message fingerprint (normalized text)
  3. shared top-of-stack frame
  4. call-chain overlap ratio
  5. parameterized test family recognition

Output feeds into EvidenceAlignment (P1-3B).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from condiag.diagnosis.signals.schema import (
    RuntimeFailureFeatureBundle,
    StackFrame,
)

# ── Regex patterns for normalizing messages ─────────────────────────

# "Coordinate frame ITRS got unexpected keywords: ['location']"
#   → "Coordinate frame X got unexpected keywords: ['Y']"
_RE_UNEXPECTED_KW = re.compile(
    r"(unexpected keywords?:\s*\[)[^\]]+(\])"
)

# "unsupported operand type(s) for -: 'Time' and 'float'"
#   → "unsupported operand type(s) for -: 'A' and 'B'"
_RE_OPERAND_TYPES = re.compile(
    r"(unsupported operand type\(s\) for[^:]+:)\s*'[^']*'\s*and\s*'[^']*'"
)

# "cannot import name 'X' from 'Y'"
#   → "cannot import name 'N' from 'P'"
_RE_IMPORT_NAME = re.compile(
    r"(cannot import name\s+')'[^']*(' from\s+')'[^']*(')"
)

# "module 'X' has no attribute 'Y'"
#   → "module 'N' has no attribute 'A'"
_RE_MODULE_ATTR = re.compile(
    r"(module\s+')'[^']*(' has no attribute\s+')'[^']*(')"
)

# number literals → <N>
_RE_NUMBER = re.compile(r"\b\d+\b")

# file paths → <path>
_RE_FILEPATH = re.compile(r"/[\w/.-]+(?:\.\w+)?")

# hex values → <hex>
_RE_HEX = re.compile(r"0x[0-9a-fA-F]+")

# line numbers in "file.py:123" → ":N"
_RE_LINENO = re.compile(r"(:)(\d+)\b")


def normalize_message(msg: str) -> str:
    """Collapse variable parts of error messages into placeholders."""
    msg = _RE_UNEXPECTED_KW.sub(r"\1'<KW>'\2]", msg)
    msg = _RE_OPERAND_TYPES.sub(r"\1 '<A>' and '<B>'", msg)
    msg = _RE_IMPORT_NAME.sub(r"\1'<NAME>'\2'<PACKAGE>'\3", msg)
    msg = _RE_MODULE_ATTR.sub(r"\1'<MOD>'\2'<ATTR>'\3", msg)
    msg = _RE_NUMBER.sub("<N>", msg)
    msg = _RE_FILEPATH.sub("<path>", msg)
    msg = _RE_HEX.sub("<hex>", msg)
    msg = _RE_LINENO.sub(r"\1<N>", msg)
    return msg.strip()


# ── Error class taxonomy ────────────────────────────────────────────

# Coarse error categories used for first-pass grouping.
# Each maps one-or-more Python exception types to a cluster key.
ERROR_CLASS: dict[str, str] = {
    "TypeError": "TYPE_ERROR",
    "AssertionError": "ASSERTION_ERROR",
    "AttributeError": "ATTRIBUTE_ERROR",
    "ImportError": "IMPORT_ERROR",
    "ModuleNotFoundError": "IMPORT_ERROR",
    "ValueError": "VALUE_ERROR",
    "KeyError": "KEY_ERROR",
    "IndexError": "INDEX_ERROR",
    "NameError": "NAME_ERROR",
    "TimeoutError": "TIMEOUT",
    "OSError": "OS_ERROR",
}


def classify_error_type(exc_type: str) -> str:
    return ERROR_CLASS.get(exc_type, "OTHER")


# ── Data structures ─────────────────────────────────────────────────


@dataclass
class FailureEvent:
    """Normalized representation of one failed test.

    Fields are designed for deterministic clustering:
      - test_name: full dotted name
      - base_test_name: test name without parameterization suffix
      - exception_type: raw Python exception (e.g. TypeError)
      - error_class: coarse group (TYPE_ERROR, ASSERTION_ERROR, …)
      - message: original error text
      - message_fingerprint: normalized/masked version (for dedup)
      - assertion_line: the source code line that failed
      - stack_frames: ALL frames for this failure
      - call_chain: first ≤N repo frames (the call path to the failure)
      - top_repo_frame: first repo frame in call_chain (for overlap)
      - is_parameterized: True if test name contains [param]
      - param_group: base_test_name for parameterized families
    """

    test_name: str = ""
    exception_type: str = ""
    error_class: str = ""
    message: str = ""
    message_fingerprint: str = ""
    assertion_line: str = ""
    stack_frames: list[StackFrame] = field(default_factory=list)
    call_chain: list[StackFrame] = field(default_factory=list)
    top_repo_frame: str = ""
    is_parameterized: bool = False
    param_group: str = ""


@dataclass
class FailureCluster:
    """A group of FailureEvents that share a common root cause.

    'root_cause' is the highest-confidence frame/function where
    the failures converge — typically the shared top of call_chain.
    """

    events: list[FailureEvent] = field(default_factory=list)
    cluster_id: str = ""
    # Deterministic cluster key (used for hashing/comparison)
    primary_error_class: str = ""
    shared_top_frame: str = ""
    message_fingerprint: str = ""
    param_group: str = ""
    # Evidence from the cluster
    count: int = 0
    error_types: dict[str, int] = field(default_factory=dict)
    exception_types_seen: list[str] = field(default_factory=list)
    test_names: list[str] = field(default_factory=list)
    call_chain_overlap: list[str] = field(default_factory=list)
    root_cause: str = ""


# ── Helpers ─────────────────────────────────────────────────────────


def _first_repo_frame(frames: list[StackFrame]) -> str:
    """Return the first repo frame path from a stack, or ''."""
    for f in frames:
        if f.is_repo_frame and not f.is_test_file:
            return f"{f.file}:{f.line}"
    if frames:
        return f"{frames[0].file}:{frames[0].line}"
    return ""


def _call_chain_file_list(frames: list[StackFrame], max_depth: int = 5) -> list[str]:
    """Return file:line strings for the first N repo frames."""
    result: list[str] = []
    for f in frames:
        if len(result) >= max_depth:
            break
        if f.is_repo_frame:
            result.append(f"{f.file}:{f.line}")
    return result


def _param_group(test_name: str) -> str:
    """Extract base test name, stripping [param] suffix."""
    idx = test_name.find("[")
    return test_name[:idx] if idx != -1 else test_name


# ── Build FailureEvents from bundle ─────────────────────────────────


def extract_failure_events(
    bundle: RuntimeFailureFeatureBundle,
) -> list[FailureEvent]:
    """Build one FailureEvent per failed test from the bundle."""
    tl = bundle.test_log
    events: list[FailureEvent] = []

    # Build index of call_chains by ordinal position
    # (index 0 → first failed test, etc.)
    chain_by_idx = list(tl.call_chains or [])

    for i, test_name in enumerate(tl.failed_tests):
        # Pick the matching call chain (if available), else empty
        chain = chain_by_idx[i] if i < len(chain_by_idx) else []
        all_frames = list(chain)  # chain is the frames for this failure
        # If chain is empty, fall back to top-level stack_frames
        if not all_frames:
            all_frames = list(tl.stack_frames or [])

        # First error message for this test (use global first if needed)
        msg_index = i if i < len(tl.error_messages) else 0
        msg = tl.error_messages[msg_index] if tl.error_messages else ""
        assertion = tl.failure_assertions[i] if i < len(tl.failure_assertions) else ""

        # Determine exception type per-event from the assertion
        # If assertion_line is present, it's likely an AssertionError
        has_assertion_text = bool(assertion and assertion.strip())
        if has_assertion_text and "TypeError" not in msg:
            exc_type = "AssertionError"
        else:
            # Pick top error type from the shared error_types dict
            error_types_sorted = sorted(tl.error_types.items(), key=lambda x: -x[1])
            exc_type = error_types_sorted[0][0] if error_types_sorted else "Unknown"

        ev = FailureEvent(
            test_name=test_name,
            exception_type=exc_type,
            error_class=classify_error_type(exc_type),
            message=msg,
            message_fingerprint=normalize_message(msg),
            assertion_line=assertion,
            stack_frames=all_frames,
            call_chain=_call_chain_file_list(all_frames),
            top_repo_frame=_first_repo_frame(all_frames),
            is_parameterized="[" in test_name,
            param_group=_param_group(test_name),
        )
        events.append(ev)

    return events


# ── Deterministic clustering ────────────────────────────────────────


def cluster_failures(events: list[FailureEvent]) -> list[FailureCluster]:
    """Group FailureEvents by shared root cause.

    Priority order for grouping (probed in order; first match wins):

    1. Parameterized family: tests sharing the same param_group
       AND the same error_class go together.
    2. Message fingerprint match: identical normalized message.
    3. Shared top-repo frame: failure converges on the same file:line.
    4. Same error_class + same call_chain overlap (≥2 shared frames).
    5. Fallback: by error_class alone.
    """
    unassigned = list(events)
    clusters: list[FailureCluster] = []

    # Helper to pop matched events
    def _pop_matching(match_fn) -> list[FailureEvent]:
        matched = [e for e in unassigned if match_fn(e)]
        for e in matched:
            unassigned.remove(e)
        return matched

    # 1. Parameterized families
    param_groups: dict[str, list[FailureEvent]] = {}
    for e in unassigned:
        if e.is_parameterized:
            key = (e.param_group, e.error_class)
            param_groups.setdefault(str(key), []).append(e)
    for key, group in param_groups.items():
        for e in group:
            unassigned.remove(e)
        _finish_cluster(clusters, group)

    # 2. Message fingerprint (non-empty, non-trivial)
    fingerprint_groups: dict[str, list[FailureEvent]] = {}
    for e in unassigned:
        fp = e.message_fingerprint
        if fp and fp not in ("", "?"):
            fingerprint_groups.setdefault(fp, []).append(e)
    for fp, group in fingerprint_groups.items():
        if len(group) < 2:
            continue  # single-test clusters handled later
        for e in group:
            unassigned.remove(e)
        _finish_cluster(clusters, group)

    # 3. Shared top-repo frame
    frame_groups: dict[str, list[FailureEvent]] = {}
    for e in unassigned:
        f = e.top_repo_frame
        if f:
            frame_groups.setdefault(f, []).append(e)
    for f, group in frame_groups.items():
        if len(group) < 2:
            continue
        for e in group:
            unassigned.remove(e)
        _finish_cluster(clusters, group)

    # 4. Same error_class + call-chain overlap ≥ 2
    unused_here = list(unassigned)
    for e1 in unused_here:
        if e1 not in unassigned:
            continue
        group = [e1]
        unassigned.remove(e1)
        for e2 in list(unassigned):
            if e1.error_class == e2.error_class and e1.call_chain and e2.call_chain:
                overlap = len(set(e1.call_chain) & set(e2.call_chain))
                if overlap >= 2:
                    group.append(e2)
                    unassigned.remove(e2)
        _finish_cluster(clusters, group)

    # 5. Remaining unassigned → one cluster per error_class
    remaining_by_class: dict[str, list[FailureEvent]] = {}
    for e in unassigned:
        remaining_by_class.setdefault(e.error_class, []).append(e)
    for ec, group in remaining_by_class.items():
        for e in group:
            unassigned.remove(e)
        _finish_cluster(clusters, group)

    return clusters


def _finish_cluster(
    clusters: list[FailureCluster],
    events: list[FailureEvent],
) -> None:
    """Create a FailureCluster from events and append to clusters."""
    if not events:
        return

    error_types: dict[str, int] = {}
    call_chains_seen: set[str] = set()
    call_chain_common: list[str] = []

    for e in events:
        error_types[e.exception_type] = error_types.get(e.exception_type, 0) + 1
        for frame_key in e.call_chain:
            call_chains_seen.add(frame_key)

    # Shared frames: find frames present in ALL events' call chains
    if len(events) > 1:
        chain_sets = [set(e.call_chain) for e in events]
        common = chain_sets[0]
        for cs in chain_sets[1:]:
            common &= cs
        call_chain_common = sorted(common, key=lambda x: list(chain_sets[0]).index(x) if x in chain_sets[0] else 99)

    first = events[0]
    cluster = FailureCluster(
        events=events,
        cluster_id=_cluster_id(first, events),
        primary_error_class=first.error_class,
        shared_top_frame=first.top_repo_frame or "",
        message_fingerprint=first.message_fingerprint,
        param_group=first.param_group,
        count=len(events),
        error_types=error_types,
        exception_types_seen=sorted(error_types.keys()),
        test_names=[e.test_name for e in events],
        call_chain_overlap=call_chain_common,
        root_cause=call_chain_common[0] if call_chain_common else first.top_repo_frame,
    )
    clusters.append(cluster)


def _cluster_id(first: FailureEvent, events: list[FailureEvent]) -> str:
    """Deterministic short ID for this cluster."""
    import hashlib
    raw = first.error_class + "|" + first.message_fingerprint + "|" + str(len(events))
    return "C" + hashlib.sha256(raw.encode()).hexdigest()[:7]


# ── API ─────────────────────────────────────────────────────────────


def reasoner_v2_cluster(
    bundle: RuntimeFailureFeatureBundle,
) -> list[FailureCluster]:
    """Full P1-3A pipeline: extract events → cluster → return clusters."""
    events = extract_failure_events(bundle)
    if not events:
        return []
    return cluster_failures(events)
