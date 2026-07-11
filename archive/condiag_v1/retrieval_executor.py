"""ConDiag retrieval executor — v0 operations.

Operations implemented in this round (per user directive for sympy-16597):
  - FIND_FAILED_TEST
  - REHYDRATE_SEEN_EVIDENCE
  - FIND_SYMBOL_DEFINITION
  - READ_DEPENDENCY_NEIGHBORHOOD
  - FIND_NEIGHBOR_TESTS  (optional helper)

Each operation returns a list of EvidenceCandidate dicts.

Evidence candidate schema (matches selected_evidence.json item shape):
  {
    "id": "E1",
    "operation": "FIND_FAILED_TEST",
    "relation": "visible_regression_test",
    "path": "...",
    "start_line": int,
    "end_line": int,
    "symbol": "...",                  # optional
    "already_seen": bool,             # True if same span appears in viewed_spans
    "score": float,                   # 0..1
    "why": "..."
  }
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import repository_index as ri
from .schemas import ManualDiagnosis, RuntimeSignals


# ============================================================================
# Evidence candidate + action result dataclasses
# ============================================================================

@dataclass
class EvidenceCandidate:
    id: str
    operation: str
    relation: str
    path: str
    start_line: int
    end_line: int
    symbol: Optional[str] = None
    already_seen: bool = False
    score: float = 0.0
    why: str = ""
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "operation": self.operation,
            "relation": self.relation,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "symbol": self.symbol,
            "already_seen": self.already_seen,
            "score": self.score,
            "why": self.why,
            "extra": self.extra,
        }


@dataclass
class ActionResult:
    operation: str
    target: str
    budget: int
    status: str               # done | skipped | no_candidates
    candidates: List[EvidenceCandidate] = field(default_factory=list)
    skipped_reason: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "operation": self.operation,
            "target": self.target,
            "budget": self.budget,
            "status": self.status,
            "candidate_count": len(self.candidates),
            "candidates": [c.to_dict() for c in self.candidates],
            "skipped_reason": self.skipped_reason,
            "notes": self.notes,
        }


# ============================================================================
# Helpers
# ============================================================================

def _normalize_viewed_spans(viewed_spans: dict) -> List[tuple]:
    """Return list of (path, start, end) tuples from viewed_spans dict.

    viewed_spans schema in runtime_signals.json:
        {"<path>": [[start, end], ...], ...}
    """
    out: list[tuple] = []
    if not isinstance(viewed_spans, dict):
        return out
    for path, spans in viewed_spans.items():
        if not isinstance(spans, list):
            continue
        for span in spans:
            if isinstance(span, (list, tuple)) and len(span) >= 2:
                try:
                    out.append((str(path), int(span[0]), int(span[1])))
                except (TypeError, ValueError):
                    continue
    return out


def _parse_lines_field(ln) -> set:
    """Parse final_patch_context_files 'lines' field into a set of ints.

    Accepts "X-Y", "X", [X,Y], [X], or empty. Returns empty set on unparseable.
    """
    if ln is None or ln == "":
        return set()
    if isinstance(ln, (list, tuple)):
        try:
            if len(ln) >= 2:
                s, e = int(ln[0]), int(ln[1])
                return set(range(s, e + 1)) if e >= s else {s}
            if len(ln) == 1:
                return {int(ln[0])}
        except (ValueError, TypeError):
            return set()
        return set()
    s = str(ln).strip()
    if not s:
        return set()
    if "-" in s:
        try:
            a, b = s.split("-", 1)
            return set(range(int(a), int(b) + 1))
        except ValueError:
            return set()
    try:
        return {int(s)}
    except ValueError:
        return set()


def _span_seen(viewed: List[tuple], path: str, start: int, end: int, tolerance: int = 5) -> bool:
    """Check if [start, end] in `path` overlaps any viewed span (with tolerance)."""
    for v_path, v_start, v_end in viewed:
        if v_path != path and not v_path.endswith(path) and not path.endswith(v_path):
            # tolerate /testbed/ prefix differences
            continue
        if max(start, v_start) - tolerance <= min(end, v_end) + tolerance:
            return True
    return False


def _next_id(prefix: str, counter: list) -> str:
    counter[0] += 1
    return f"{prefix}{counter[0]}"


# ============================================================================
# Operations
# ============================================================================

def find_failed_test(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """Locate regression tests visible in the agent's local test output."""
    result = ActionResult(
        operation="FIND_FAILED_TEST",
        target="visible_regressions + visible_target_fixes",
        budget=3,
        status="done",
    )

    # Source test names from trigger_assessment first; fallback to runtime_signals.test_failures
    trigger = manual_diagnosis.trigger_assessment or {}
    names = []
    for key in ("visible_regressions", "visible_target_fixes"):
        names.extend(trigger.get(key, []) or [])
    if not names and runtime_signals.test_failures:
        # test_failures are "path::test_name"; extract bare name
        for tf in runtime_signals.test_failures:
            if "::" in tf:
                names.append(tf.split("::", 1)[1])
            else:
                names.append(tf)

    if not names:
        result.status = "skipped"
        result.skipped_reason = "no visible_regressions or visible_target_fixes in manual_diagnosis; no test_failures in runtime_signals"
        return result

    seen_names = set()
    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)
    for name in names:
        if name in seen_names:
            continue
        seen_names.add(name)
        hits = ri.find_tests(idx, name)
        if not hits:
            # Try without test_ prefix / split issue number
            stripped = re.sub(r"^test_", "", name)
            hits = ri.find_tests(idx, stripped)
        if not hits:
            result.notes.append(f"no test function found for name='{name}'")
            continue
        # Take first hit only (avoid dup-by-substring explosion)
        t = hits[0]
        already = _span_seen(viewed, t.path, t.start_line, t.end_line)
        # Score: regressions higher than target fixes
        is_regression = name in (trigger.get("visible_regressions") or [])
        score = 0.95 if is_regression else 0.78
        rel = "visible_regression_test" if is_regression else "target_fix_test"
        why = ("Regression test failed after the attempted fix." if is_regression
               else "Target test that the previous attempt was trying to fix.")
        result.candidates.append(EvidenceCandidate(
            id=_next_id("E", id_counter),
            operation="FIND_FAILED_TEST",
            relation=rel,
            path=t.path,
            start_line=t.start_line,
            end_line=t.end_line,
            symbol=t.name,
            already_seen=already,
            score=score,
            why=why,
            extra={"is_regression": is_regression},
        ))
    if not result.candidates:
        result.status = "no_candidates"
    return result


def rehydrate_seen_evidence(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """Pick viewed spans relevant to target hints / regression tests but
    missing from final PATCH_CONTEXT.

    These are evidence the agent already saw but didn't carry into the final
    patch context — reactivating them is REHYDRATE's job.
    """
    result = ActionResult(
        operation="REHYDRATE_SEEN_EVIDENCE",
        target="viewed_spans ∩ target_hints ∩ NOT(final_patch_context)",
        budget=4,
        status="done",
    )

    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)
    if not viewed:
        result.status = "skipped"
        result.skipped_reason = "no viewed_spans in runtime_signals"
        return result

    # Final patch context as {path: set(lines)} — preserves the lines field
    # so we can do span-level containment checks (NOT file-level).
    final_spans: dict[str, set] = {}
    for entry in (runtime_signals.final_patch_context_files or []):
        if isinstance(entry, dict):
            p = (entry.get("file") or "").replace("/testbed/", "").lstrip("/")
            ln = entry.get("lines", "")
        else:
            p = str(entry).replace("/testbed/", "").lstrip("/")
            ln = ""
        if not p:
            continue
        parsed = _parse_lines_field(ln)
        final_spans.setdefault(p, set()).update(parsed)

    # Target keywords from manual_diagnosis.target_hints
    keywords: list[str] = []
    for h in manual_diagnosis.target_hints or []:
        if isinstance(h, dict):
            v = h.get("value", "")
            if v:
                keywords.append(v)
        else:
            keywords.append(str(h))
    # Also include visible regression test names
    for k in ("visible_regressions", "visible_target_fixes"):
        keywords.extend((manual_diagnosis.trigger_assessment or {}).get(k, []) or [])

    if not keywords:
        result.status = "skipped"
        result.skipped_reason = "no target_hints or visible regressions to match against"
        return result

    # Score each viewed span by keyword overlap on path AND span content.
    # Span-level containment check: only skip spans the agent already kept in
    # PATCH_CONTEXT. Same-file-different-span is the dropped-evidence case and
    # gets the highest priority (that is what REHYDRATE is designed to surface).
    scored = []
    for path, start, end in viewed:
        norm_path = path.replace("/testbed/", "").lstrip("/")
        span_lines = set(range(start, end + 1))
        final_lines_for_file = final_spans.get(norm_path)
        if final_lines_for_file is None:
            in_final_file = False
            span_covered = False
        elif not final_lines_for_file:
            # File in final but no line info — conservatively assume covered
            in_final_file = True
            span_covered = True
        else:
            in_final_file = True
            span_covered = span_lines.issubset(final_lines_for_file)

        # Skip spans the agent already kept in PATCH_CONTEXT
        if span_covered:
            continue

        try:
            span_content = ri.read_span(Path(idx.repo_root), norm_path, start, end).lower()
        except Exception:
            span_content = ""
        path_blob = norm_path.lower()
        match_count = 0
        matched = []
        for kw in keywords:
            kwl = kw.lower()
            last = kwl.rsplit(".", 1)[-1]
            if not last or len(last) < 4:
                continue
            if last in path_blob or last in span_content:
                match_count += 1
                matched.append(kw)
        if match_count == 0:
            continue

        # Dropped spans (file in final, span NOT) get the highest baseline —
        # this is the exact case REHYDRATE is designed to recover.
        if in_final_file:
            score = 0.85 + min(match_count * 0.05, 0.10)
        else:
            score = 0.65 + min(match_count * 0.08, 0.20)
        scored.append((match_count, score, path, norm_path, start, end, matched,
                       in_final_file, span_covered))

    # Sort: dropped (in_final_file=True, span_covered=False) first,
    # then by match_count, then by score.
    scored.sort(key=lambda x: (-int(x[7] and not x[8]), -x[0], -x[1]))
    for mc, score, raw_path, norm_path, start, end, matched, in_final, _covered in scored[:result.budget]:
        relation = "previously_seen_but_dropped" if in_final else "previously_seen_uncovered_file"
        result.candidates.append(EvidenceCandidate(
            id=_next_id("E", id_counter),
            operation="REHYDRATE_SEEN_EVIDENCE",
            relation=relation,
            path=norm_path,
            start_line=start,
            end_line=end,
            symbol=None,
            already_seen=True,    # by definition
            score=round(score, 3),
            why=(
                f"Attempt 1 viewed this span but it is absent from (or only partially present in) "
                f"final PATCH_CONTEXT. Matches target keywords: {matched[:3]}."
            ),
            extra={
                "matched_keywords": matched,
                "raw_viewed_path": raw_path,
                "file_in_final_patch": in_final,
                "dropped_span": in_final,
            },
        ))

    if not result.candidates:
        result.status = "no_candidates"
        result.notes.append("all viewed spans either landed in final PATCH_CONTEXT or did not match target keywords")
    return result


def find_symbol_definition(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """Locate the target symbol's definition (or its enclosing class if the
    symbol was newly added by the attempted patch)."""
    result = ActionResult(
        operation="FIND_SYMBOL_DEFINITION",
        target="target_hints with kind=symbol",
        budget=2,
        status="done",
    )

    target_symbols: list[str] = []
    for h in manual_diagnosis.target_hints or []:
        if isinstance(h, dict) and h.get("kind") == "symbol":
            v = h.get("value", "")
            if v:
                target_symbols.append(v)

    if not target_symbols:
        result.status = "skipped"
        result.skipped_reason = "no symbol target_hints in manual_diagnosis"
        return result

    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)

    for sym_query in target_symbols:
        hits = ri.find_symbol(idx, sym_query)
        if hits:
            # Prefer exact name match; if multiple, pick the one in the most relevant path
            hits.sort(key=lambda s: (
                0 if s.name == sym_query else 1,   # exact wins
                -int(s.name.endswith(sym_query.rsplit(".", 1)[-1])),
            ))
            for s in hits[:2]:
                already = _span_seen(viewed, s.path, s.start_line, s.end_line)
                result.candidates.append(EvidenceCandidate(
                    id=_next_id("E", id_counter),
                    operation="FIND_SYMBOL_DEFINITION",
                    relation="target_symbol_definition",
                    path=s.path,
                    start_line=s.start_line,
                    end_line=s.end_line,
                    symbol=s.name,
                    already_seen=already,
                    score=0.92 if s.name == sym_query else 0.78,
                    why=f"Definition of {s.name} — anchors the previous patch's target site.",
                ))
        else:
            # Fallback: enclosing class. e.g. Symbol._eval_is_finite → Symbol
            if "." in sym_query:
                enclosing = sym_query.split(".", 1)[0]
                cls_hits = [s for s in idx.symbol_index if s.kind == "class" and s.name == enclosing]
                for s in cls_hits[:1]:
                    already = _span_seen(viewed, s.path, s.start_line, s.end_line)
                    result.candidates.append(EvidenceCandidate(
                        id=_next_id("E", id_counter),
                        operation="FIND_SYMBOL_DEFINITION",
                        relation="enclosing_class_definition",
                        path=s.path,
                        start_line=s.start_line,
                        end_line=s.end_line,
                        symbol=s.name,
                        already_seen=already,
                        score=0.70,
                        why=(
                            f"{sym_query} does not exist at base_commit — it is what the "
                            f"previous patch tried to add. Enclosing class {s.name} found "
                            f"so the agent can locate the correct insertion site."
                        ),
                        extra={"target_query": sym_query, "at_base_commit": False},
                    ))
            else:
                result.notes.append(f"no symbol definition found for '{sym_query}'")

    if not result.candidates:
        result.status = "no_candidates"
    return result


def read_dependency_neighborhood(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """For each target symbol, surface sibling implementations across other classes.

    This is critical for REGRESSION_AFTER_PARTIAL_FIX: agent added a method on
    Symbol but missed that other classes (Expr / Number / Pow) also override
    the same _eval_is_* method, leading to inconsistent assumptions.
    """
    result = ActionResult(
        operation="READ_DEPENDENCY_NEIGHBORHOOD",
        target="sibling _eval_is_* implementations + class-internal neighbors",
        budget=3,
        status="done",
    )

    # Pick the primary target symbol
    primary: Optional[str] = None
    for h in manual_diagnosis.target_hints or []:
        if isinstance(h, dict) and h.get("kind") == "symbol":
            v = h.get("value", "")
            if v and "." in v:
                primary = v
                break
    if not primary:
        result.status = "skipped"
        result.skipped_reason = "no dotted symbol target_hint (need e.g. Symbol._eval_is_finite)"
        return result

    # Extract the bare method name (last segment after the dot)
    method_name = primary.rsplit(".", 1)[-1]
    class_name = primary.split(".", 1)[0]

    # Find all symbols ending in .<method_name>
    siblings = [s for s in idx.symbol_index if s.name.endswith("." + method_name)]
    siblings.sort(key=lambda s: (
        0 if s.parent == class_name else 1,    # siblings in same class first
        s.path,                                # then by path
    ))

    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)

    # Emit candidates (skip the primary itself, since FIND_SYMBOL_DEFINITION handles it)
    for s in siblings:
        if s.name == primary:
            continue
        already = _span_seen(viewed, s.path, s.start_line, s.end_line)
        # Score: same-named method in different class is high-signal
        result.candidates.append(EvidenceCandidate(
            id=_next_id("E", id_counter),
            operation="READ_DEPENDENCY_NEIGHBORHOOD",
            relation="sibling_method_implementation",
            path=s.path,
            start_line=s.start_line,
            end_line=s.end_line,
            symbol=s.name,
            already_seen=already,
            score=0.85 if s.parent != class_name else 0.70,
            why=(
                f"{s.name} is a sibling implementation of {method_name} in class "
                f"{s.parent}. Previous patch on {class_name} may interact with it."
            ),
            extra={"target_method": method_name, "target_class": class_name},
        ))
        if len(result.candidates) >= result.budget:
            break

    if not result.candidates:
        result.status = "no_candidates"
    return result


def find_neighbor_tests(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """Find neighbor tests by topic keyword (concept target_hints).

    For sympy-16597: concepts like 'is_integer / is_even / is_odd assumptions'
    → find tests in test_assumptions.py with matching keywords.
    """
    result = ActionResult(
        operation="FIND_NEIGHBOR_TESTS",
        target="concept target_hints → neighbor tests",
        budget=2,
        status="done",
    )

    concepts: list[str] = []
    for h in manual_diagnosis.target_hints or []:
        if isinstance(h, dict) and h.get("kind") == "concept":
            v = h.get("value", "")
            if v:
                concepts.append(v)
    if not concepts:
        result.status = "skipped"
        result.skipped_reason = "no concept target_hints in manual_diagnosis"
        return result

    # Tokenize concepts into keywords (drop stopwords like 'assumptions' / 'is_')
    stop = {"assumptions", "is_", "or", "and", "/", "the", "a"}
    tokens: set = set()
    for c in concepts:
        for tok in re.split(r"[\s/]+", c.lower()):
            tok = tok.strip()
            if len(tok) >= 3 and tok not in stop:
                tokens.add(tok)
    if not tokens:
        result.status = "skipped"
        result.skipped_reason = "no usable keywords after tokenizing concepts"
        return result

    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)
    scored = []
    for t in idx.test_index:
        # Score by counting token matches in the test name
        name_l = t.name.lower()
        mc = sum(1 for tok in tokens if tok in name_l)
        if mc > 0:
            scored.append((mc, t))
    scored.sort(key=lambda x: -x[0])

    seen_paths = set()
    for mc, t in scored:
        if len(result.candidates) >= result.budget:
            break
        key = (t.path, t.start_line)
        if key in seen_paths:
            continue
        seen_paths.add(key)
        already = _span_seen(viewed, t.path, t.start_line, t.end_line)
        result.candidates.append(EvidenceCandidate(
            id=_next_id("E", id_counter),
            operation="FIND_NEIGHBOR_TESTS",
            relation="neighbor_test_by_concept",
            path=t.path,
            start_line=t.start_line,
            end_line=t.end_line,
            symbol=t.name,
            already_seen=already,
            score=0.55 + min(mc * 0.1, 0.3),
            why=f"Neighbor test matching concept keywords ({mc} matches).",
            extra={"matched_token_count": mc},
        ))

    if not result.candidates:
        result.status = "no_candidates"
    return result


# ============================================================================
# REHYDRATE-flow operations (parallel implementations / imports / callers)
# ============================================================================

def _target_hints_of_kind(md: ManualDiagnosis, kinds: list[str]) -> list[str]:
    out = []
    for h in md.target_hints or []:
        if isinstance(h, dict) and h.get("kind") in kinds:
            v = h.get("value", "")
            if v:
                out.append(v)
    return out


def _file_basename_stem(path: str) -> str:
    """Return basename without .py extension: 'a/b/c.py' -> 'c'."""
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _camel_tokens(name: str) -> list[str]:
    """Split a CamelCase identifier into tokens >= 4 chars.

    'RelatedFieldListFilter' -> ['Related', 'Field', 'List', 'Filter']
    'RelatedOnlyFieldListFilter' -> ['Related', 'Only', 'Field', 'List', 'Filter']
    """
    return [t for t in re.findall(r"[A-Z][a-z]+|[A-Z]+(?=[A-Z]|$)", name) if len(t) >= 4]


def _sibling_classes_in_target_file(
    idx: ri.RepositoryIndex,
    target_class_names: list[str],
    viewed: List[tuple],
    id_counter: list,
) -> list[EvidenceCandidate]:
    """Find sibling classes that share a file with any target class.

    Sibling rules (any one strengthens the match):
      - Same file (path) as a target class definition
      - Similar name (shared camelCase token, e.g. 'FieldListFilter')
      - Shared prefix/suffix of length >= 4

    Returns candidates EXCLUDING the target classes themselves. The same-file
    signal alone is enough to surface a sibling (the agent may have edited
    only one of N sibling classes in the same file).
    """
    out: list[EvidenceCandidate] = []

    # Locate target class definitions in the index
    target_class_set = set(target_class_names)
    target_syms: list = []
    for sym in idx.symbol_index:
        if sym.kind != "class":
            continue
        short = sym.name.rsplit(".", 1)[-1]
        if short in target_class_set:
            target_syms.append(sym)
    if not target_syms:
        return out

    # Group classes by file
    path_to_classes: dict[str, list] = {}
    for sym in idx.symbol_index:
        if sym.kind == "class":
            path_to_classes.setdefault(sym.path, []).append(sym)

    # Build similarity signatures from target names
    target_short_names = [s.name.rsplit(".", 1)[-1] for s in target_syms]
    target_camel_tokens: set[str] = set()
    target_prefixes: set[str] = set()
    target_suffixes: set[str] = set()
    for n in target_short_names:
        target_camel_tokens.update(t.lower() for t in _camel_tokens(n))
        if len(n) >= 8:
            target_prefixes.add(n[:4].lower())
            target_suffixes.add(n[-4:].lower())

    emitted: set[tuple[str, str]] = set()   # (path, symbol name)
    target_id_keys = {(s.path, s.name) for s in target_syms}

    for tgt in target_syms:
        tgt_short = tgt.name.rsplit(".", 1)[-1]
        siblings = path_to_classes.get(tgt.path, [])
        for sib in siblings:
            key = (sib.path, sib.name)
            if key in target_id_keys or key in emitted:
                continue
            sib_short = sib.name.rsplit(".", 1)[-1]
            sib_lower = sib_short.lower()
            score = 0.55  # base for same-file
            why_bits = [f"same file as {tgt_short}"]

            # Camel-token overlap (strongest name signal)
            sib_tokens = set(t.lower() for t in _camel_tokens(sib_short))
            shared = target_camel_tokens & sib_tokens
            if shared:
                score += min(0.05 * len(shared), 0.20)
                why_bits.append(f"shares token(s) {sorted(shared)}")

            # Prefix / suffix match
            if any(sib_lower.startswith(p) for p in target_prefixes):
                score += 0.05
                why_bits.append("shares name prefix")
            if any(sib_lower.endswith(s) for s in target_suffixes):
                score += 0.08
                why_bits.append("shares name suffix")

            # Sibling-by-position: class directly adjacent in source (within 60 lines)
            if (abs(sib.start_line - tgt.end_line) <= 60 or
                abs(tgt.start_line - sib.end_line) <= 60):
                score += 0.03
                why_bits.append("nearby in source")

            score = max(0.45, min(score, 0.95))
            already = _span_seen(viewed, sib.path, sib.start_line, sib.end_line)
            out.append(EvidenceCandidate(
                id=_next_id("E", id_counter),
                operation="FIND_PARALLEL_IMPLEMENTATIONS",
                relation="sibling_class_same_file",
                path=sib.path,
                start_line=sib.start_line,
                end_line=sib.end_line,
                symbol=sib.name,
                already_seen=already,
                score=round(score, 3),
                why="Sibling class: " + "; ".join(why_bits) + ".",
                extra={
                    "sibling_of": tgt.name,
                    "same_file": True,
                    "shared_camel_tokens": sorted(shared),
                },
            ))
            emitted.add(key)

    out.sort(key=lambda c: -c.score)
    return out


def find_parallel_implementations(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """Find files / classes following a similar pattern to the edited files.

    Two strategies combined:

    Strategy A — same-file sibling classes (RETRIEVE / sibling audit):
      When target_hints contain class names (e.g. RelatedFieldListFilter), find
      OTHER classes in the same file. The agent may have edited only one of N
      sibling classes that need a parallel update.

    Strategy B — cross-file parallel implementations (REHYDRATE):
      For each edited file, derive a "shape signature" from its basename
      (e.g. 'itrs_observed_transforms.py' -> tokens ['itrs','observed','transforms']).
      Surface other files in the same directory whose basename shares tokens
      OR files in any directory whose basename shares the "shape"
      (e.g. all '*_observed_transforms.py').

    The two strategies are merged and sorted by score; the top `budget`
    candidates are emitted with relations `sibling_class_same_file` or
    `parallel_implementation`.
    """
    result = ActionResult(
        operation="FIND_PARALLEL_IMPLEMENTATIONS",
        target="sibling classes + parallel implementations",
        budget=3,
        status="done",
    )

    edited = list(runtime_signals.edited_files or [])
    hint_class_names = list(_target_hints_of_kind(manual_diagnosis, ["class", "symbol"]))
    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)

    # -------- Strategy A: same-file sibling classes --------
    # Filter hints to those that are actually classes in the index
    target_class_names: list[str] = []
    if hint_class_names:
        known_classes = {sym.name.rsplit(".", 1)[-1] for sym in idx.symbol_index if sym.kind == "class"}
        for h in hint_class_names:
            short = h.rsplit(".", 1)[-1]
            if short in known_classes:
                target_class_names.append(short)

    sibling_candidates: list[EvidenceCandidate] = []
    if target_class_names:
        sibling_candidates = _sibling_classes_in_target_file(
            idx, target_class_names, viewed, id_counter,
        )

    # -------- Strategy B: cross-file parallel implementations --------
    cross_file_candidates: list[EvidenceCandidate] = []
    if edited:
        shape_tokens: set[str] = set()
        file_dir_map: dict[str, str] = {}
        for f in edited:
            stem = _file_basename_stem(f)
            file_dir_map[f] = f.rsplit("/", 1)[0] if "/" in f else ""
            shape_tokens.update(t for t in re.split(r"[_\-.]", stem) if len(t) >= 3)

        hint_symbols = list(_target_hints_of_kind(manual_diagnosis, ["symbol", "function", "class"]))
        hint_last_tokens = set()
        for h in hint_symbols:
            last = h.rsplit(".", 1)[-1]
            if len(last) >= 4:
                hint_last_tokens.add(last.lower())

        path_to_symbol_tails: dict[str, set[str]] = {}
        for sym in idx.symbol_index:
            tail = sym.name.split(".")[-1].lower()
            if len(tail) >= 4:
                path_to_symbol_tails.setdefault(sym.path, set()).add(tail)

        edited_set = set(edited)
        scored = []
        for f in idx.file_index:
            if f.path in edited_set:
                continue
            stem = _file_basename_stem(f.path)
            f_tokens = set(t for t in re.split(r"[_\-.]", stem) if len(t) >= 3)
            overlap = len(f_tokens & shape_tokens)
            if overlap == 0:
                continue
            same_dir = any(f.path.startswith(d + "/") for d in file_dir_map.values())
            symbol_hits = 0
            if hint_last_tokens:
                file_tails = path_to_symbol_tails.get(f.path, set())
                symbol_hits = len(file_tails & hint_last_tokens)
            score = 0.55 + min(overlap * 0.1, 0.25) + (0.05 if same_dir else 0.0) + min(symbol_hits * 0.05, 0.15)
            score = min(score, 0.95)
            scored.append((score, overlap, same_dir, symbol_hits, f))

        scored.sort(key=lambda x: (-x[0], -x[1]))
        seen_paths = set()
        for score, overlap, same_dir, symbol_hits, f in scored:
            if f.path in seen_paths:
                continue
            seen_paths.add(f.path)
            start = 1
            end = min(50, f.line_count) if hasattr(f, "line_count") and f.line_count else 50
            already = _span_seen(viewed, f.path, start, end)
            why_bits = [f"{overlap} token(s) overlap with edited-file shape"]
            if same_dir:
                why_bits.append("same directory as an edited file")
            if symbol_hits:
                why_bits.append(f"{symbol_hits} target-hint symbol(s) present")
            cross_file_candidates.append(EvidenceCandidate(
                id=_next_id("E", id_counter),
                operation="FIND_PARALLEL_IMPLEMENTATIONS",
                relation="parallel_implementation",
                path=f.path,
                start_line=start,
                end_line=end,
                symbol=_file_basename_stem(f.path),
                already_seen=already,
                score=round(score, 3),
                why="Parallel implementation: " + "; ".join(why_bits) + ".",
                extra={
                    "shape_token_overlap": overlap,
                    "same_dir": same_dir,
                    "target_hint_symbol_hits": symbol_hits,
                },
            ))

    # -------- Merge & emit top `budget` --------
    merged = sibling_candidates + cross_file_candidates
    merged.sort(key=lambda c: -c.score)

    for c in merged:
        if len(result.candidates) >= result.budget:
            break
        result.candidates.append(c)

    if not result.candidates:
        result.status = "no_candidates"
    return result


def find_imports(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """Find files that import a target-hint module/symbol.

    Uses ripgrep to find `from <module> import` or `import <module>` lines.
    Target modules come from target_hints of kind 'module' (and 'symbol' fallback).
    """
    result = ActionResult(
        operation="FIND_IMPORTS",
        target="files importing target modules/symbols",
        budget=2,
        status="done",
    )
    modules = list(_target_hints_of_kind(manual_diagnosis, ["module"]))
    # Fallback: derive modules from edited files (the file itself is the imported thing)
    for f in runtime_signals.edited_files or []:
        # itrs_observed_transforms.py -> "itrs_observed_transforms"
        stem = _file_basename_stem(f)
        if stem and stem not in modules:
            modules.append(stem)
    if not modules:
        result.status = "skipped"
        result.skipped_reason = "no module target_hints and no edited files to derive from"
        return result

    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)
    seen_sites: set[tuple[str, int]] = set()
    scored = []
    for mod in modules:
        # Match `from <...>.<mod> import` and `import <...>.<mod>`
        mod_last = mod.rsplit(".", 1)[-1]
        if len(mod_last) < 4:
            continue
        # Build regex: from ... import ... <mod_last>
        # Simpler: just search for the module name as a word, then filter lines starting with from/import
        try:
            hits = ri.rg_search(Path(idx.repo_root), rf"\b{re.escape(mod_last)}\b", max_hits=50)
        except Exception:
            hits = []
        for hit in hits:
            path = hit.get("path", "")
            line_no = int(hit.get("line", 0))
            line_text = hit.get("content", "")
            stripped = (line_text or "").strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            # skip the module's own definition file
            if path.endswith(mod_last + ".py") and "/" + mod_last + "/" in path + "/":
                continue
            key = (path, line_no)
            if key in seen_sites:
                continue
            seen_sites.add(key)
            already = _span_seen(viewed, path, line_no, line_no)
            scored.append((0.7, path, line_no, stripped, mod_last, already))

    scored.sort(key=lambda x: -x[0])
    for score, path, line_no, line_text, mod, already in scored:
        if len(result.candidates) >= result.budget:
            break
        result.candidates.append(EvidenceCandidate(
            id=_next_id("E", id_counter),
            operation="FIND_IMPORTS",
            relation="import_site",
            path=path,
            start_line=line_no,
            end_line=line_no,
            symbol=mod,
            already_seen=already,
            score=score,
            why=f"Imports '{mod}' — registration site for the new/edited module.",
            extra={"import_line": line_text[:200]},
        ))

    if not result.candidates:
        result.status = "no_candidates"
    return result


def find_callers(
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
    id_counter: list,
) -> ActionResult:
    """Find call/reference sites of target-hint symbols (functions, classes).

    For astropy-13398: targets like AltAz, HADec, itrs_observed_transforms
    → finds registration sites and call sites.
    """
    result = ActionResult(
        operation="FIND_CALLERS",
        target="call/reference sites of target symbols",
        budget=2,
        status="done",
    )
    symbols = list(_target_hints_of_kind(manual_diagnosis, ["symbol", "function", "class"]))
    if not symbols:
        result.status = "skipped"
        result.skipped_reason = "no symbol/function/class target_hints"
        return result

    viewed = _normalize_viewed_spans(runtime_signals.viewed_spans)
    seen_sites: set[tuple[str, int, str]] = set()
    scored = []
    for sym in symbols:
        last = sym.rsplit(".", 1)[-1]
        if len(last) < 4:
            continue
        try:
            hits = ri.rg_search(Path(idx.repo_root), rf"\b{re.escape(last)}\b", max_hits=50)
        except Exception:
            hits = []
        for hit in hits:
            path = hit.get("path", "")
            line_no = int(hit.get("line", 0))
            line_text = hit.get("content", "")
            stripped = (line_text or "").strip()
            key = (path, line_no, last)
            if key in seen_sites:
                continue
            seen_sites.add(key)
            already = _span_seen(viewed, path, line_no, line_no)
            # Score: registration-style lines (decorators, FunctionTransformWithFiniteDifference)
            # score higher
            score = 0.6
            if any(decorator in stripped for decorator in ["@frame_transform_graph", "@", "register"]):
                score = 0.85
            elif "def " in stripped or "class " in stripped:
                score = 0.75
            elif last + "(" in stripped:
                score = 0.7
            scored.append((score, path, line_no, stripped, last, already))

    scored.sort(key=lambda x: -x[0])
    emitted_paths = set()
    for score, path, line_no, line_text, sym, already in scored:
        if len(result.candidates) >= result.budget:
            break
        # Limit 1 per file to keep diversity
        if path in emitted_paths:
            continue
        emitted_paths.add(path)
        result.candidates.append(EvidenceCandidate(
            id=_next_id("E", id_counter),
            operation="FIND_CALLERS",
            relation="call_site",
            path=path,
            start_line=line_no,
            end_line=line_no,
            symbol=sym,
            already_seen=already,
            score=score,
            why=f"References '{sym}'.",
            extra={"reference_line": line_text[:200]},
        ))

    if not result.candidates:
        result.status = "no_candidates"
    return result


# ============================================================================
# Dispatcher
# ============================================================================

OPERATIONS = {
    "FIND_FAILED_TEST":              find_failed_test,
    "REHYDRATE_SEEN_EVIDENCE":       rehydrate_seen_evidence,
    "FIND_SYMBOL_DEFINITION":        find_symbol_definition,
    "READ_DEPENDENCY_NEIGHBORHOOD":  read_dependency_neighborhood,
    "FIND_NEIGHBOR_TESTS":           find_neighbor_tests,
    "FIND_PARALLEL_IMPLEMENTATIONS": find_parallel_implementations,
    "FIND_IMPORTS":                  find_imports,
    "FIND_CALLERS":                  find_callers,
}


def execute_plan(
    retrieval_plan: List[dict],
    idx: ri.RepositoryIndex,
    runtime_signals: RuntimeSignals,
    manual_diagnosis: ManualDiagnosis,
) -> List[ActionResult]:
    """Walk retrieval_plan, dispatch each operation, return all results.

    Unknown operations are reported as a single skipped ActionResult.
    Synthesis operations (no retrieval) are reported as done with 0 candidates.
    """
    id_counter = [0]   # mutable counter for E1/E2/...
    results: list[ActionResult] = []
    for step in retrieval_plan or []:
        if not isinstance(step, dict):
            continue
        op = step.get("operation") or step.get("op") or ""
        target = step.get("target", "")
        budget = int(step.get("budget", 3))
        if op not in OPERATIONS:
            # Synthesis actions: handled by context_packet_builder, not retrieval
            if op in {"RECONCILE_TARGET_FIX_WITH_REGRESSION_CONSTRAINTS"}:
                results.append(ActionResult(
                    operation=op, target=target, budget=budget,
                    status="done",
                    candidates=[],
                    notes=["synthesis action — handled by context_packet_builder; no retrieval needed"],
                ))
                continue
            results.append(ActionResult(
                operation=op, target=target, budget=budget,
                status="skipped",
                skipped_reason=f"unknown operation '{op}' (not in v0 retrieval executor)",
            ))
            continue
        # Override the function's default budget with the plan's budget
        fn = OPERATIONS[op]
        r = fn(idx, runtime_signals, manual_diagnosis, id_counter)
        r.budget = budget
        results.append(r)
    return results
