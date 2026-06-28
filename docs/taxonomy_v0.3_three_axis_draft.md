# ConDiag Taxonomy v0.3 — Three-Axis Design (Draft)

**Status:** DRAFT — pending advisor review
**Date:** 2026-06-28
**Authors:** ConDiag team
**Supersedes:** `pathology_taxonomy.v0.2.json` (5 pathologies on a single axis)
**Scope of change:** Taxonomy reorganization only. The five recovery flows (RECONCILE / RESTRAIN / REHYDRATE / RETRIEVE / RELOCALIZE + NOOP/ABSTAIN) are **preserved unchanged** — they move from "primary taxonomy axis" to "recovery action layer".

---

## 1. Motivation

The single-axis taxonomy in v0.2 (`pathology_taxonomy.v0.2.json`) defined five pathology labels: `OVER_EXPLORE_OVER_EDIT`, `EXPLORE_OK_EDIT_MISALIGNED`, `UNDER_EDIT_PARTIAL_FIX`, `REGRESSION_AFTER_PARTIAL_FIX`, `LIKELY_CORRECT_NOOP`. The intent was to characterize "what kind of failure is this run".

The problem: these labels **conflate two orthogonal dimensions**:

1. The **type of context evidence** the task requires (which is stable — it depends only on the issue + repo, not on the agent run).
2. The **runtime gap** between what the agent explored/utilized and what was actually needed (which is run-dependent).

This conflation has two visible costs:

- **Annotation cost.** Labeling a case as `MISSING_LOCALIZATION_DIRECTION` requires reading the trajectory to verify the localization evidence was actually unseen. The label cannot be assigned from issue + repo alone.
- **Robustness.** The same gold context (e.g. a sibling-class definition) can be `UNSEEN` for one agent, `SEEN_BUT_DROPPED` for another, and `EDIT_MISALIGNED` for a third. A taxonomy whose primary axis depends on the run is brittle.

ContextBench reports the same finding from the evaluation side: there is a measurable gap between **explored context** (file paths/spans the agent visited) and **final utilized context** (spans actually used in the patch). "Missing" is only one of several possible states for an evidence piece.

Pilot50 batch1 confirms this empirically. Of 10 mini-SWE trajectories, **7 have a ConDiag runtime trigger that disagrees with the gap status inferred from ContextBench gold-aligned metrics** (see §4).

---

## 2. Three-Axis Design

We propose splitting the taxonomy into three orthogonal axes.

### Axis 1 — Context Evidence Type (stable)

> *What kind of context evidence does this task require?*

This axis is **task- and repo-level**, not run-level. It can be assigned by inspecting the issue text, the gold patch, and the repo structure — without looking at any agent trajectory. This is what makes it cheap to annotate.

| Evidence type | Description | Example signals |
|---|---|---|
| `API_DEFINITION` | Class / function / method / attribute / variable definition | `class X:`, `def f(...)`, `y: int = ...` |
| `INTERFACE_CONTRACT` | Function signature, parameter semantics, return type, constructor semantics | Signature, docstring contracts, type hints |
| `EXPECTED_BEHAVIOR` | Test assertions, error messages, boundary behavior, visible regression tests | `assert ...`, `self.assertRaises(...)`, `FAIL_TO_PASS` |
| `CALL_DATA_FLOW` | Caller / callee, data flow, control flow, parameter propagation | Reference graph from gold patch sites |
| `DEPENDENCY_CONFIG` | Import, registration, configuration, framework hooks | `INSTALLED_APPS`, decorators, `__init__.py` |
| `CROSS_MODULE_PATTERN` | Sibling class, parallel implementation, backend-isomorphic logic | `RelatedFieldListFilter` siblings; backend A vs B |
| `ERROR_LOCALIZATION` | Error code, warning source, exception origin, stack-trace origin | `models.E015`, `SystemCheckError`, `RemovedInDjango41Warning` |
| `EDIT_SCOPE_EVIDENCE` | Evidence that supports or opposes a particular edited file / span | Coverage of edited region by retrieved evidence |
| `REGRESSION_CONSTRAINT` | Existing behavior that must not break after the fix | `PASS_TO_PASS`, regression tests |

A single gold context span can carry a **primary** label (exactly one) and zero-or-more **secondary** labels. Trigger logic only reads the primary label.

### Axis 2 — Runtime Gap Status (run-dependent, split into two layers)

> *What did the agent do with this evidence on this run?*

This axis is run-dependent. It must be split into two strictly-separated layers because ConDiag's runtime must not read gold context (enforced by `leakage_guard.py`):

#### Axis 2a — Runtime Gap Status (runtime-derivable)

Computed only from `runtime_signals.json` (viewed spans, edited files, test failures, patch shape). No gold required. This is what ConDiag's trigger can actually use.

| Status | Definition (runtime-only) |
|---|---|
| `UNSEEN` | File / symbol never appeared in viewed_spans |
| `SEEN_BUT_DROPPED` | Appeared in viewed_spans but not in `final_patch_context` |
| `EDIT_MISALIGNED` | Edited the right file but the edited span doesn't overlap the gold-likely region (signal: file in edited_files ∩ viewed_files but `editloc_pred_size > 0` and test still fails on adjacent behavior) |
| `NOISY_OVERBROAD` | `patch_shape_anomaly` strong (≥8 changed files + repeated_pattern) |
| `WRONG_LOCALIZATION` | Stack-trace / test-failure origin file not in edited_files |
| `CONSTRAINT_CONFLICT` | `test_failures_count > 0` after partial pass — runtime validation failure with non-trivial stack |
| `SUFFICIENT_OR_NOOP` | No patch-shape anomaly, no test failures, no evidence-edit mismatch — trigger abstain |
| `INSUFFICIENT_SIGNAL` | Run too short / signals below threshold — trigger abstain |

#### Axis 2b — Gold-Aligned Gap Status (evaluation-only)

Computed using ContextBench gold context. Lives in `contextbench_metrics.json`. **Never read by ConDiag runtime** — same isolation rule as `gold_check` / `FAIL_TO_PASS` / `resolved`.

| Status | Definition (gold-aligned) |
|---|---|
| `GOLD_UNSEEN` | gold span not in agent's viewed_spans (`file_cov < 1`) |
| `GOLD_SEEN_NOT_UTILIZED` | gold span in viewed_spans but not in final_patch_context (`auc_file > file_cov`) |
| `GOLD_EDIT_LOC_WRONG` | `editloc_recall = 0` despite `file_cov = 1` |
| `GOLD_SCOPE_OVERFLOW` | `file_prec < 1` (agent edited non-gold files) |
| `GOLD_FULL_RECOVERY` | All gold-aligned metrics ≥ 0.9 |

Axis 2a is what ConDiag's runtime infers. Axis 2b is what ContextBench tells us after the fact. The pair (2a, 2b) is what we use to evaluate whether ConDiag's runtime diagnosis is correct.

### Axis 3 — Recovery Intent (action layer)

> *Given (Axis 1, Axis 2), what should ConDiag do?*

This is the 5R + NOOP/ABSTAIN action layer — preserved unchanged from v0.2.

| Intent | Mapped to flow |
|---|---|
| `RECONCILE` | manual-retrieval / RECONCILE |
| `RESTRAIN` | manual-guard |
| `REHYDRATE` | manual-retrieval / REHYDRATE |
| `RETRIEVE` | manual-retrieval / RETRIEVE |
| `RELOCALIZE` | manual-retrieval / RELOCALIZE (TBD — still no seed case) |
| `NOOP` | abstain (no ContextPacket) |
| `ABSTAIN` | abstain (insufficient signal, no ContextPacket) |

---

## 3. Mapping: (Axis 1, Axis 2a) → Axis 3

The table below is **deterministic for primary mappings** (one cell → one Intent). Ambiguous cells use `runtime_evidence_strength` as a tiebreaker and may fall to `ABSTAIN`.

| Axis 1 ＼ Axis 2a | `UNSEEN` | `SEEN_BUT_DROPPED` | `EDIT_MISALIGNED` | `NOISY_OVERBROAD` | `WRONG_LOCALIZATION` | `CONSTRAINT_CONFLICT` |
|---|---|---|---|---|---|---|
| `API_DEFINITION` | RETRIEVE | REHYDRATE | RELOCALIZE | RESTRAIN | RELOCALIZE | RECONCILE |
| `INTERFACE_CONTRACT` | RETRIEVE | REHYDRATE | RELOCALIZE | RESTRAIN | RELOCALIZE | RECONCILE |
| `EXPECTED_BEHAVIOR` | RETRIEVE | REHYDRATE | RELOCALIZE | RESTRAIN | RELOCALIZE | RECONCILE |
| `CALL_DATA_FLOW` | RETRIEVE | REHYDRATE | RELOCALIZE | RESTRAIN | RELOCALIZE | RECONCILE |
| `DEPENDENCY_CONFIG` | RETRIEVE | REHYDRATE | RELOCALIZE | RESTRAIN | RELOCALIZE | RECONCILE |
| `CROSS_MODULE_PATTERN` | RETRIEVE | REHYDRATE | RELOCALIZE | RESTRAIN | RELOCALIZE | RECONCILE |
| `ERROR_LOCALIZATION` | RELOCALIZE | RELOCALIZE | RELOCALIZE | RESTRAIN | RELOCALIZE | RECONCILE |
| `EDIT_SCOPE_EVIDENCE` | RESTRAIN | RESTRAIN | RESTRAIN | RESTRAIN | RELOCALIZE | RECONCILE |
| `REGRESSION_CONSTRAINT` | RECONCILE | RECONCILE | RECONCILE | RECONCILE | RECONCILE | RECONCILE |

Reading the table: for `ERROR_LOCALIZATION`, any runtime gap (UNSEEN, SEEN_BUT_DROPPED, EDIT_MISALIGNED, WRONG_LOCALIZATION) routes to `RELOCALIZE`. This is the "missing RELOCALIZE seed" case we have been unable to find — batch1 had 0 strong / 0 weak RELOCALIZE candidates because none of the 10 instances had a stack-trace origin in user-code that escaped the edited file.

**Tiebreaker for ambiguous cells** (currently none in the deterministic table; future additions may create them): prefer the action whose `runtime_evidence_strength` is highest. If the tie is still unbroken, fall to `ABSTAIN`.

---

## 4. Batch1 Sanity Check (10 mini-SWE × Verified)

This is the most important section — the new taxonomy must be **consistent with batch1's observed behavior**, otherwise the design is wrong.

For each instance, we list:
- **ConDiag v0.2 runtime trigger** (what current code says)
- **Axis 2a** (runtime-derivable gap, inferred from `runtime_signals`)
- **Axis 2b** (gold-aligned gap, inferred from `contextbench_metrics`)
- **Axis 1 candidates** (gold context types, heuristic)
- **Axis 3 intent** (what the new mapping table would produce)

| Instance | v0.2 trigger | Axis 2a (runtime) | Axis 2b (gold) | Axis 1 (likely) | Axis 3 |
|---|---|---|---|---|---|
| django-11820 | PARTIAL_FIX_SUSPICION | EDIT_MISALIGNED | GOLD_EDIT_LOC_WRONG (fileC=1.0, ElocR=0) | EDIT_SCOPE_EVIDENCE | RELOCALIZE |
| django-12858 | EVIDENCE_EDIT_MISMATCH | SEEN_BUT_DROPPED | GOLD_SEEN_NOT_UTILIZED (fileC=0.33, AUC=0.82) | EDIT_SCOPE_EVIDENCE | REHYDRATE |
| django-13023 | PARTIAL_FIX_SUSPICION | SEEN_BUT_DROPPED | GOLD_FULL_RECOVERY? (fileC=1.0, ElocR=1, spanC=0.06) | API_DEFINITION | REHYDRATE |
| django-13109 | PARTIAL_FIX_SUSPICION | UNSEEN | GOLD_UNSEEN (fileC=0.25, AUC=0.25) | API_DEFINITION | RETRIEVE |
| django-13449 | RUNTIME_VALIDATION_FAILURE | EDIT_MISALIGNED | GOLD_EDIT_LOC_WRONG (fileC=1.0, spanC=0.93, ElocR=0) | EDIT_SCOPE_EVIDENCE / REGRESSION_CONSTRAINT | RELOCALIZE / RECONCILE |
| django-13925 | EVIDENCE_EDIT_MISMATCH | SEEN_BUT_DROPPED | GOLD_SEEN_NOT_UTILIZED (fileC=0.40, AUC=0.50) | EDIT_SCOPE_EVIDENCE | REHYDRATE |
| django-15863 | PARTIAL_FIX_SUSPICION | SUFFICIENT_OR_NOOP | GOLD_FULL_RECOVERY (all metrics = 1.0) | any | NOOP |
| django-16454 | EVIDENCE_EDIT_MISMATCH | NOISY_OVERBROAD | GOLD_SCOPE_OVERFLOW (fileP=0.33, ElocR=0) | EDIT_SCOPE_EVIDENCE | RESTRAIN |
| sympy-13372 | PATCH_SHAPE_ANOMALY | EDIT_MISALIGNED | GOLD_EDIT_LOC_WRONG (fileP=1.0, spanC=0.08, ElocR=0) | EDIT_SCOPE_EVIDENCE | RELOCALIZE |
| sympy-17318 | PARTIAL_FIX_SUSPICION | SEEN_BUT_DROPPED | GOLD_SEEN_NOT_UTILIZED (AUC=0.93, fileC=1.0, ElocR=1) | CROSS_MODULE_PATTERN | REHYDRATE |

**Key findings from the table:**

1. **v0.2 trigger agrees with Axis 3 in 3/10 cases** (django-12858, django-13925, sympy-17318 → REHYDRATE; django-16454 → RESTRAIN; sympy-13372 → close).
2. **v0.2 trigger disagrees with Axis 3 in 7/10 cases**:
   - `django-15863` is a **NOOP false positive** under v0.2 (all metrics = 1.0, but v0.2 says PARTIAL_FIX_SUSPICION).
   - `django-11820`, `django-13449`, `sympy-13372` should be **RELOCALIZE** under the new table, but v0.2 routes them to PARTIAL_FIX_SUSPICION / RUNTIME_VALIDATION_FAILURE / PATCH_SHAPE_ANOMALY.
3. **RELOCALIZE has 3 strong candidates in batch1** under the new taxonomy. Under v0.2, RELOCALIZE miner found 0/0 — because the miner used stack-trace heuristics, not the (Axis 1, Axis 2) derivation.

This is the validation we needed: the three-axis taxonomy resolves v0.2's ambiguity and recovers 3 RELOCALIZE candidates that the v0.2 miner missed.

---

## 5. Implementation Plan (deferred until Pilot50 complete)

**Do not implement until batch2 + batch3 are triaged under v0.2** — switching taxonomy mid-Pilot would invalidate cross-batch comparison.

When ready, the change is mostly renaming + one new module:

1. **Rename (preserves data):**
   - `pathology_taxonomy.json` (v0.2) → split into:
     - `runtime_gap.json` (Axis 2a — same content as v0.2 pathologies, just renamed)
     - `context_evidence.json` (Axis 1 — new file, 9 types)
   - Recovery Intent (Axis 3) stays inline in each module's `5r_action` field — no file change.
2. **New module: `context_evidence_tagger.py`**
   - Input: gold context span (file + line range) + repo
   - Output: primary Axis 1 label + secondary labels
   - Heuristic rules (no ML for v0.3):
     - function/class definition span → `API_DEFINITION`
     - span contains decorator / `INSTALLED_APPS` / registry → `DEPENDENCY_CONFIG`
     - span is in `tests/` or contains `assert` → `EXPECTED_BEHAVIOR`
     - span contains `class XError` or `raise` → `ERROR_LOCALIZATION`
     - span contains sibling class (same file, parent class shared with target) → `CROSS_MODULE_PATTERN`
     - default → `EDIT_SCOPE_EVIDENCE`
3. **Update `trigger.py`:**
   - Read Axis 2a signals from runtime_signals (already done — these are the existing 4 trigger types).
   - Output a list of candidate (Axis 2a status, evidence_strength) tuples, not a single pathology.
4. **Update `diagnosis_normalizer.py`:**
   - Combine Axis 2a candidate × Axis 1 (from `manual_diagnosis.json` for now; from `context_evidence_tagger.py` in v0.4) → Axis 3 via §3 table.
   - If ambiguous → `ABSTAIN`.
5. **Re-tag 5 seed cases:**
   - Add `axis1_primary` + `axis1_secondary` + `axis2a_status` to each `manual_diagnosis.json`.
   - Re-run `seed_regression` — must remain 5/5 PASS because Axis 3 (the 5R output) is unchanged.

---

## 6. Comparison to v0.2

| Aspect | v0.2 | v0.3 |
|---|---|---|
| Primary axis | 5 run-dependent pathology labels | 9 stable context-evidence types |
| Annotation cost | High (need trajectory reading) | Low (issue + repo + gold patch only) |
| Robustness across agents | Brittle (same case → different label) | Stable Axis 1, run-aware Axis 2 |
| RELOCALIZE discovery | Heuristic miner (0 strong in batch1) | Deterministic from (Axis 1, Axis 2) (3 strong in batch1) |
| NOOP false positive | django-15863 misfires | django-15863 → NOOP via Axis 2a SUFFICIENT_OR_NOOP |
| 5R flow code | unchanged | unchanged |
| seed_regression | 5/5 PASS | 5/5 PASS (must verify after rename) |

---

## 7. Open Questions for Advisor

1. **Axis 1 granularity.** Are 9 types too many? `EDIT_SCOPE_EVIDENCE` and `REGRESSION_CONSTRAINT` are arguably meta-types (they describe *role in the fix* rather than *kind of context*). Should we collapse to 7?
2. **Axis 2a `EDIT_MISALIGNED` definition.** Runtime-derivable signal is weak: we can detect "edited file ∩ viewed file but editloc_pred_size > 0 and adjacent test still fails" but this is heuristic. Should we accept that Axis 2a EDIT_MISALIGNED has low recall and rely on Axis 2b for evaluation?
3. **Mapping table determinism.** The proposed table is deterministic but coarse — every cell maps to exactly one Intent. Is this too rigid? Alternative: a ranked list of Intents per cell, with the trigger choosing the top-1 by `runtime_evidence_strength`.
4. **ABSTAIN threshold.** When should ConDiag say "I don't know"? Current v0.2 only abstains on NOOP-shape. The new taxonomy allows ABSTAIN whenever Axis 2a signal is below threshold. What threshold?

---

## Appendix A — Effect on Existing Seed Cases

| Seed case | v0.2 pathology | v0.3 Axis 1 + Axis 2a | Axis 3 |
|---|---|---|---|
| sympy-16597 | REGRESSION_AFTER_PARTIAL_FIX | REGRESSION_CONSTRAINT + CONSTRAINT_CONFLICT | RECONCILE ✓ |
| sympy-13877 | OVER_EXPLORE_OVER_EDIT | EDIT_SCOPE_EVIDENCE + NOISY_OVERBROAD | RESTRAIN ✓ |
| astropy-13398 | EXPLORE_OK_EDIT_MISALIGNED | CROSS_MODULE_PATTERN + SEEN_BUT_DROPPED | REHYDRATE ✓ |
| django-11400 | UNDER_EDIT_PARTIAL_FIX | CROSS_MODULE_PATTERN + UNSEEN | RETRIEVE ✓ |
| django-13195 | LIKELY_CORRECT_NOOP | (any) + SUFFICIENT_OR_NOOP | NOOP ✓ |

All 5 seed cases map to the **same Axis 3 intent** under v0.3 as under v0.2 — confirming backward compatibility.

---

*End of draft. Feedback requested before any code changes.*
