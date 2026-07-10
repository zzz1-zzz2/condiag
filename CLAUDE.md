# ConDiag Project CLAUDE.md

## Project Identity

ConDiag: **Failure-Guided Diagnostic Search Contracts** for Repository-Level Program Repair.
Research codebase at ~/condiag/. Artifacts at /mnt/d/condiag-artifacts/condiag/.

**Core claim:** ConDiag improves Attempt-2 repair outcomes not by retrieving context itself, but by producing a **structured Diagnostic Search Contract** that steers the agent's exploration in its own tool-use loop.

ConDiag is a **Failure-Guided Diagnostic Search Controller**, NOT a retrieval executor.
The diagnostic output (Contract) is what distinguishes ConDiag from plain/feedback/broad baselines.

## Experiment Setting (NON-NEGOTIABLE)

This is a **Post-Validation / CI-Feedback Repair** setting, NOT standard hidden-test SWE-bench pass@1.

Correct pipeline:

```
Host Agent attempt_1
→ official validation failure output
→ Failure Witness + Trajectory Signals
→ ConDiag Diagnosis (structured)
→ Diagnostic Search Contract (JSON → Markdown)
→ Host Agent attempt_2 (contract-guided tool use)
→ workspace_git_diff patch
→ official ContextBench eval
→ trajectory metrics (attempt_1 vs attempt_2) + rescue matrix
```

- We run validation harness ourselves after attempt_1, inject failure output into attempt_2.
- This mirrors CI feedback, but validation is performed by us, not by an existing CI system.
- All retry baselines receive the same failure witness.
- Do NOT call this "Strict hidden-test SWE-bench". It is not.
- **Primary metric** is ContextBench trajectory metrics (File/Block/Symbol/Line P-R-F1, ΔContext F1), NOT repair rate alone.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Attempt-1 trajectory + validation failure output        │
│  → Failure Witness Builder  (post-validation logs)      │
│  → Trajectory Signals       (runtime signals +          │
│                              oracle audit eval-only)    │
│  → ConDiag Diagnosis        (CDType: 7-type scoring    │
│                              classification)            │
│  → Diagnostic Search Contract (JSON)                    │
│    ├── context_deficiency_diagnosis: {...CDType scores}  │
│    ├── required_inspections: [...files, lines, symbols]  │
│    ├── required_searches:   [...queries, targets]        │
│    ├── anti_patterns:       [...behaviors to avoid]      │
│    └── validation_target:   {...specific edit scope}     │
│  → Contract → Markdown injection → Attempt-2 agent      │
│  → Agent executes searches via its own tool loop        │
│  → Contract Compliance Analyzer (explicit/covered/ignored)│
│  → ContextBench eval (attempt_1 vs attempt_2 metrics)   │
└─────────────────────────────────────────────────────────┘

## Execution Environment (MANDATORY)

- **All code execution**: WSL2 (`/home/swelite/condiag/`)
- **All artifacts**: `/mnt/d/condiag-artifacts/condiag/`
- **Windows side**: read-only via `D:\condiag-artifacts` (do NOT write from Git Bash)
- **No hardcoded paths** like `D:/`, `/d/`, `/mnt/d/` in Python/shell code — use `experiment_settings.py`
- Python calls via WSL python3 directly — no `wsl.exe` wrappers, no `MSYS_NO_PATHCONV`
- All instances referenced by canonical `instance_manifest.py` in `manifests/instances_v1.jsonl`

## Project Structure

```text
/home/swelite/condiag/
├── condiag/          # Core method: trajectory_signals, search_contract_builder,
│                     #   context_deficiency_diagnoser, contract_renderer
├── experiments/      # Experiment orchestration: baseline_handlers, retry_runner,
│                     #   failure_witness_builder, instance_manifest, experiment_settings
├── scripts/          # Stable CLI entry points
├── scripts_tmp/      # Legacy temp scripts (to be archived)
├── docs/             # ADR, plans
└── archive/          # Frozen legacy code

/mnt/d/condiag-artifacts/condiag/
├── manifests/        # instance_v1.jsonl, legacy_inventory
├── instances/        # <id>/attempt_1/ + retries/<baseline>/
├── aggregate/        # Paper tables
├── archive/          # Pre-v2.1 frozen artifact directories
└── v0/               # Original legacy tree (frozen, read-only — do NOT modify)
```
```

## Hard Rules (VIOLATIONS = ABORT)

### Rule 1: Gold Leakage (Absolute Ban)

The following are FORBIDDEN in any agent-facing output, context packet, or retry input:

- Gold solution patch
- Gold context (official correct context range)
- Resolved label
- F2P/P2P / fail-to-pass / pass-to-pass as benchmark labels (say "validation test" instead)
- ContextBench oracle metrics (file_cov, span_cov, EditLoc)
- feedback_success_patch (other baseline's successful patch)
- manual_hindsight_only hints
- Any "this is a benchmark target test" implication
- **Gold file timeline / gold file coverage / any gold-derived signal**

Failure Witness must carry: `source = "post_validation_output"`, `oracle_labels_hidden = true`.

**Oracle audit signals** (gold file timeline, gold file coverage, gold seen-then-dropped) are:
- ALLOWED in: eval-only analysis, error analysis, Table 2 metrics
- FORBIDDEN in: any runtime module (trajectory_signals runtime path, search_contract_builder, diagnosis_generator, retry prompt)

### Rule 2: Write Protection

- Analysis reports go to `docs/` or `artifacts/reports/` ONLY.
- NEVER write `.md` / `.txt` / `.html` report content into `.py` files.
- Existing `.py` files are ONLY modified for their stated code purpose.
- Forbidden write targets: `experiments/*.py`, `condiag/*.py`, any source code file.

### Rule 3: Patch Generation Protocol

- Patches come ONLY from `workspace_git_diff` (git diff HEAD in workspace).
- NEVER extract diff from assistant message text.
- NEVER use direct LLM output as a patch.
- patch_source must be `workspace_git_diff`.

### Rule 4: Host-Agent Retry Protocol

- All retry attempt_2 must go through `host_agent_retry_runner.py`.
- Must show real tool use (tool_calls > 0).
- `baseline_handlers.py` generates intervention artifacts ONLY; it does NOT execute attempt_2.
- `plain_rerun` handler produces artifacts, runner executes.

### Rule 5: Diagnostic Search Contract Provenance

Every `DiagnosticSearchContract` MUST have:
- `contract_source` in: `failure_witness_signals`, `trajectory_signal_analysis`, `issue_driven_deduction`.
- `supporting_artifact` non-empty (links to specific evidence).
- `required_inspections` and `required_searches` **must be traceable to runtime-only sources**: stack frame files, failed test files, edited files, viewed files, issue-term matched files, error-term matched files, viewed-but-dropped evidence.
- FORBIDDEN sources: `gold_patch`, `feedback_success_patch`, `manual_hindsight_only`, `contextbench_oracle`, `gold_file_timeline`.

### Rule 6: Version Fields (Every Result)

All result files (CSV/JSON) MUST include: `method_version`, `contract_version`, `failure_witness_version`, `trajectory_signal_version`, `eval_version`, `retry_runner_version`, `plan_version`.

Current plan_version = `plan_v2.0_search_contract`.

## Forbidden Actions

- Do NOT do direct LLM patch generation.
- Do NOT extract diff from assistant message as official patch.
- Do NOT write custom official eval parser (Django/Sympy).
- Do NOT manually modify baseline patch and count as official.
- Do NOT run only ConDiag without plain/feedback/broad/random/rehydrate controls.
- Do NOT switch Host Agent model mid-experiment.
- Do NOT put gold patch implementation into contract.
- Do NOT reverse-engineer feedback_success_patch into ConDiag.
- Do NOT bypass canonical_base_eval_matrix.
- Do NOT overwrite v0/v1 artifacts.
- Do NOT skip gates.
- Do NOT invent new experiment paths.
- Do NOT have ConDiag execute retrieval itself (retrieval is the agent's job).
- Do NOT use success cases (attempt_1 resolved) in ConDiag-Failure experiment.
- Do NOT feed gold-derived signals (gold file timeline, gold coverage) into any runtime module.

## Baseline Input Contract

| baseline | issue | failure witness | contract | broad context | rehydrate only |
|---|---|---|---|---|---|
| plain_rerun | YES | NO | NO | NO | NO |
| feedback_retry | YES | YES | NO | NO | NO |
| random_expansion_retry | YES | YES | NO | random | NO |
| broad_expansion_retry | YES | YES | NO | YES | NO |
| rehydrate_only_retry | YES | YES | NO | NO | YES |
| condiag_contract_retry | YES | YES | YES | NO | NO |

Non-variable (same across all baselines): model, temperature, max_steps, timeout, clean base, runner, Host Agent, patch collection, eval.

**First-round priority:** plain_rerun, feedback_retry, broad_expansion_retry, condiag_contract_retry.
random_expansion and rehydrate_only are ablation-level controls, added after main comparison.

## Information Boundary

**Allowed in runtime/contract/agent input:**
- issue text, attempt_1 patch summary
- validation failure output, traceback, assertion message
- expected vs actual, validation command
- repo-visible source, public API signature (as search hints in contract)
- runtime introspection result, failure witness
- runtime trajectory signals: error-edit alignment (4-layer), error-visit alignment, exploration mode, test behavior, viewed-then-dropped evidence

**Forbidden in runtime/contract/agent input (any):**
- gold patch, gold context, resolved label
- ContextBench oracle metrics
- F2P/P2P as benchmark semantic labels
- feedback_success_patch, manual_hindsight_only hint
- any final solution copied from gold/feedback patch
- ContextBench oracle-derived trajectory metrics
- gold file timeline, gold file coverage, gold seen-then-dropped (these are eval-only)

## Trajectory Signals Architecture

```
trajectory_signals.py
  ├─ runtime_signals (enter runtime / contract / agent input):
  │   ├── error_edit_alignment (4-layer):
  │   │   ├── file-level:    top repo error file edited?
  │   │   ├── symbol-level:  error function/class edited?
  │   │   ├── line-window:   edit line within error line ±N?
  │   │   └── term-overlap:  error/issue terms appear in edited span?
  │   │   → output: aligned / viewed_not_edited / edited_elsewhere / error_file_never_viewed / unknown
  │   ├── error_visit_alignment: agent visited failure site / stack frames?
  │   ├── exploration_mode: focused / oscillating / jumping / shallow_scan
  │   ├── test_behavior: test_runs, checkouts, regression_signals
  │   └── viewed_then_dropped_evidence: files viewed but not in edit context
  │
  └─ oracle_audit (eval-only, NOT in runtime):
      ├── gold_file_first_seen_step
      ├── gold_file_coverage
      ├── gold_seen_then_dropped
      └── eval_only: true
```

## Success Case Policy

- Attempt-1 resolved instances → **only** used for Attempt-1 baseline (Table 2)
- **Not** used in ConDiag-Failure experiment
- ConDiag experiment runs only on first-failed pool
- Rationale: success cases don't have diagnostic signal to study

## Main Experiments (4 Figures, 5 Tables)

### Figures
- **Figure 1:** Motivation — attempt_1 failure patterns vs attempt_2 trajectory shift
- **Figure 2:** Method architecture (ConDiag + Search Contract flow)
- **Figure 3:** Search Contract example (structured JSON → agent behavior change)
- **Figure 4:** Trajectory shift bar charts (attempt_1 vs attempt_2 across baselines)

### Tables
- **Table 1:** Dataset statistics (instances, repos, benchmarks)
- **Table 2:** Attempt-1 baseline (resolved cases: File/Block/Symbol/Line metrics)
- **Table 3:** Main results — trajectory metrics (attempt_1 vs attempt_2, all baselines)
- **Table 4:** Repair outcome (rescue counts per baseline)
- **Table 5:** Ablation (contract components, compliance rate)

## Current Execution Order

### Phase 0 — Core Modules (offline, no network needed)
```
Task 0: Update CLAUDE.md, memory, plan  ← DONE 2026-07-08
Task 1: Write trajectory_signals.py (runtime signals + oracle audit)
Task 2: Write search_contract_builder.py (runtime-only sources)
Task 3: Write contract_compliance_analyzer.py
```

**Phase 0 Gate:** Offline verification on 5 dev cases (django-11820, django-12125, django-13513, django-16454, sympy-20428)
- Check trajectory_signals parse stably
- Check error-edit alignment non-empty
- Check exploration mode has discrimination
- Check search_contract generates specific required_inspections/searches
- Check gold timeline only appears in eval-only oracle_audit

### Phase 1 — Data & Baseline
```
Task 4: Build instance manifest (52 instances → 99 instances)
Task 5: Run ContextBench eval on 52 Attempt-1 trajs → Table 2
Task 6: Split solved / first-failed by official eval
Task 7: Complete Batch5 remaining instances (~40 more, needs Clash TUN)
Task 8: Run Attempt-1 traj + ContextBench eval for new instances
```

### Phase 2 — ConDiag Experiment
```
Task 9: Run 4 core baselines × first-failed instances
        (plain_rerun, feedback_retry, broad_expansion, condiag_contract)
Task 10: Run 2 ablation baselines (random_expansion, rehydrate_only)
Task 11: ContextBench trajectory metrics on all Attempt-2 trajs
Task 12: Contract compliance analysis
Task 13: Build rescue matrix + paper figures/tables
```

### Gate Dependencies
```
Task 0 DONE → Task 1/2/3 UNBLOCKED
Phase 0 verification PASS → Phase 1 UNBLOCKED
Task 6 NOT complete → Phase 2 CANNOT start
Task 9 NOT complete → Task 10-13 blocked
```

## Pre-Task Protocol (MANDATORY Before Every Task)

Before starting ANY task, output:

```
## Pre-Task Check
Phase: X
Current task: Task N — [short description]
Upstream gates: [which gates must pass]
Allowed files:
  - READ: [file1, file2, ...]
  - WRITE: [file1, file2, ...]
  - CREATE: [file1, file2, ...]
Applicable rules: Rule [1, 3, ...]
Gate check: [which gates, status]
Artifacts I will produce: [...]
Validation commands I will run: [...]
Risk of leakage or baseline contamination: [...]
Stop conditions for this task: [...]
```

If you cannot produce this, STOP and ask the user.

## Post-Task Protocol (MANDATORY After Every Task)

After completing any task, output:

```
## Post-Task Report
Completed Gate/Task: [...]
Modified files: [...]
New artifacts: [...]
Version fields updated: [...]
Tests run: [...]
git diff --stat: [...]
Leakage guard status: [...]
Whether official eval was used: [...]
Remaining blockers: [...]
Memory update summary: [...]
```

## Key File Map

| Module | Path |
|---|---|
| Trajectory Signals | `condiag/trajectory_signals.py` | DONE |
| Search Contract Builder | `condiag/search_contract_builder.py` | DONE |
| Context Deficiency Diagnoser | `condiag/context_deficiency_diagnoser.py` | DONE |
| Contract Renderer | `condiag/contract_renderer.py` |
| Contract Compliance Analyzer | `experiments/contract_compliance_analyzer.py` |
| Experiment Settings | `experiments/experiment_settings.py` |
| Instance Manifest | `experiments/instance_manifest.py` |
| Baseline Handlers | `experiments/baseline_handlers.py` |
| Retry Runner | `experiments/host_agent_retry_runner.py` |
| Schemas | `condiag/schemas.py` |
| Leakage Guard | `condiag/leakage_guard.py` |
| Failure Witness Builder | `experiments/failure_witness_builder.py` | DONE |
| Canonical Matrix | `$CANONICAL_MATRIX_PATH` |
| Eval Script | `scripts_tmp/smoke_eval_matrix.py` |

### Removed (replaced by new architecture)
| Old Module | Replacement |
|---|---|
| `condiag/api_navigation.py` | (retrieval → agent tool loop) |
| `condiag/retrieval_executor.py` | (retrieval → agent tool loop) |
| `condiag/context_packet_builder.py` v1-v2 | `search_contract_builder.py` |
| `condiag/diagnosis_generator.py` | (to be replaced by structured diagnosis) |

## Canonical State

| Artifact | Location | Status |
|---|---|---|
| CLAUDE.md | `/home/swelite/condiag/CLAUDE.md` | UPDATED 2026-07-10 |
| Instance Manifest | `manifests/instances_v1.jsonl` | DONE (99 instances) |
| Experiment Settings | `experiments/experiment_settings.py` | DONE |
| Trajectory Signals | `condiag/trajectory_signals.py` | DONE |
| Search Contract Builder | `condiag/search_contract_builder.py` | DONE |
| Context Deficiency Diagnoser | `condiag/context_deficiency_diagnoser.py` | DONE |
| Contract Renderer | `condiag/contract_renderer.py` | DONE |
| Contract Compliance Analyzer | `experiments/contract_compliance_analyzer.py` | TODO |
| Migration | `manifests/migration_log_v1.json` | DONE |
| Legacy Inventory | `manifests/legacy_inventory.json` | DONE (frozen) |

## v0 Baseline Results (Frozen, Do Not Overwrite)

```
base_miniswe: 19 instances (13 resolved / 5 unresolved / 1 conflict)
  First-failed pool: django-11820, django-12125, django-13513, django-16454, sympy-20428
  Conflict (excluded): sympy-19954 (P1=True vs P3=False)
v0 eval: feedback_retry 2/4 rescue, condiag_retry 1/4 rescue, condiag_unique 0
```

These results are from the old "ConDiag-does-retrieval" architecture. New contract-based results will replace them in Phase 2.

## Terse Communication

- No emoji unless asked.
- Chinese for user-facing text; English for code/terms.
- State results directly, don't narrate your thinking process.
- End of turn: one sentence about what changed and what's next.
