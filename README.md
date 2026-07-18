# ConDiag v4 — Failure-Guided Context Diagnosis via Persistent Repair Episodes

ConDiag v4 keeps the same repair agent alive across intermediate validation:
**Round 1 → intercept submission → official SWE-bench eval → FailureWitness →
Round 2 (same agent, same messages) → final evaluation.**

Not: two independent attempts. Not: a separate retrieval step. Not: CDType classification (v1-v3, frozen).

## Architecture

```
round1_runner.py    ↔  natural submission loop (step_limit=0, wait for Submitted)
branch_runner.py    ↔  forked R2 (SF: FW only | CD: FW + Diagnosis)
experiment.py       ↔  thin orchestration: R1 → eval → fork → eval → comparison
branch_builder.py   ↔  shared message injection (gate + runner use same code)
checkpoint.py       ↔  snapshot save/restore (messages, workspace, n_calls, cost, elapsed)
official_harness.py ↔  thin wrapper: canonical row → make_test_spec(ns=swebench) → run_instance()

experiments/v2c_entry.py  ← CLI entry point (--instance, --dry-run, --force)
```

## Quick Start

```bash
git clone https://github.com/zzz1-zzz2/condiag.git
cd condiag

# 1. Python dependencies
pip install -r requirements.txt

# 2. API key (DeepSeek V4 Pro)
echo "DEEPSEEK_API_KEY=sk-..." >> ~/.config/mini-swe-agent/.env

# 3. Docker images (52 Verified/python eval images, ~3-4GB each)
bash scripts/pull_pilot_images.sh

# 4. Verify setup
HF_DATASETS_OFFLINE=1 python3 scripts/test_branch_builder.py   # unit tests
HF_DATASETS_OFFLINE=1 python3 scripts/injection_gate.py         # 8 assertions

# 5. Run one instance
HF_DATASETS_OFFLINE=1 DEEPSEEK_API_KEY=sk-... python3 -m experiments.v2c_entry --instance astropy__astropy-13398
```

## Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| minisweagent | 2.4.1 | Agent framework (from source at `/home/swelite/swebench_study/mini-swe-agent/`) |
| swebench | 4.1.0 | Official SWE-bench harness (`run_instance()`) |
| datasets | ≥2.14 | HuggingFace dataset loading (SWE-bench_Verified) |
| pyarrow | ≥12.0 | ContextBench parquet reading |
| docker | ≥6.0 | Docker SDK for container management |
| litellm | (via minisweagent) | LLM API gateway (DeepSeek V4 Pro) |

## Docker Images

52 pre-pulled images available for Verified/python instances.

**Naming convention:**
- Eval: `swebench/sweb.eval.x86_64.{instance_id}:latest` (double underscore → `_1776_`)
- Env: `sweb.env.py.x86_64.{hash}:latest` (tagged from eval images)

**Usage:** `namespace="swebench"` in `make_test_spec()`, `force_rebuild=False`, `rm_image=False`.

## Dataset: ContextBench + SWE-bench Verified

ContextBench parquet (`ContextBench/data/full.parquet`, 25MB):
- 1136 instances across 4 sources (Verified, Pro, Multi, Poly) and 8 languages
- Gold context: `[{"file": "path", "start_line": N, "end_line": N, "content": "..."}]`
- Our subset: 99 instances in `/mnt/d/condiag-artifacts/condiag/manifests/instances_v2.jsonl`

SWE-bench Verified (HuggingFace cache, offline):
- Canonical rows with version, FAIL_TO_PASS, PASS_TO_PASS, base_commit
- InstanceRegistry merges both sources via `original_inst_id`

### Full Breakdown (99 instances)

| Source | Pool | Count | Language | Docker image |
|--------|------|-------|----------|-------------|
| Verified | first_failed | **16** | python | ✅ 52/52 |
| Verified | solved | 36 | python | ✅ |
| Pro | first_failed | 13 | python/go/javascript | ❌ needs adapter |
| Pro | solved | 3 | python/go | ❌ |
| Multi | first_failed | 8 | c/cpp/go/java/js/rust | ❌ |
| Multi | timeout/pending | 6 | c/go/java/typescript | ❌ |
| Multi | solved | 2 | cpp/rust | ❌ |
| Poly | first_failed | 2 | python/javascript | ❌ |
| Poly | solved | 10 | python/typescript/javascript/python | ❌ |
| Poly | pending | 2 | typescript | ❌ |
| **Total** | | **99** | | **52 ready** |

**Pilot set** (16 first-failed Verified/python): astropy-13398, astropy-14995,
django-11400, django-11815, django-11820, django-12663, django-13195,
django-13513, django-14140, django-14349, django-14792,
scikit-learn-25232, sympy-13852, sympy-16597, sympy-17318, sympy-20428

## Validation Status (2026-07-18)

| Component | Status | Evidence |
|-----------|--------|----------|
| R1 natural submission | ✅ | astropy-13398: 31 calls, $0.026, submitted |
| Official run_instance() | ✅ | namespace=swebench, image reuse, no pull/build |
| Checkpoint fairness | ✅ | workspace hash identical SF/CD |
| FW/Diagnosis injection | ✅ | shared build_branch_messages, gate 8/8 + unit 6/6 |
| SF/CD complete + submit | ❌ | blocked by DeepSeek long-context JSON instability |
| Django gold calibration | ✅ | empty→UNRESOLVED, gold→RESOLVED (12125) |
| ContextBench offline eval | ❌ | needs repo cache or stable git clone |

### Known Issues

1. **DeepSeek JSON format errors (~7% rate)**: Under 150K+ character context, DeepSeek V4 Pro produces unterminated JSON strings. Affects SF/CD in long conversations. Not a code bug — model limitation.

2. **Format error counter confirmed correct**: `n_consecutive_format_errors` resets on every clean step. The 8-FE death spiral at SF end is real consecutive model errors, not a counting bug.

3. **env images need manual tagging**: Eval images exist locally but env images must be tagged: `docker tag swebench/sweb.eval.x86_64.{iid}:latest sweb.env.py.x86_64.{hash}:latest`.

4. **Docker container name validation**: run_id must contain only `[a-zA-Z0-9_.-]`. Use hex SHA, not raw diff prefix.

## Repository Structure

```
condiag/
├── round1_runner.py              R1 natural submission loop (~75 lines)
├── branch_runner.py              R2 branch (SF/CD) loop (~100 lines)
├── experiment.py                 Thin orchestration (~130 lines)
├── branch_builder.py             Shared message injection (~60 lines)
├── checkpoint.py                 Snapshot save/restore (~150 lines)
├── instance_registry.py          Data loader (ContextBench + SWE-bench)
├── evaluators/
│   ├── official_harness.py       Official run_instance() wrapper
│   └── docker_swebench.py        Frozen (custom evaluator, do not use)
├── integrated_agent.py           Original ConDiagIntegratedAgent (frozen reference)
├── diagnosis_prompt_builder.py   Soft guidance prompt (stateless, rule-based)
├── paired_runner_legacy.py       Frozen prototype
├── paired_runner_prototype_v1.py Frozen prototype

experiments/
├── v2c_entry.py                  CLI entry point
└── tests/
    └── test_branch_builder.py    Unit tests (6 tests)

scripts/
├── injection_gate.py             Injection gate test (8 assertions)
├── api_smoke.py                  Single-step API smoke test
├── test_branch_builder.py        Branch builder unit tests
├── setup_new_machine.sh          Environment setup
└── pull_pilot_images.sh          Docker image puller

docs/
└── CONDIAG_HANDOFF.md            Full project knowledge (13 sections)
```

## Git Tags

| Tag | Commit | Description |
|-----|--------|-------------|
| `v2c-modular-freeze` | d0f931a | Current HEAD, modular V2c + handoff |

## Hard Rules (VIOLATION = ROLLBACK)

See `CLAUDE.md` and `docs/CONDIAG_HANDOFF.md` section 7.

- **Do NOT modify mini-SWE-agent source.**
- **Must use official `swebench.harness.run_instance()`** (namespace=swebench).
- **step_limit=0** — no artificial cutoff. Only `submitted` is a valid termination.
- **SF and CD start from same checkpoint.** Workspace hashes must match.
- **FW identical for both branches.** Only CD receives Diagnosis Instruction.
- **comparison.json always written** (try/finally), even on partial failure.
- **Format-error termination ≠ valid submission.**
- **Old artifact dirs archived, not deleted** — tag with `_invalid_` suffix.
