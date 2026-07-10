# ConDiag v2 质量打磨阶段 —— 计划

> 日期：2026-07-10
> 前序：FailureWitness v2.0 输入规范化已完成（39 first-failed, 16 eligible, 0 generic, 100% high+medium）
> 当前分支：`context-deficiency-v1`

---

## 一、项目现状（真实盘点）

### 1.1 已冻结

| 模块 | 状态 |
|---|---|
| `failure_parsers/` (12 files) | **FROZEN v2.0** — ANSI 剥离 + stage authority + Mocha JSON |
| `rebuild_witnesses_v2.py` | **DONE** — official_eval 优先于 raw_log |
| `test_parsers.py` | **DONE** — 15 个回归测试 |

### 1.2 ConDiag 核心（存在但未经过系统审计）

| 模块 | 文件 | 行数 | 真实状态 |
|---|---|---|---|
| `trajectory_signals.py` | `condiag/trajectory_signals.py` | 727 | **EXISTS** — Phase 0 产物，未在真实数据上跑过 |
| `search_contract_builder.py` | `condiag/search_contract_builder.py` | 761 | **EXISTS** — Phase 0 产物，未做批量 audit |
| `contract_renderer.py` | `condiag/contract_renderer.py` | 219 | **EXISTS** — Phase 0 产物 |
| `schemas.py` | `condiag/schemas.py` | 440 | **EXISTS** — 含 FailureWitness / DiagnosticSearchContract |
| `leakage_guard.py` | `condiag/leakage_guard.py` | 109 | **EXISTS** — gold leakage 检测 |
| `failure_witness_builder.py` | `experiments/failure_witness_builder.py` | 263 | **DONE** — v2 builder，与 parsers 对接 |
| `baseline_handlers.py` | `experiments/baseline_handlers.py` | 1204 | **EXISTS** — 5 baseline interventions |
| `host_agent_retry_runner.py` | `experiments/host_agent_retry_runner.py` | 1024 | **EXISTS** — attempt-2 runner |
| `baseline_runner.py` | `experiments/baseline_runner.py` | 289 | **EXISTS** — 编排层 |
| `instance_manifest.py` | `experiments/instance_manifest.py` | 304 | **EXISTS** |
| `experiment_settings.py` | `experiments/experiment_settings.py` | 159 | **EXISTS** |

### 1.3 关键缺口

| 模块 | CLAUDE.md 声称 | 实际 | 影响 |
|---|---|---|---|
| `context_deficiency_diagnoser.py` | DONE | **MISSING** | 无法生成 CDType 诊断 → Blocking |
| `contract_compliance_analyzer.py` | TODO | **MISSING** | 无法衡量 agent 是否执行 contract |

**结论：context_deficiency_diagnoser.py 是实现 ConDiag 闭环前必须填补的缺口。**

### 1.4 遗留代码（需归档）

以下文件属于旧架构（ConDiag-does-retrieval），不应再修改：

| 文件 | 替换者 |
|---|---|
| `condiag/context_packet_builder.py` | `search_contract_builder.py` |
| `condiag/diagnosis_generator.py` | `context_deficiency_diagnoser.py`（待实现） |
| `condiag/diagnosis_normalizer.py` | — |
| `condiag/retrieval_executor.py` | agent tool loop |
| `condiag/api_navigation.py` | agent tool loop |
| `condiag/action_planner.py` | agent tool loop |
| `condiag/evidence_selector.py` | — |
| `condiag/manual_retrieval.py` | — |
| `condiag/manual_guard.py` | — |
| `condiag/edit_support_checker.py` | — |
| `condiag/patch_scope_analyzer.py` | — |
| `condiag/patch_prune_suggester.py` | — |
| `condiag/repo_resolver.py` | — |
| `condiag/seed_regression.py` | — |
| `condiag/scope_guard.py` | — |
| `condiag/scope_guard_executor.py` | — |
| `condiag/repository_index.py` | — |
| `condiag/cli.py` | — |
| `condiag/report.py` | — |
| `condiag/trigger.py` | — |
| `condiag/loader.py` | — |

### 1.5 实例池

```
39 first-failed
├── 16 eligible validation failures (ConDiag 实验池)
│   ├── 3 high quality (django-11820, django-13513, ripgrep-1367)
│   └── 13 medium quality
├── 18 infrastructure / non-validation (excluded)
└── 5 patch-apply / missing-log (excluded)

16 eligible 分布:
  7 SWE-bench Verified (python)
  8 SWE-bench Multi   (6 ansible + 1 NodeBB + 1 axios)
  1 SWE-bench Poly    (serverless)
  
  Parser: 6 pytest, 6 ansible, 3 mocha_jest, 1 cargo_test
  Lang:   12 python, 3 js, 1 rust
```

---

## 二、执行计划（8 步）

### 批次 1：补缺口 + 项目治理

#### Step 1：实现 `context_deficiency_diagnoser.py`

Claude.md 说 DONE 但实际上不存在。需要按当前 schema 实现：
- 输入：FailureWitness + TrajectorySignals
- 输出：CDType 评分（7-type classification）
- 来源：`condiag/context_deficiency_diagnoser.py`

**Gate：** 离线验证 5 个 dev case 上 CDType 非空、非坍缩、支持信号可追溯。

#### Step 2：项目结构清理

- 归档旧文件到 `archive/` 目录
- 更新 `__init__.py` 只暴露新架构模块
- 更新 CLAUDE.md 反映真实状态
- 提交 git + 推送到 remote

### 批次 2：冻结 + 分组 + 批量诊断

#### Step 3：冻结 FailureWitness Builder v2.0

- 冻结声明（代码注释 + docs）
- 产出现状摘要 JSON（39 实例的状态矩阵）
- manifest 字段：`failure_stage`, `failure_witness_quality`, `parser_name`, `eligible_for_condiag`, `ineligibility_reason`

#### Step 4：划分 Dev / Held-out

Dev Pool（6 个，用于打磨）：

| 实例 | Benchmark | 语言 | Parser | Quality | 选择理由 |
|---|---|---|---|---|---|
| django-11820 | Verified | python | pytest | high | Python + pytest 主流场景 |
| django-13513 | Verified | python | pytest | high | 另一个 high quality |
| sympy-20428 | Verified | python | pytest | medium | medium quality 代表 |
| ansible-8127ab | Multi | python | ansible | medium | Ansible 生态 |
| axios-5661 | Multi | js | mocha | medium | JS 生态 |
| ripgrep-1367 | Verified | rust | cargo | high | Rust 生态 |

剩余 10 个为 Held-out Pool，ConDiag 冻结前不允许针对单例修改。

#### Step 5：批量生成 16 个 ConDiag 产物

统一生成每个 eligible 实例的：
- `trajectory_signals.json`
- `context_deficiency_diagnosis.json`
- `search_contract.json`
- `rendered_contract.md`

### 批次 3：审计 + 合规分析

#### Step 6：Diagnosis + Contract 质量审计

两张表：
- **Diagnosis Audit** — CDType 分布、confidence、supporting signals
- **Contract Quality Audit** — 动作数量、target 存在率、gold leakage、冗余度

#### Step 7：实现 `contract_compliance_analyzer.py`

路径：`experiments/contract_compliance_analyzer.py`

功能：对 Attempt-2 trajectory，识别 contract 中每个 action 的完成状态：
- explicit / covered / ignored / not_applicable

先用 Attempt-1 trajectory 做 matcher 单元测试（预期全部 ignored 或 not_applicable）。

### 批次 4：Dev Pilot

#### Step 8：6 个 Dev 上跑四组 Attempt-2

```text
plain_rerun
feedback_retry
broad_expansion_retry
condiag_contract_retry
```

每组的输出：

1. **Compliance** —— Agent 执行了什么 contract actions
2. **ContextBench** —— ΔFile/Block/Symbol/Line F1，ΔAUC-Coverage
3. **Official Eval** —— rescue 与否

### 批次 5：打磨 + 冻结

#### Step 9：根据 Pilot 系统性打磨

禁止单例特判。只允许基于多例系统性现象的修改。

例：
- 多例忽略 required_searches → renderer 表达不够强
- ROOT_CAUSE 实例编辑旧文件 → relocalization contract 需加强
- 大量 target 不存在 → target resolver 改进
- agent 执行了 contract 但 ContextBench 不提升 → diagnosis/action profile 有问题

#### Step 10：冻结 ConDiag v2.2

```text
FailureWitness Builder v2.0 — FROZEN
ConDiag v2.2 — FROZEN
Search Contract Schema v2.2 — FROZEN
Contract Renderer v1.0 — FROZEN
Compliance Analyzer v1.0 — FROZEN
```

跑 10 个 Held-out，对外报告。

---

## 三、版本标记

| 产物 | 版本 | 冻结时间 |
|---|---|---|
| FailureWitness Builder | v2.0 | 当前 |
| Failure Parsers | v2.0 | 当前 |
| ConDiag Diagnosis | v2.1-dev | Step 1 |
| Search Contract | v2.1-dev | Step 5 |
| Contract Renderer | v1.0-dev | Step 5 |
| Compliance Analyzer | v1.0-dev | Step 7 |
| ConDiag (全栈) | v2.2 | Step 10 |

---

## 四、Gate 清单

| Step | Gate | 通过条件 |
|---|---|---|
| 1 | CDType 验证 | 5 dev case 上 CDType 非空、非坍缩、gold_leakage=0 |
| 2 | 清理完成 | git clean build、旧文件归档、CLAUDE.md 匹配实际 |
| 3 | Witness 冻结 | 39 实例 JSON 状态表、manifest 更新 |
| 4 | 分组完成 | Dev 6 / Held-out 10，分组理由记录 |
| 5 | 批量生成 | 16 实例全部有 4 个产物 |
| 6 | 审计完成 | Diagnosis + Contract 两张表，问题列表 |
| 7 | Compliance 可用 | 单元测试通过，Attempt-1 全部 ignored |
| 8 | Pilot 完成 | 6×4=24 runs 完成 |
| 9 | 打磨完成 | 修改均有系统性证据 |
| 10 | 冻结 + Held-out | 10 组结果 + 最终报告 |

---

## 五、当前立即执行

1. ~~审计 conflict 范围~~ ✅
2. ~~修 stage authority~~ ✅
3. ~~增强 Mocha 检测~~ ✅
4. [ ] **实现 context_deficiency_diagnoser.py** (Blocking)
5. [ ] 项目结构清理 + git 提交
6. [ ] 冻结 Witness v2.0 + 分组
7. [ ] 批量生成 + 审计
8. [ ] 下游步骤继续...
