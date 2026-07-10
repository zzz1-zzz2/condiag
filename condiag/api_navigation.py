"""Task 4 — API Navigation hint generator.

Rule-based generator that turns a FailureWitness + repo-visible context into an
ApiNavigationHint. No LLM calls, no packet, no retry.

Allowed hint_source values (Rule 5):
    public_api_signature
    repo_source_signature
    issue_keyword_api_match
    runtime_introspection

Forbidden sources (Rule 1 / Rule 5):
    gold_patch, feedback_success_patch, manual_hindsight_only,
    contextbench_oracle, gold_context, resolved_label

Generator priority order (first legal hit wins):
    1. runtime_introspection    — from witness.top_repo_frames (file:line:func)
    2. repo_source_signature    — read repo file at frame location, extract sig
    3. issue_keyword_api_match  — keywords from failed_tests / error_message
    4. public_api_signature     — match well-known public API names in error text
    5. no_legal_api_hint        — has_api_hint=false, missing_reason recorded

For django__django-16454 (needs_regression_witness=true), the generator is
bypassed and a forced no-hint record is emitted.
"""
import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from condiag.path_utils import is_test_file, strip_testbed
from condiag.schemas import validate_api_hint_source  # noqa: F401  (re-exported)
from condiag.schemas import ApiNavigationHint  # noqa: F401

# Version fields.
API_NAVIGATION_VERSION = "v1"
METHOD_VERSION = "v1"
FAILURE_WITNESS_VERSION = "v1"
PLAN_VERSION = "plan_v1.0_post_validation"

# Forced-skip instance (django-16454): no F2P failure witness, needs P2P
# regression witness (Task 3E) before any API navigation.
REGRESSION_ONLY_INSTANCE = "django__django-16454"
REGRESSION_ONLY_MODE = "needs_regression_witness"
REGRESSION_ONLY_REASON = "f2p_passed_no_p2p_raw_output"

# Confidence bands (rule-based; not calibrated).
CONF_HIGH = 0.85   # runtime_introspection with clear repo frame
CONF_MED = 0.65    # repo_source_signature / issue_keyword_api_match
CONF_LOW = 0.45    # public_api_signature (generic)


# ---------------------------------------------------------------------------
# Hint result dataclass (serialised to JSON per instance)
# ---------------------------------------------------------------------------


@dataclass
class ApiHintResult:
    instance_id: str
    has_api_hint: bool
    hint_text: str
    hint_source: str          # "" when has_api_hint=False
    supporting_artifact: str  # "" when has_api_hint=False
    target_symbol: str
    confidence: float
    generation_method: str
    mode: str                 # "hint_generated" / "no_legal_api_hint" / "needs_regression_witness"
    missing_reason: str
    # Version fields (Rule 6).
    api_navigation_version: str = API_NAVIGATION_VERSION
    method_version: str = METHOD_VERSION
    failure_witness_version: str = FAILURE_WITNESS_VERSION
    plan_version: str = PLAN_VERSION


# ---------------------------------------------------------------------------
# Path translation (Windows host reading WSL repo paths)
# ---------------------------------------------------------------------------


def _translate_wsl_path(path_str: str) -> Optional[Path]:
    s = (path_str or "").strip()
    if not s:
        return None
    if s.startswith("/mnt/"):
        drive = s[len("/mnt/"):len("/mnt/") + 1].upper()
        rest = s[len("/mnt/") + 2:]
        return Path(f"{drive}:/{rest}")
    return Path(s)


# ---------------------------------------------------------------------------
# Source 1: runtime_introspection (top_repo_frames)
# ---------------------------------------------------------------------------


def _hint_from_runtime_introspection(witness: dict) -> Optional[dict]:
    """Use witness.stack_trace (full) as the strongest signal, picking the
    DEEPEST non-test repo frame as the failure origin.

    Originally this used top_repo_frames, but the witness builder curates
    that field by taking the TOP N repo frames (entry-point side), which
    misses the actual failure origin for cases like django-12125 where
    base.py:113 __new__ is the RuntimeError source but only the management
    chain made it into top_repo_frames. Scanning the full stack_trace from
    the bottom (deepest = failure origin) backwards fixes this.

    These frames come from the post-validation failure output — runtime
    introspection of the failure, not gold context.
    """
    # Prefer the full stack_trace (deepest = failure origin); fall back to
    # top_repo_frames if stack_trace is empty.
    frames = witness.get("stack_trace") or witness.get("top_repo_frames") or []
    if not frames:
        return None
    impl_frame = None
    # Iterate from the DEEPEST frame backwards so we pick the failure origin
    # (Python tracebacks list the raising frame last). Only non-test repo
    # frames qualify — a test-only traceback yields no runtime_introspection
    # hint, letting test_class_to_impl / issue_keyword_api_match fire instead.
    for fr in reversed(frames):
        if not isinstance(fr, dict):
            continue
        f = (fr.get("file") or "").strip()
        if not f:
            continue
        if not is_test_file(f):
            impl_frame = fr
            break
    chosen = impl_frame
    if not chosen:
        return None
    f = (chosen.get("file") or "").strip()
    line = chosen.get("line", "")
    func = (chosen.get("func") or "").strip()
    if not func:
        return None
    # Normalise target_symbol: strip "/testbed/" prefix, drop extension.
    rel = f
    rel = strip_testbed(rel)
    rel = rel.rsplit(".", 1)[0] if rel.endswith(".py") else rel
    target = f"{rel}::{func}" if rel else func

    hint_text = (
        f"Failure surfaced at {f}:{line} in {func}(). Inspect this function's "
        f"signature and call site; the post-validation traceback identifies it "
        f"as the runtime failure location."
    )
    return {
        "hint_text": hint_text,
        "hint_source": "runtime_introspection",
        "supporting_artifact": json.dumps(chosen, ensure_ascii=False),
        "target_symbol": target,
        "confidence": CONF_HIGH,
        "generation_method": "top_repo_frame_extraction",
    }


# ---------------------------------------------------------------------------
# Source 2: repo_source_signature (read repo file at frame)
# ---------------------------------------------------------------------------


def _extract_function_signature(source_lines: list, target_line: int) -> str:
    """Find the def/class line preceding target_line (1-indexed)."""
    if not source_lines:
        return ""
    idx = max(0, min(target_line - 1, len(source_lines) - 1))
    # Search backwards for a def/class line.
    for i in range(idx, max(-1, idx - 200), -1):
        if i >= len(source_lines):
            continue
        line = source_lines[i]
        m = re.match(r"^(class|def)\s+(\w+)", line)
        if m:
            return line.rstrip()
    return ""


def _hint_from_repo_source_signature(witness: dict,
                                     repo_base_path: str) -> Optional[dict]:
    """Read the repo file at the failure-origin frame and extract the
    enclosing function/class signature. Requires repo_base_path to be
    readable on this host (e.g. WSL when repo_base_path is a WSL path).

    Scans stack_trace from the deepest frame backwards to find the first
    repo implementation frame (same logic as runtime_introspection), then
    reads the repo source at that location to extract the enclosing
    def/class signature. This produces a signature-level hint, stronger
    than runtime_introspection's frame-only hint.
    """
    if not repo_base_path:
        return None
    frames = witness.get("stack_trace") or witness.get("top_repo_frames") or []
    if not frames:
        return None
    base = _translate_wsl_path(repo_base_path)
    if base is None or not base.is_dir():
        return None
    # Build ordered candidate list: deepest non-test repo frame first.
    candidates = []
    for fr in reversed(frames):
        if not isinstance(fr, dict):
            continue
        f = (fr.get("file") or "").strip()
        line = fr.get("line")
        if not f or not isinstance(line, int):
            continue
        if is_test_file(f):
            continue
        candidates.append(fr)
    for fr in candidates:
        f = (fr.get("file") or "").strip()
        line = fr.get("line")
        # Strip /testbed/ -> repo relative path.
        rel = f
        rel = strip_testbed(rel)
        candidate = base / rel
        if not candidate.is_file():
            continue
        try:
            with open(candidate, encoding="utf-8", errors="replace") as fh:
                src = fh.readlines()
        except OSError:
            continue
        sig = _extract_function_signature(src, line)
        if not sig:
            continue
        target = f"{rel}::{sig.split()[1].split('(')[0] if len(sig.split()) > 1 else ''}"
        return {
            "hint_text": (
                f"Repo source at {rel}:{line} is enclosed by: `{sig.strip()}`. "
                f"Use this signature as the navigation anchor."
            ),
            "hint_source": "repo_source_signature",
            "supporting_artifact": f"{candidate}:{line}",
            "target_symbol": target,
            "confidence": CONF_MED,
            "generation_method": "repo_file_signature_extraction",
        }
    return None


# ---------------------------------------------------------------------------
# Source 3: issue_keyword_api_match (failed_tests / error_message keywords)
# ---------------------------------------------------------------------------


# Well-known public API surfaces per repo. Matched against failed_tests paths
# and error_message text. This is public API knowledge, NOT gold context.
_PUBLIC_API_BY_MODULE_PREFIX = {
    "django/db/models": ["django.db.models.Model", "ModelBase", "ModelMeta"],
    "django/core/management": ["django.core.management"],
    "django/test/runner": ["django.test.runner.TestRunner"],
    "django/db/migrations": ["django.db.migrations"],
    "sympy/polys": ["sympy.polys.polytools"],
    "sympy/core": ["sympy.core"],
    "sympy/sets": ["sympy.sets"],
}


def _hint_from_issue_keyword_api_match(witness: dict) -> Optional[dict]:
    """Extract keywords from failed_tests and error_message; match against
    public module-path prefixes. Useful when no top_repo_frames available
    (e.g. pytest assertion failures with no Python traceback).
    """
    failed_tests = witness.get("failed_tests") or []
    err = (witness.get("error_message") or "").strip()
    # Build candidate keyword set from failed test paths.
    keywords = set()
    for t in failed_tests:
        if not isinstance(t, str):
            continue
        # e.g. "sympy/polys/tests/test_polytools.py::test_issue_20427"
        path_part = t.split("::", 1)[0]
        keywords.add(path_part)
        if "::" in t:
            keywords.add(t.split("::", 1)[1])

    # NOTE: module-prefix-only matches (e.g. "sympy/polys" ->
    # "sympy.polys.polytools") are intentionally NOT returned as hints.
    # A module-level target is too weak to guide the agent — per Task 4.3
    # rule, if no specific implementation symbol can be identified, we
    # emit no_legal_hint rather than a weak module-level hint.
    # The module-prefix map is retained for future use by test-source
    # exploration but not used as a standalone hint source.

    # Keyword scan on error_message for known patterns.
    err_lower = err.lower()
    if "app_label" in err_lower and "installed_apps" in err_lower:
        return {
            "hint_text": (
                "Error message mentions app_label / INSTALLED_APPS. Navigate "
                "to django.db.models.base.ModelBase.__new__ (app_label "
                "resolution) and django.conf.settings.INSTALLED_APPS."
            ),
            "hint_source": "issue_keyword_api_match",
            "supporting_artifact": "error_message_keyword_match:app_label,INSTALLED_APPS",
            "target_symbol": "django.db.models.base.ModelBase.__new__",
            "confidence": CONF_LOW,
            "generation_method": "error_message_keyword_match",
        }
    return None


# ---------------------------------------------------------------------------
# Source 3b: test-class -> impl-class resolution (issue_keyword_api_match)
# ---------------------------------------------------------------------------

# Pattern: "ClassNameTests.method_name" or "module.ClassNameTests.method_name"
_TEST_CLASS_RE = re.compile(r"\b([A-Z]\w*?)Tests\.([a-z]\w*)")


def _hint_from_test_class_to_impl(witness: dict,
                                  repo_base_path: str) -> Optional[dict]:
    """When the traceback only points at a test method (common for
    AssertionError-at-test cases where the impl is not in the traceback),
    parse the test class name from the error_message, strip the "Tests"
    suffix, and search the repo for `class <ImplName>`.

    This is issue_keyword_api_match: the keyword is the test class name
    extracted from the post-validation failure output. The repo search
    reads only repo-visible source, NOT the test patch (which is not in
    repo_base at base_commit).

    Requires repo_base_path to be readable (WSL host).
    """
    if not repo_base_path:
        return None
    err = witness.get("error_message") or ""
    stack = witness.get("stack_trace") or []
    # Also scan stack_trace func names for ClassNameTests.method patterns.
    scan_text = err
    for fr in stack:
        if isinstance(fr, dict):
            scan_text += "\n" + (fr.get("func") or "")

    m = _TEST_CLASS_RE.search(scan_text)
    if not m:
        return None
    test_class = m.group(1)
    test_method = m.group(2)
    impl_class = test_class  # ExceptionReporterTests -> ExceptionReporter

    base = _translate_wsl_path(repo_base_path)
    if base is None or not base.is_dir():
        return None

    # Search repo for `class <ImplName>` definition using Python os.walk
    # (cross-platform; no grep dependency). Limit to .py files.
    import os
    pattern = f"class {impl_class}"
    matches = []  # list of (abs_path, line_no, line)
    for root, dirs, files in os.walk(base):
        # Skip obvious non-source dirs.
        dirs[:] = [d for d in dirs if d not in (
            ".git", "__pycache__", ".mypy_cache", ".pytest_cache",
            "node_modules", ".venv", "venv")]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            fpath = os.path.join(root, fn)
            try:
                with open(fpath, encoding="utf-8", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if line.startswith(pattern):
                            # Ensure word boundary (class FooBar not class FooBarBaz).
                            rest = line[len(pattern):]
                            if rest and (rest[0].isalnum() or rest[0] == "_"):
                                continue
                            matches.append((fpath, i, line.rstrip()))
                            break
            except OSError:
                continue
        if len(matches) > 200:
            break
    if not matches:
        return None
    # Prefer the first match not under tests/.
    impl_match = None
    for m in matches:
        if "/tests/" not in m[0] and "/test_" not in m[0] and "\\tests\\" not in m[0]:
            impl_match = m
            break
    if impl_match is None:
        impl_match = matches[0]
    file_path, line_no, _ = impl_match
    # Make repo-relative.
    rel = file_path
    try:
        rel = str(Path(file_path).relative_to(base))
    except ValueError:
        pass
    # Try to find a method matching the test behaviour. For tests asserting
    # len(frames)==1 etc., look for get_* methods on this class.
    method_target = ""
    try:
        with open(file_path, encoding="utf-8", errors="replace") as fh:
            src = fh.readlines()
    except OSError:
        src = []
    # Find methods defined inside impl_class after line_no.
    if src:
        for i in range(line_no, min(len(src), line_no + 200)):
            line = src[i]
            # Stop at next top-level class.
            if re.match(r"^class\s+\w+", line) and i > line_no:
                break
            m2 = re.match(r"^\s+def\s+(\w+)", line)
            if m2:
                mname = m2.group(1)
                # Prefer methods related to the test's assertion.
                if any(kw in mname.lower()
                       for kw in ("frame", "traceback", "html", "text")):
                    method_target = mname
                    break
    target = (f"{rel}::{impl_class}"
              + (f".{method_target}" if method_target else ""))
    hint_text = (
        f"Failed test `{test_class}Tests.{test_method}` exercises "
        f"`{impl_class}` (defined at {rel}:{line_no}). "
        + (f"The test's assertion likely targets `{method_target}`."
           if method_target
           else "Inspect this class's methods.")
        + " Navigate to this implementation symbol."
    )
    return {
        "hint_text": hint_text,
        "hint_source": "issue_keyword_api_match",
        "supporting_artifact": f"{file_path}:{line_no}",
        "target_symbol": target,
        "confidence": CONF_MED,
        "generation_method": "test_class_to_impl_class_resolution",
    }


# ---------------------------------------------------------------------------
# Source 4: public_api_signature (well-known API names)
# ---------------------------------------------------------------------------


def _hint_from_public_api_signature(witness: dict) -> Optional[dict]:
    """Last-resort: match well-known public API names in error_message."""
    err = (witness.get("error_message") or "")
    if not err:
        return None
    # Pattern: "RuntimeError: Model class ... doesn't declare an explicit app_label"
    if "Model class" in err and "app_label" in err:
        return {
            "hint_text": (
                "Public API hint: django.db.models.base defines ModelBase and "
                "the app_label resolution path. Inspect its public signature."
            ),
            "hint_source": "public_api_signature",
            "supporting_artifact": "public_api:django.db.models.base.ModelBase",
            "target_symbol": "django.db.models.base.ModelBase",
            "confidence": CONF_LOW,
            "generation_method": "public_api_name_match",
        }
    return None


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def generate_hint(witness: dict, instance_id: str,
                  repo_base_path: str = "",
                  repo_base_commit: str = "") -> ApiHintResult:
    """Generate an ApiHintResult for one instance.

    For REGRESSION_ONLY_INSTANCE the generator is bypassed.
    """
    if instance_id == REGRESSION_ONLY_INSTANCE:
        return ApiHintResult(
            instance_id=instance_id,
            has_api_hint=False,
            hint_text="",
            hint_source="",
            supporting_artifact="",
            target_symbol="",
            confidence=0.0,
            generation_method="forced_skip",
            mode=REGRESSION_ONLY_MODE,
            missing_reason=REGRESSION_ONLY_REASON,
        )

    has_witness = bool(witness.get("has_failure_witness", False))
    if not has_witness:
        return ApiHintResult(
            instance_id=instance_id,
            has_api_hint=False,
            hint_text="",
            hint_source="",
            supporting_artifact="",
            target_symbol="",
            confidence=0.0,
            generation_method="none",
            mode="no_legal_api_hint",
            missing_reason="no_failure_witness",
        )

    # Try each source in priority order. repo_source_signature is tried
    # before runtime_introspection because a signature-level hint is
    # stronger than a frame-only hint. When repo_base_path is not readable
    # on this host (e.g. Windows host with WSL repo paths), repo_source_signature
    # returns None and runtime_introspection fires as fallback.
    # _hint_from_test_class_to_impl handles AssertionError-at-test cases
    # where the impl is not in the traceback (parses test class name from
    # error_message and greps repo for the impl class).
    for gen in (
        lambda: _hint_from_repo_source_signature(witness, repo_base_path),
        lambda: _hint_from_runtime_introspection(witness),
        lambda: _hint_from_test_class_to_impl(witness, repo_base_path),
        lambda: _hint_from_issue_keyword_api_match(witness),
        lambda: _hint_from_public_api_signature(witness),
    ):
        try:
            candidate = gen()
        except Exception:
            candidate = None
        if candidate is None:
            continue
        # Validate source against Rule 5 allowlist.
        try:
            validate_api_hint_source(candidate["hint_source"])
        except ValueError:
            # Should never happen — internal generators only emit allowed
            # sources. Reject defensively if they do.
            continue
        return ApiHintResult(
            instance_id=instance_id,
            has_api_hint=True,
            hint_text=candidate["hint_text"],
            hint_source=candidate["hint_source"],
            supporting_artifact=candidate["supporting_artifact"],
            target_symbol=candidate["target_symbol"],
            confidence=candidate["confidence"],
            generation_method=candidate["generation_method"],
            mode="hint_generated",
            missing_reason="",
        )

    return ApiHintResult(
        instance_id=instance_id,
        has_api_hint=False,
        hint_text="",
        hint_source="",
        supporting_artifact="",
        target_symbol="",
        confidence=0.0,
        generation_method="none",
        mode="no_legal_api_hint",
        missing_reason="no_legal_hint_source_matched",
    )


def result_to_dict(result: ApiHintResult) -> dict:
    return asdict(result)
