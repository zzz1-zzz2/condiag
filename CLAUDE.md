# ConDiag Project

## Reference (READ FIRST)

@docs/CONDIAG_HANDOFF.md

## Hard Constraints (VIOLATIONS = ROLLBACK)

- Do not modify mini-SWE-agent source.
- Use the official SWE-bench harness (`run_instance()`), not custom `docker exec`.
- SF and ConDiag must start from the same Round 1 snapshot (`branch_builder`).
- Both receive the same FailureWitness. Only ConDiag receives the Diagnosis Instruction.
- `step_limit=0` — no artificial cutoff. Only `submitted` is a valid termination.
- Do not count a format-error termination as a valid final submission.
- `comparison.json` must always be written (try/finally).
- Show git diff after every code change.

## Project Identity

ConDiag v4: **Failure-Guided Context Diagnosis via Persistent Repair Episodes**.
Research codebase at `/home/swelite/condiag/`. Artifacts at `/mnt/d/condiag-artifacts/condiag/`.

**Core claim:** ConDiag improves repair outcomes not by retrieving more context, but by keeping the same repair agent alive across intermediate validation, using the failure signal to guide targeted re-exploration (via soft diagnosis prompt) within the same episode.

**v4 核心变化（2026-07-15）：**
- 从两次独立 Attempt → 1 persistent episode
- 从 Markdown ContextPacket 注入 → 保留原生 message history + working tree
- 从外部 CDType 规则 → 宿主同一模型在 DIAGNOSE phase 显式推理
- 从外部 retry runner → `ConDiagIntegratedAgent(DefaultAgent)` 嵌入原生 loop

## Experiment Setting (NON-NEGOTIABLE)

Post-Validation / Interactive Feedback Repair. 不是标准 hidden-test SWE-bench pass@1.

**已验证**（V1b ✅）：
```
Round 1 → Submits (echo COMPLETE_TASK)
→ 拦截 Submitted → 补 tool response（API 协议）
→ 调 evaluator(patch) → sanitized FailureWitness
→ inject FW → optionally inject Diagnosis prompt
→ Round 2（同一 agent 对象，同一 environment）
→ Submits → final
```

**未验证**（V2 ⏳）：
- Docker 隔离 evaluator（当前用 subprocess）
- 完整 Round 1 → evaluator → Round 2 在一个 SWE-bench 实例上跑通
- ContextBench 轨迹评测后处理
- Stateful Feedback vs ConDiag 对比

## Architecture (v4)

```text
/home/swelite/condiag/           ← 唯一代码位置（WSL）
├── condiag/
│   ├── __init__.py                    v4 package (v1-v3 frozen exports kept)
│   ├── integrated_agent.py           ConDiagIntegratedAgent(DefaultAgent)  ✅
│   ├── diagnosis_prompt_builder.py   DiagnosisPromptBuilder (stateless)    ✅
│   ├── isolated_evaluator.py         subprocess + sanitizer + evidence     ✅
│   ├── instance_registry.py          InstanceRegistry (CB + SWE-bench)     ✅ V2c
│   ├── checkpoint.py                 CheckpointManager (capture/restore)   ✅ V2c
│   ├── paired_runner.py              PairedRunner (orchestration)          ✅ V2c
│   └── evaluators/
│       ├── docker_swebench.py        Custom evaluator (frozen)
│       └── official_harness.py       OfficialHarnessGateway (fallback)      ✅ V2c
├── experiments/
│   ├── v4_loop_test.py               Mock 测试                             ✅
│   ├── v4_real_flow.py               真实 DeepSeek + LocalEnvironment      ✅
│   ├── v2_entry.py                   V2 单实例入口                         ⏳
│   └── v2c_entry.py                  V2c paired run entry point            ✅ V2c
├── docs/
│   ├── condiag_v2_design.md          V2 工程设计
│   ├── django11820_motivation.md     Motivation example
│   └── django11820_failure_analysis.md v1 失败分析
└── archive/
    └── condiag_v1/                   v1-v3 旧架构（冻结）
```

## Hard Rules (VIOLATIONS = ABORT)

### Gold Leakage (Absolute Ban)

以下内容永不允许进入 agent-facing 输出：

- gold patch / gold context / resolved label / gold file timeline
- ContextBench oracle metrics (file_cov, span_cov, EditLoc)
- F2P/P2P 作为 benchmark 标签（说 "validation test"）
- feedback_success_patch / manual_hindsight_only
- 任何来自 gold patch 的编辑指令

### Diagnosis Module Boundary

`DiagnosisPromptBuilder` 只做规则提取 + 文本模板填充。

| 可以做 | 不可以做 |
|---|---|
| 提取 failed test names | 规则匹配 error_type → CDType |
| 提取 error message + stack frames | 代替 agent 判断缺失上下文类型 |
| 提取 stack frame 中未访问的文件 | 输出 "此错误是 API Definition 缺失" 等分类标签 |
| 格式化 6 段式 soft prompt | 输出 Required/Required 等硬约束 |

语义诊断推理必须由 host agent 自身的 LLM 在 DIAGNOSE phase 完成。

### Patch Generation

- patch 必须来自 `git diff HEAD`
- 不多 LLM 输出直接当 patch
- `patch_source` 必须是 `workspace_git_diff`

### Success Case Policy

- Round 1 resolved 的 55 实例不进入 rescue pool
- ConDiag 只在 39 个 first-failed 实例上评估
- Rescue Rate 分母 = 39，不是 94

## 主要指标

| 名称 | 公式 | 状态 |
|---|---|---|
| Resolved Rate | #最终通过 / #全部 | 主结果 |
| Rescue Rate | #R1失败但R2成功 / #R1失败 | 核心机制 |
| MER | \|N₂ ∩ (G \ C₁)\| / \|G \ C₁\| | 缺失证据恢复 |
| MRP | \|N₂ ∩ (G \ C₁)\| / \|N₂\| | 检索精度伴侣 |
| Regression Rate | #round2 新 P2P 失败 / #进入 round2 | 安全指标 |
| Additional Tokens/Cost | round2 - round1 | 效率指标 |
| ContextBench P-R-F1 | File / Symbol / Span / Line | 轨迹质量（辅助） |

## 数据集状态

**94 instances in manifest:**
- 39 first-failed → ConDiag rescue pool
- 55 first-resolved → baseline only

**ContextBench gold availability:**
- 99/99 mapped via `original_inst_id`
- 5 dev instances all have gold context
- 20 batch3 all have gold context

**Instances in SWE-bench Lite:** 12 (subset)

## 当前执行阶段

```
V1b（Persistent Repair Loop）✅
  Real LLM + LocalEnvironment 跑通
  ConDiagIntegratedAgent + DiagnosisPromptBuilder + isolated_evaluator 完成

V2a（隔离 evaluator 验证）⏳
  isolated_evaluator.py 编译通过、子进程 backend 待实战
  v2_entry.py 待跑通一个实例

V2b（完整实例验证）⏳
  从 SWE-bench Lite 数据集自动读实例参数
  Round 1 → 真实 DeepSeek → 真实 evaluator → Round 2 → ContextBench

V3（多实例对比）⏳
  Stateful Feedback vs ConDiag
  3-5 dev 实例 pilot → 扩到 39 first-failed
```

## 关键文件映射

| 模块 | WSL 路径 | 状态 |
|---|---|---|
| ConDiagIntegratedAgent | `condiag/integrated_agent.py` | ✅ V1b |
| DiagnosisPromptBuilder | `condiag/diagnosis_prompt_builder.py` | ✅ V1b |
| Isolated Evaluator | `condiag/isolated_evaluator.py` | ✅ 编译通 |
| Instance Manifest | `/mnt/d/condiag-artifacts/condiag/manifests/instances_v2.jsonl` | ✅ |
| V2 Entry | `experiments/v2_entry.py` | ⏳ |
| Trajectory Analyzer | (待写) | ❌ |
| V2 Batch | (待写) | ❌ |

## 旧模块处置

| 模块 | 处置 |
|---|---|
| `condiag/trajectory_signals.py` | 冻结，v4 不再调用 |
| `condiag/context_deficiency_diagnoser.py` | 冻结，v4 不再调用 |
| `condiag/search_contract_builder.py` | 冻结，v4 不再调用 |
| `condiag/contract_renderer.py` | 冻结，v4 不再调用 |
| `experiments/baseline_handlers.py` | 冻结，v4 不再调用 |
| `experiments/host_agent_retry_runner.py` | 冻结，v4 不再调用 |
| `experiments/contract_compliance_analyzer.py` | 冻结，v4 不再调用 |
| `experiments/failure_witness_builder.py` | 冻结（FW schema 可复用） |
