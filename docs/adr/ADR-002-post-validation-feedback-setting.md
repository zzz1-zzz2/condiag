# ADR-002: Post-Validation / CI-Feedback Repair Setting

**Status**: Accepted
**Date**: 2026-06-30
**Supersedes**: (none — initial ADR for experiment setting)

---

## Context

ConDiag was originally conceived in a generic SWE-bench repair context. As the experiment design matured, it became clear that the correct setting is **not** standard hidden-test SWE-bench pass@1, but rather a **post-validation feedback loop** where attempt_1 failure signals are used to guide a second repair attempt.

## Decision

We adopt the **Post-Validation / CI-Feedback Repair Setting** as the official experiment setting for ConDiag v1.

### What this means

1. Host Agent attempt_1 produces a patch.
2. **We** (not an existing CI system) run the SWE-bench / ContextBench validation harness on that patch.
3. Validation failure output (traceback, assertion error, expected vs actual) is extracted as a **Failure Witness**.
4. All retry baselines receive the **same** Failure Witness per the baseline input contract.
5. Host Agent attempt_2 runs through a tool-use loop with context determined by the baseline.
6. attempt_2 patch comes from `workspace_git_diff` (git diff HEAD).
7. `resolved` status comes only from official SWE-bench / ContextBench eval.

### What this is NOT

- **NOT** hidden-test SWE-bench pass@1. The validation tests are known to us.
- **NOT** a setting where the agent sees hidden test names at any point.
- **NOT** a setting where gold patch / gold context / resolved label can leak into agent input.

### Information boundary

**Allowed in agent-facing output:**
- Validation failure output, traceback, assertion message, expected vs actual
- Validation command
- test_patch (for running validation tests)
- Public API signature, repo-visible source, runtime introspection
- Issue text, attempt_1 patch summary (files changed, not content)
- Failure Witness

**Forbidden in agent-facing output:**
- Gold solution patch, gold context, resolved label
- F2P/P2P as benchmark semantic labels (use "validation test" instead)
- ContextBench oracle metrics
- feedback_success_patch, manual_hindsight_only hints

## Rationale

### Why not hidden-test pass@1?

In a hidden-test setting, the agent has no feedback about why attempt_1 failed. This makes context diagnosis impossible — there is no signal to diagnose from. ConDiag's core contribution is using failure signals to guide retrieval, which requires a post-validation feedback loop.

### Why not real CI?

We need reproducibility and control. Running our own validation harness gives us:
- Raw eval logs for Failure Witness extraction.
- Deterministic eval environment (Docker containers).
- Ability to save and replay eval outputs.

### Why baseline input contract?

The only experimental variable should be the context packet content. All other variables (model, temperature, max_steps, timeout, clean base, runner, Host Agent, patch collection, eval) must be identical across baselines. This ensures that any rescue rate difference is attributable to ConDiag's diagnosis and targeted retrieval.

## Consequences

### Positive

- Clean experimental variable isolation.
- Failure Witness provides actionable signal for diagnosis routing.
- Mirrors real-world CI feedback workflows.
- Enables fair comparison between plain_rerun / feedback_retry / broad_expansion / condiag_retry.

### Negative

- Requires running official eval twice per instance (attempt_1 + attempt_2).
- rescue rate may be lower than hidden-test pass@1 (because attempt_1 has already failed once).
- Failure Witness quality depends on eval harness output parsing accuracy.

### Risks

- Failure type misparse: mitigated by 30% human spot-check.
- Docker eval failures: mitigated by cached local images.
- F2P/P2P terminology leakage: mitigated by leakage_guard.

### Related

- ADR-001: (not yet created — ConDiag project identity)
- CLAUDE.md: Hard rules and execution order
- `docs/plans/condiag_plan_v1_post_validation.md`: Full experiment plan
