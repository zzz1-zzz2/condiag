# ConDiag v4 — Project Handoff

## 1. Research Question

**Core claim:** ConDiag improves repair outcomes not by retrieving more context, but by keeping the same repair agent alive across intermediate validation, using the failure signal to guide targeted re-exploration (via a soft diagnosis prompt) within the same episode.

**Not:** two independent attempts. **Not:** a separate retrieval step. **Not:** CDType classification (v1-v3 approach, frozen).

## 2. Contribution

| Component | What it does | Status |
|-----------|--------------|--------|
| **Persistent repair episode** | Same agent object, same environment, same message history across Round 1 → Round 2 | ✅ V2b |
| **Stateful Feedback (baseline)** | Round 1 submitted → official eval → FailureWitness injected → agent continues | ✅ V2b |
| **ConDiag** | Same as SF + Diagnosis Instruction prompt (soft guidance, no CDType) | ✅ V2b |
| **Official SWE-bench harness** | Thin layer wrapping `swebench.harness.run_evaluation.run_instance()` | ✅ V2c |
| **Checkpoint fairness** | Same R1 messages, workspace, FW hash for both SF and CD | ✅ V2c.1 |
| **Branch injection** | Shared `build_branch_messages()` used by both gate test and runner | ✅ finalized |

## 3. Architecture

```text
                      ┌─────────────────────────┐
                      │  round1_runner.py        │
                      │  step_limit=0            │
                      │  Wait for Submitted      │
                      └───────────┬──────────────┘
                                  │ R1 patch
                                  ▼
                      ┌─────────────────────────┐
                      │  official_evaluator.py   │
                      │  run_instance()          │
                      │  namespace="swebench"    │
                      │  force_rebuild=False     │
                      └───────────┬──────────────┘
                                  │ FW (if unresolved)
                                  ▼
                      ┌─────────────────────────────┐
                      │   build_branch_messages()    │
                      │   tool_resp + FW [+diag]     │
                      └───────────┬─────────────────┘
                          ┌───────┴───────┐
                          ▼               ▼
                  ┌──────────────┐  ┌──────────────┐
                  │ branch_runner │  │ branch_runner │
                  │ mode="sf"     │  │ mode="condiag"│
                  │ step_limit=0  │  │ step_limit=0  │
                  └───────┬──────┘  └───────┬──────┘
                          ▼               ▼
                  ┌──────────────┐  ┌──────────────┐
                  │ official eval │  │ official eval │
                  └──────────────┘  └──────────────┘
                          ▼               ▼
                      ┌─────────────────────────┐
                      │  experiment.py           │
                      │  comparison.json         │
                      │  try/finally always      │
                      └─────────────────────────┘
```

## 4. Key Design Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Agent framework | mini-SWE-agent v2.4.1 | Already used by baseline, inheritable |
| LLM | DeepSeek V4 Pro (via liteLLM) | Only provider with good API at reasonable cost |
| Eval harness | Official `swebench.harness.run_instance()` | NOT custom evaluator. Images tagged with `namespace="swebench"` |
| Step limit | `step_limit=0` (unlimited) | Agent must submit naturally. No artificial R1 cutoff |
| Cost limit | `cost_limit=3.0` | Safety limit, usually not hit |
| Wall time limit | 1800s (30 min global, shared across R1 + SF + CD) | Enforced via `_start_time` preserved in checkpoint |
| Format error tolerance | 15 consecutive | DeepSeek occasionally produces bad JSON |
| R2 counter | `n_calls` preserved from checkpoint | Shared budget, not reset |
| Image format | `swebench/sweb.eval.x86_64.{iid}:latest` | With `swebench/` prefix, namespace="swebench" |
| Env images | Tagged from eval images | `docker tag swebench/sweb.eval...:latest sweb.env.py.x86_64.{hash}:latest` |
| Harness eval caching | Unique `run_id` per patch | `run_id = f"{branch}_{patch_sha}_{timestamp}"` |

## 4. Setup & Configuration

### 4.1 Docker Images

52 Verified/python eval images exist locally. Env images must be tagged from them:

```bash
# For each pilot instance, find env image key and tag:
python3 -c "
from condiag.instance_registry import InstanceRegistry
from swebench.harness.test_spec.test_spec import make_test_spec
import json, docker

client = docker.from_env()
reg = InstanceRegistry()
done = set()

for spec in reg.list_pilot():
    sb = spec._swebench_row
    swe_inst = dict(instance_id=sb['instance_id'], repo=sb['repo'], version=sb['version'],
        base_commit=sb['base_commit'], test_patch=sb['test_patch'],
        FAIL_TO_PASS=sb.get('FAIL_TO_PASS','[]'), PASS_TO_PASS=sb.get('PASS_TO_PASS','[]'))
    tspec = make_test_spec(swe_inst, namespace='swebench')
    ek = tspec.env_image_key
    ik = tspec.instance_image_key
    if ek in done:
        continue
    done.add(ek)
    try:
        img = client.images.get(ek)
        print(f'Exists: {ek}')
    except:
        client.images.get(ik).tag(ek)
        print(f'Tagged: {ik.split("/")[-1][:30]} → {ek.split(":")[0][-30:]}')
"
```

All eval images follow: `swebench/sweb.eval.x86_64.{instance_id}:latest`
Where instance_id uses `__` (double underscore), e.g. `django__django-11820`.
Double underscores are encoded as `_1776_` in Docker tag: `django_1776_django-11820`.

### 4.2 ContextBench Data

Parquet file: `ContextBench/data/full.parquet` (25MB)
Columns: instance_id, original_inst_id, repo, repo_url, language, base_commit,
gold_context, patch, test_patch, problem_statement, f2p, p2p, source

Gold context is JSON: `[{"file": "path/to/file.py", "start_line": N, "end_line": N, "content": "..."}]`

### 4.3 SWE-bench Instance Mapping

Each of our 99 instances maps via `original_inst_id` → SWE-bench instance_id.
The SWE-bench Verified dataset is cached at HuggingFace cache dir, or loaded via:
```python
from datasets import load_dataset
ds = load_dataset('princeton-nlp/SWE-bench_Verified', split='test')
```

InstanceRegistry merges both sources: ContextBench for gold_context, SWE-bench for
version, FAIL_TO_PASS, PASS_TO_PASS, environment_setup_commit.

### 4.4 API Key

```bash
echo 'DEEPSEEK_API_KEY=sk-...' >> ~/.config/mini-swe-agent/.env
```

### 4.5 Offline Mode

```bash
export HF_DATASETS_OFFLINE=1    # prevent HuggingFace retry loops during eval
```

## 5. Dataset

## 6. Code Structure

```
condiag/
├── round1_runner.py         ← R1 natural submission loop
├── branch_runner.py          ← R2 branch (SF/CD) loop
├── experiment.py             ← Thin orchestration
├── branch_builder.py         ← Shared message injection (gate + runner)
├── checkpoint.py             ← Snapshot save/restore
├── diagnosis_prompt_builder.py  ← Soft guidance prompt (stateless)
├── integrated_agent.py       ← ConDiagIntegratedAgent (legacy, frozen for reference)
├── evaluators/
│   ├── official_harness.py   ← Thin `run_instance()` wrapper (MUST USE)
│   └── docker_swebench.py    ← Frozen (custom evaluator, do NOT use)
├── paired_runner_legacy.py   ← Frozen (replaced by experiment.py)
├── paired_runner_prototype_v1.py ← Frozen (older version)
├── instance_registry.py      ← Data loader (ContextBench + SWE-bench)
└── branch_builder.py         ← build_branch_messages()

experiments/
├── v2c_entry.py              ← CLI entry point
└── tests/
    └── test_branch_builder.py ← Unit tests (6 tests, all pass)

scripts/
├── injection_gate.py         ← V2c.1a gate test
├── api_smoke.py              ← V2c.1b API smoke test
├── setup_new_machine.sh      ← Environment setup
└── pull_pilot_images.sh       ← Docker image puller
```

## 7. Critical Constraints (VIOLATION = ROLLBACK)

1. **Do NOT modify mini-SWE-agent source.** All ConDiag code is in `condiag/`.
2. **MUST use official `swebench.harness.run_instance()`** for evaluation. No custom `docker exec` eval logic.
3. **Must use `namespace="swebench"`** when calling `make_test_spec()`.
4. **Run_id must be unique per evaluation** to avoid cached report pollution.
5. **SF and CD must start from the same checkpoint.** Workspace hashes must match.
6. **Both branches receive the same FailureWitness.** Only CD receives the Diagnosis Instruction.
7. **Step limit must be 0** (unlimited). No artificial cutoff. Only `Submitted` counts as valid R1.
8. **`comparison.json` must always be written** (in `try/finally`), even on partial failure.
9. **Format-error termination is NOT a valid submission.** Only `submitted` counts.
10. **Old artifact dirs must be archived, not deleted.** Tag with `_invalid_` suffix and `.invalid_reason.json`.

## 8. Bugs Found & Fixed

| # | Issue | Fix |
|---|-------|-----|
| 1 | `_trajectory_info()` returned dict instead of TrajectorySnapshot | Return typed dataclass |
| 2 | Test mock_evaluator double-counting | Static witness |
| 3 | Heredoc in `bash -c` for eval | Use `docker cp` from temp file |
| 4 | DeepSeek rejects `role="exit"` | Strip exit-role messages before sending to API |
| 5 | FW/diag NOT injected (inject_fw/inject_diag params unused) | Moved injection to `build_branch_messages()` |
| 6 | `tool_call_id` extraction looked at wrong field | Check both top-level `tool_calls` and `extra.actions` |
| 7 | Duplicate tool response injection | `build_branch_messages()` checks for existing via `tool_call_id` |
| 8 | `run_id` shared across evals → cached report | Unique `run_id = f"{branch}_{patch_sha}_{timestamp}"` |
| 9 | `env_image` missing | `docker tag` from eval image |
| 10 | `paired_runner.py` too large (single class does everything) | Split into 6 modules |

## 9. Current State (2026-07-17)

```
V2c   Engineering validation  ✅
  R1 natural submission       ✅ (tested on astropy-13398, 31 calls)
  Official eval               ✅ (namespace="swebench", force_rebuild=False)
  Checkpoint fairness         ✅ (workspace hash identical across SF/CD)
  FW injection                ✅ (shared build_branch_messages)
  Diagnosis injection         ✅ (CD gets extra message)
  comparison.json always      ✅ (try/finally)
  Image reuse                 ✅ (local images, no pull/build)

V2c.1a Injection gate         ✅ (8/8 assertions, shared function)
V2c.1b API smoke              ✅ (SF 1 step, CD 1 step, both accepted)

Known issues:
- DeepSeek has ~7% JSON format errors (unterminated string in tool_call args)
- Agent needs R1 submitted naturally, ~10-30 DeepSeek calls typical
- contextbench evaluate needs git clone (network unreliable)
```

## 10. Next Steps

1. **Check CD format error** → `condiag/trajectory.json`: Is diagnosis instruction causing output format errors?
2. **V2d ContextBench offline eval** → Connect trajectory metrics (needs stable git clone for repo checkout)
3. **5 dev pilot** → Run on astropy-13398, django-11400, etc. with real LLM
4. **MER/MRP** → Compare gold context vs Round 2 visited files

## 12. New-Machine Migration

### Do NOT copy these (machine-local, not project)

```
~/.claude/                          # Claude Code state
~/.claude.json                       # OAuth, credentials
~/.claude/.credentials.json
~/.claude/projects/<project>/*.jsonl # conversation history
~/.claude/file-history/              # raw file read history
~/.claude/debug/
~/.claude/paste-cache/
~/.claude/image-cache/
~/.claude/plugins/                   # re-install
```

The hardcoded DeepSeek API key in this repo's history MUST be rotated in the
provider dashboard. New key: read from env var only.

### Must-migrate (project content)

```
/home/swelite/condiag/                # ENTIRE workspace (git + untracked artifacts)
ContextBench/data/full.parquet        # 25MB, gold contexts
/min/d/condiag-artifacts/condiag/manifests/instances_v2.jsonl
```

### Migration package layout

```text
condiag-migration/
├── condiag.bundle                 # git bundle create condiag.bundle --all
├── data/
│   ├── contextbench.parquet
│   └── instances_99.json
├── artifacts/                      # last successful run (optional, for verification)
├── environment/
│   ├── versions.txt
│   ├── pip-freeze.txt
│   ├── git-commits.txt
│   ├── docker-images.txt
│   └── env.example                 # variable NAMES only, no values
└── docker/
    └── pilot-images.tar             # docker save (optional, see 4.3)
```

### New-machine recovery order

```text
1. Install Ubuntu, Git, Docker, Python 3.10+, VS Code
2. Install mini-SWE-agent from source matching commit
3. Restore git bundle: git clone condiag.bundle
4. Restore ContextBench parquet + instance manifest
5. pip install -r requirements.txt
6. bash scripts/setup_new_machine.sh
7. bash scripts/pull_pilot_images.sh  # or restore from docker/pilot-images.tar
8. Configure API key (env var, NOT hardcoded)
9. cd condiag && python3 scripts/test_branch_builder.py  # unit tests
10. python3 scripts/injection_gate.py  # injection gate (8 assertions)
11. Run a single canary instance, verify R1 + SF + CD all termination_reason=submitted
12. THEN run 5 dev pilot
```

## 11. Versions



```
ConDiag v4
Commit: 9bc3f25 (master)
Python: 3.10.12
mini-SWE-agent: 2.4.1 (installed from source at /home/swelite/swebench_study/mini-swe-agent/)
SWE-bench (harness): 4.1.0
ContextBench: local copy at /home/swelite/condiag/ContextBench/
DeepSeek: V4 Pro (via liteLLM)

Docker images: 52 Verified/python eval images
  naming: swebench/sweb.eval.x86_64.{instance_id}:latest
  env images tagged: 8 unique sweb.env.py.x86_64.{hash}:latest
```
