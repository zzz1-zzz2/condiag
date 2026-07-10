"""Base parser registry and dispatch for FailureWitness v2."""
from __future__ import annotations

import re
import typing as t
from abc import ABC, abstractmethod
from pathlib import Path

from condiag.schemas import FailureWitness

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_PARSERS: dict[str, type["FailureParser"]] = {}


def register_parser(name: str):
    """Decorator: register a parser class."""
    def _wrap(cls: type[FailureParser]) -> type[FailureParser]:
        _PARSERS[name] = cls
        cls._parser_name = name
        return cls
    return _wrap


def list_parsers() -> dict[str, type["FailureParser"]]:
    return dict(_PARSERS)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class FailureParser(ABC):
    """Base class for a test-framework-specific parser.

    Subclasses must set:
      framework  — label used in FailureWitness.test_framework
      priority   — higher = tried earlier within a failure stage
    """
    _parser_name: str = ""
    framework: str = "unknown"
    priority: int = 0

    @classmethod
    @abstractmethod
    def can_parse(cls, log_text: str) -> bool:
        """Return True if this parser understands *log_text*."""

    @classmethod
    @abstractmethod
    def parse(cls, instance_id: str, log_text: str,
              raw_log_path: str = "") -> FailureWitness:
        """Return a fully populated FailureWitness."""


# ---------------------------------------------------------------------------
# Log normalisation (ANSI strip, line endings)
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
_REMOTE_LINE_PREFIX = re.compile(r"^\[?[a-zA-Z]+\s+\d+\s+\S+\]?\s+", re.M)


def normalize_log(log_text: str, strip_prefixes: bool = False) -> str:
    """Strip ANSI escape codes and normalise line endings."""
    text = _ANSI_RE.sub("", log_text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if strip_prefixes:
        text = _REMOTE_LINE_PREFIX.sub("", text)
    return text


# ---------------------------------------------------------------------------
# Stage detection
# ---------------------------------------------------------------------------

STAGE_PATTERNS: list[tuple[str, str, list[re.Pattern]]] = [
    ("patch_apply_failure", "git_apply", [
        re.compile(r"patch\s+does\s+not\s+apply", re.I),
        re.compile(r"error:\s+patch\s+failed", re.I),
        re.compile(r"apply_error", re.I),
    ]),
    ("dependency_or_environment_failure", "build_or_infra_failure", [
        re.compile(r"ModuleNotFoundError", re.I),
        re.compile(r"ImportError", re.I),
        re.compile(r"cannot\s+find\s+command", re.I),
        re.compile(r"command\s+not\s+found", re.I),
        re.compile(r"bash:.*syntax error", re.I),
        re.compile(r"Build failed with an exception", re.I),
        re.compile(r"BUILD FAILED", re.I),
        re.compile(r"make.*Error \d+", re.I),
        re.compile(r"\[build failed\]", re.I),
    ]),
    ("test_collection_failure", "collect_error", [
        re.compile(r"ERROR:\s+collection", re.I),
        re.compile(r"INTERNALERROR>", re.I),
        re.compile(r"could\s+not\s+collect", re.I),
    ]),
    ("timeout", "timeout", [
        re.compile(r"^timeout", re.I),
        re.compile(r"timeout\s+occurred", re.I),
        re.compile(r"timed\s+out", re.I),
    ]),
    ("validation_failure", "test_failure", [
        re.compile(r"FAILED", re.I),
        re.compile(r"Error", re.I),
        re.compile(r"traceback", re.I),
        re.compile(r"assert", re.I),
    ]),
]


def detect_failure_stage(log_text: str) -> tuple[str, str]:
    """Return (failure_stage, failure_type) based on heuristics."""
    for stage, ftype, patterns in STAGE_PATTERNS:
        for p in patterns:
            if p.search(log_text):
                return (stage, ftype)
    return ("unknown_failure", "unknown")


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------

FRAMEWORK_PATTERNS: list[tuple[str, list[re.Pattern], int]] = [
    ("pytest", [
        re.compile(r"^=+\s*FAILURES\s*=", re.M),
        re.compile(r"FAILED\s+\S+\.py::", re.M),
        re.compile(r"\[FAIL\]", re.M),
        re.compile(r"Traceback.*AssertionError", re.M | re.DOTALL),
    ], 100),
    ("unittest", [
        re.compile(r"^FAIL:\s+", re.M),
        re.compile(r"^ERROR:\s+", re.M),
    ], 90),
    ("go_test", [
        re.compile(r"---\s+FAIL:", re.M),
        re.compile(r"FAIL\s+\S+\s+\[build\s+failed\]", re.M),
    ], 80),
    ("cargo_test", [
        re.compile(r"test result: FAILED", re.M),
        re.compile(r"error\[E\d+\]", re.M),
        re.compile(r"error: test failed", re.I),
    ], 70),
    ("junit_gradle", [
        re.compile(r"FAILURE:\s+", re.M),
        re.compile(r"Gradle\s+Test\s+Run", re.I),
    ], 60),
    ("mocha_jest", [
        re.compile(r"\d+\s+failing", re.M),
        re.compile(r"\d+\s+passing", re.M),
        re.compile(r"AssertionError", re.M),
        re.compile(r'"stats"\s*:\s*\{[^}]*"failures"\s*:\s*\d+', re.DOTALL),
    ], 50),
    ("cpp_test", [
        re.compile(r"FAILED\s+TEST", re.M),
        re.compile(r"FAIL:\s+", re.M),
    ], 40),
    ("ansible_custom", [
        re.compile(r"fatal:", re.M),
        re.compile(r"FAILED!", re.M),
    ], 30),
    ("generic", [], 0),
]


def detect_test_framework(log_text: str) -> str:
    """Return detected test framework label."""
    for framework, patterns, _priority in FRAMEWORK_PATTERNS:
        if framework == "generic":
            continue
        for p in patterns:
            if p.search(log_text):
                return framework
    return "generic"


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def build_failure_witness_from_log(
    instance_id: str,
    log_text: str,
    raw_log_path: str = "",
    parser_override: str | None = None,
) -> FailureWitness:
    """Build FailureWitness by stage -> framework dispatch.

    1. Detect failure_stage.
    2. If NOT validation_failure -> return non-validation witness early.
    3. Detect test_framework.
    4. Dispatch to registered parser (highest priority first).
    5. Fallback -> GenericParser.
    6. Normalise (quality/eligible_for_condiag/provenance).
    """
    log_text = normalize_log(log_text)
    stage, ftype = detect_failure_stage(log_text)

    # early return for non-validation stages
    if stage != "validation_failure":
        witness = _build_non_validation_witness(
            instance_id, stage, ftype, log_text, raw_log_path,
        )
        return _normalise_witness(witness, "stage_detector", "v2.0", stage, log_text)

    # validate content
    if len(log_text.strip()) < 20:
        witness = _build_non_validation_witness(
            instance_id, "unknown_failure", "empty_log", log_text, raw_log_path,
        )
        return _normalise_witness(witness, "stage_detector", "v2.0", stage, log_text)

    # framework detection + parser dispatch
    framework = detect_test_framework(log_text)

    parser = _select_parser(framework, log_text, parser_override)
    if parser is not None:
        try:
            witness = parser.parse(instance_id, log_text, raw_log_path)
            witness.test_framework = framework
            return _normalise_witness(witness, parser._parser_name,
                                      "v2.0", stage, log_text)
        except Exception:
            pass  # fall through to generic

    # generic fallback
    if framework == "generic" and _has_any_failure_signal(log_text):
        from .generic_parser import GenericParser
        witness = GenericParser.parse(instance_id, log_text, raw_log_path)
        return _normalise_witness(witness, "generic_parser", "v2.0", stage, log_text)

    # truly unparseable
    witness = _build_non_validation_witness(
        instance_id, stage, "unparseable", log_text, raw_log_path,
    )
    witness.failure_stage = stage
    witness.failure_type = ftype
    return _normalise_witness(witness, "stage_detector", "v2.0", stage, log_text)


def _select_parser(
    framework: str,
    log_text: str,
    override: str | None = None,
) -> type[FailureParser] | None:
    """Select best parser, trying exact framework match then priority order."""
    candidates: list[type[FailureParser]] = []
    for pname, pcls in _PARSERS.items():
        if override and pname != override:
            continue
        if pcls.framework == framework or pcls.can_parse(log_text):
            candidates.append(pcls)
    if not candidates:
        return None
    candidates.sort(key=lambda c: c.priority, reverse=True)
    return candidates[0]


def _has_any_failure_signal(log_text: str) -> bool:
    """Minimal check: does the log suggest something failed?"""
    signals = [
        r"fail", r"error", r"traceback", r"assert", r"exception",
        r"exit code", r"non-zero", r"FAILED", r"FAIL",
    ]
    return any(re.search(s, log_text, re.I) for s in signals)


# ---------------------------------------------------------------------------
# Non-validation witness
# ---------------------------------------------------------------------------


def _build_non_validation_witness(
    instance_id: str,
    stage: str,
    ftype: str,
    log_text: str,
    raw_log_path: str = "",
) -> FailureWitness:
    """Build minimal witness for patch-apply / dependency / timeout stages."""
    lines = log_text.strip().splitlines()
    context = "\n".join(lines[:20] + ["..."] + lines[-10:]) if len(lines) > 30 else log_text

    return FailureWitness(
        instance_id=instance_id,
        has_failure_witness=False,
        failure_observed=True,
        failure_stage=stage,
        failure_type=ftype,
        test_framework=detect_test_framework(log_text),
        error_message=context[:2000],
        mode="diagnostic_only_no_failure_witness",
        source="post_validation_output",
        source_type="infrastructure_failure",
        raw_output_path=raw_log_path,
        version="v2.0",
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalise_witness(
    witness: FailureWitness,
    parser_name: str,
    parser_version: str,
    failure_stage: str,
    log_text: str,
) -> FailureWitness:
    """Fill provenance / quality / eligible_for_condiag after parsing."""
    witness.parser_name = parser_name
    witness.parser_version = parser_version

    if witness.failure_stage == "unknown_failure":
        witness.failure_stage = failure_stage

    # Derive eligible_for_condiag
    if witness.failure_stage in (
        "patch_apply_failure",
        "dependency_or_environment_failure",
        "test_collection_failure",
        "timeout",
    ):
        witness.eligible_for_condiag = False
    elif witness.failure_observed and witness.has_failure_witness:
        witness.eligible_for_condiag = True

    # Quality rating
    witness.quality = _rate_quality(witness)

    # Matched patterns
    if not witness.matched_patterns:
        witness.matched_patterns = _extract_matched_patterns(log_text)

    return witness


QUALITY_SIGNALS: dict[str, list[re.Pattern]] = {
    "high": [
        re.compile(r"FAILED\s+\S+\.py::", re.M),
        re.compile(r"AssertionError:\s*.+!=\s*.+", re.M),
    ],
    "medium": [
        re.compile(r"Error", re.I),
        re.compile(r"Traceback", re.I),
    ],
    "low": [
        re.compile(r"FAILED", re.I),
        re.compile(r"fail", re.I),
    ],
}


def _rate_quality(witness: FailureWitness) -> str:
    """Rate witness quality based on available fields."""
    if not witness.failure_observed:
        return "none"
    if witness.failure_stage != "validation_failure":
        return "infrastructure_only"
    has_assert = bool(witness.expected and witness.actual)
    has_traceback = bool(witness.stack_trace)
    has_failed_tests = bool(witness.failed_tests)
    if has_assert and has_traceback and has_failed_tests:
        return "high"
    if has_traceback or has_failed_tests:
        return "medium"
    if witness.has_failure_witness:
        return "low"
    return "minimal"


def _extract_matched_patterns(log_text: str) -> list[str]:
    """Return list of pattern names matched in log_text."""
    matched: list[str] = []
    for stage, ftype, patterns in STAGE_PATTERNS:
        for p in patterns:
            if p.search(log_text):
                matched.append(f"{stage}/{ftype}")
                break
    for framework, patterns, _priority in FRAMEWORK_PATTERNS:
        if framework == "generic":
            continue
        for p in patterns:
            if p.search(log_text):
                matched.append(f"framework/{framework}")
                break
    return matched
