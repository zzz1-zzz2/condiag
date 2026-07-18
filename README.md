# ConDiag v4 — Failure-Guided Context Diagnosis via Persistent Repair Episodes

ConDiag v4 keeps the same repair agent alive across intermediate validation:
**Round 1 → intercept submission → official SWE-bench eval → FailureWitness →
Round 2 (same agent, same messages) → final evaluation.**

## Quick Start

```bash
# Requirements
pip install -r requirements.txt

# Setup (API key, Docker images)
bash scripts/setup_new_machine.sh
bash scripts/pull_pilot_images.sh

# Run one instance
HF_DATASETS_OFFLINE=1 DEEPSEEK_API_KEY=sk-... python3 -m experiments.v2c_entry --instance astropy__astropy-13398
```

## Architecture

```
round1_runner.py    ← natural submission loop (step_limit=0, wait for Submitted)
branch_runner.py    ← forked R2 (SF: FW only | CD: FW + Diagnosis)
experiment.py       ← thin orchestration: R1 → eval → fork → eval → comparison
branch_builder.py   ← shared message injection (gate + runner use same code)
checkpoint.py       ← snapshot save/restore (messages, workspace, counters)
official_harness.py ← thin wrapper: make_test_spec(ns=swebench) → run_instance()
```

## Validation Status (2026-07-18)

| Component | Status |
|-----------|--------|
| R1 natural submission | ✅ (astropy-13398, 31 calls, submitted) |
| Official run_instance() | ✅ (namespace=swebench, image reuse, no rebuild) |
| Checkpoint fairness | ✅ (workspace hash identical SF/CD) |
| FW/Diagnosis injection | ✅ (shared build_branch_messages, gate + runner) |
| SF/CD complete + evaluate | ❌ (blocked by DeepSeek long-context JSON instability) |
| ContextBench offline eval | ❌ (needs repo cache) |

See @docs/CONDIAG_HANDOFF.md for full project knowledge.

## Repository

```
condiag/             V2c core (round1_runner, branch_runner, experiment, …)
experiments/         Entry points (v2c_entry.py)
scripts/             Gate tests, API smoke, unit tests, setup
docs/                CONDIAG_HANDOFF.md + legacy docs
```

Current commit: `v2c-modular-freeze` (tag)
