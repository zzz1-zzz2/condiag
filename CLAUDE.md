# ConDiag 项目

## 首先阅读

@docs/research_baseline.md

## 项目定位

**AAAI 投稿方向：** ConDiag: Self-Diagnostic Context Management for LLM Agents

核心研究问题：LLM Agent 在仓库级程序修复中，能否利用测试失败信号**自我诊断**当前缺失的上下文类型，并据此指导后续的信息获取。

## 核心架构（三层）

```
Patch → Test → Failure Signal
                ↓
         [Diagnosis] ←── 核心贡献
         多特征融合推理缺失上下文类型
                ↓
         [Compression] ←── 使能技术
         诊断感知的上下文预算分配
                ↓
         [Acquisition] ←── 使能技术
         诊断驱动的定向检索
                ↓
         Next Patch
```

## 硬性约束

- **不改 mini-SWE-agent 源码。** 所有 ConDiag 代码在 `condiag/` 下。
- **必须用官方 `swebench.harness.run_instance()`** 评测（`namespace="swebench"`）。
- **`comparison.json` 必须在 `try/finally` 中写出。**

## 投稿目标

| 项目 | 内容 |
|------|------|
| 会议 | AAAI |
| 主标题 | ConDiag: Self-Diagnostic Context Management for LLM Agents |
| 核心贡献 | Diagnosis（多特征融合的缺失推理） |
| 使能技术 | Compression + Acquisition（System Design） |
| 核心创新 | 测试失败 → Context Deficiency Signal 的形式化 |
| 实验重点 | 诊断 ablation、Oracle 上界、多模型泛化 |

## 关键指标

| 维度 | 指标 | 对应模块 |
|------|------|---------|
| 修复效果 | Resolved Rate, Rescue Rate | 整体 |
| 上下文质量 | Context Precision/Recall, MER, Steps-to-Hit | Diagnosis |
| 窗口健康 | Structured Output Success Rate | Compression |
| 诊断消费 | Diagnosis Consumed Rate | 整体 |
| 成本 | Token Consumption, Tool Calls | 整体 |

## 代码结构

```
condiag/
├── round1_runner.py           # R1 自然提交（复用 v4）
├── branch_runner.py           # R2 分支逻辑（复用 v4）
├── experiment.py              # 实验编排（需扩展）
├── checkpoint.py              # 状态保存/恢复（复用 v4）
├── branch_builder.py          # 消息注入（需扩展）
│
├── diagnosis/                 # 🆕 核心贡献
│   ├── taxonomy.py            # ContextDeficiencyType
│   ├── schema.py              # 数据结构
│   ├── signals/               # 信号提取（规则）
│   └── reasoner/              # 缺失推理（LLM/规则）
│
├── compression/               # 🆕 使能技术
│   ├── strategy.py            # 压缩策略
│   └── budget.py              # 预算管理
│
├── acquisition/               # 🆕 使能技术
│   ├── router.py              # 缺失类型→检索动作
│   └── instructions.py        # 检索指令生成
│
├── evaluation/                # 🆕 评测
│   ├── contextbench.py        # ContextBench 评测
│   └── metrics.py             # 指标计算
│
├── evaluators/
│   └── official_harness.py    # SWE-bench 评测（复用）
│
├── instance_registry.py       # 数据加载（复用）
├── schemas.py                 # 现有 schema（冻结）
├── trajectory_signals.py      # 待迁移到 diagnosis/signals/
├── context_deficiency_diagnoser.py  # 冻结（v1-v3）
├── search_contract_builder.py       # 冻结（v2）
├── contract_renderer.py             # 冻结（v2）
├── integrated_agent.py              # 冻结（v4）
└── diagnosis_prompt_builder.py      # 冻结（v4 soft diagnosis）
```

## 实验对比矩阵

| 实验 | Diag | Compress | Acquire | 回答的问题 |
|------|------|----------|---------|-----------|
| Full Context (baseline) | ❌ | ❌ | ❌ | 当前 v4 现状 |
| w/o Diagnosis | ❌ | ✅ | ✅ 无脑 | 诊断是否有用 |
| Single-Signal Diag | ✅ 规则 | ✅ | ✅ 定向 | 一对一映射够不够 |
| Multi-Signal Diag | ✅ LLM | ✅ | ✅ 定向 | 多特征是否更好 |
| **Oracle Diag** | ✅ Gold | ✅ | ✅ 定向 | 上界 |
| w/o Compression | ✅ | ❌ | ✅ | 压缩是否必要 |
| w/o Routing | ✅ | ✅ | ❌ 全量 | 定向是否优于全量 |

## 环境

| 项目 | 状态 |
|------|------|
| Python 3.11 + venv | ✅ |
| Docker | ✅ |
| minisweagent v2.4.1 | ✅ |
| swebench v4.1.0 | ✅ |
| SWE-bench data（HF 镜像） | ✅ |
| DeepSeek API Key | ✅ |
| astropy-13398 镜像 | ✅ |
| SWE-bench 评测 | ✅ 已验证 |
| Compression → R2 跑通 | ❌ Phase 1 |
| Diagnosis 模块 | ❌ Phase 2 |
| ContextBench data | ❌ 需迁移 |

## 执行 Plan

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | Compression 模块 + 跑通 R1→R2 | 🔜 当前 |
| Phase 2 | Diagnosis 模块（信号 + 推理 + ablation） | ⏳ 待开始 |
| Phase 3 | 跨模型验证 + Oracle baseline + 写论文 | ⏳ 待开始 |

## 冻结模块（不修改）

| 模块 | 原因 |
|------|------|
| `condiag/context_deficiency_diagnoser.py` | v1-v3 规则 CDType，被新架构取代 |
| `condiag/search_contract_builder.py` | v2 Diagnostic Search Contract，思路已过时 |
| `condiag/contract_renderer.py` | v2 Contract→Markdown，已弃用 |
| `condiag/integrated_agent.py` | v4 ConDiagIntegratedAgent，参考冻结 |
| `condiag/diagnosis_prompt_builder.py` | v4 soft diagnosis，被新 diagnosis 模块取代 |
| `condiag/trajectory_signals.py` | 有用逻辑待迁移到 diagnosis/signals/，迁后冻结 |
| `condiag/evaluators/docker_swebench.py` | 自定义 evaluator，禁止使用 |
