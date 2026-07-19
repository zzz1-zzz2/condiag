# ConDiag: Self-Diagnostic Context Management for LLM Agents

> 本文档统一了截至目前（2026-07-19）关于课题方向、架构设计、实验方案的全部讨论，是后续所有实现和论文写作的基线。修改基线需全员讨论。

---

## 1. 研究问题

### 核心问题

LLM Agent 在仓库级程序修复中，测试失败后通常只是"再试一次"或"扩大检索范围"，**不分析失败反映了什么样的信息缺失**。

### 具体现象

- AssertionError → 可能缺少相关测试逻辑
- AttributeError/TypeError → 可能缺少接口定义
- 多轮修复停在同一个位置 → 定位方向错了
- 新引入 Regression → 缺少依赖/调用关系信息

### 研究目标

> 能否利用修复过程中的失败信号，让 Agent **自我诊断**当前缺失的上下文类型，并据此指导后续的信息获取？

---

## 2. 核心方法

### 总体框架

```
Patch → Test → Failure Signal
                ↓
         [Diagnosis] ←── 核心贡献
         判断缺什么上下文
                ↓
         [Compression] ←── 使能技术
         腾出窗口，保证诊断能被消费
                ↓
         [Information Acquisition] ←── 使能技术
         根据诊断结果获取对应信息
                ↓
         Next Patch
```

### 2.1 Diagnosis（核心贡献）

**问题：** Failure → Deficiency 映射不可靠。单一异常类型不能唯一对应一种上下文缺失。

**方法：** 多特征融合软分类（LLM 推理），输入多个维度的失败信号：

| 信号 | 来源 | 示例 |
|------|------|------|
| Error type | FailureWitness | AttributeError, AssertionError |
| Stack trace top frame | FailureWitness | astropy/io/fits/hdu/image.py:397 |
| Patch edit location | Patch diff | astropy/io/fits/hdu/base.py:720 |
| Viewed files | Trajectory | agent 看过哪些文件 |
| Exploration pattern | Trajectory | focused / oscillating / jumping |
| Coverage changes | Test log | 新增 regression |

**输出：**

```json
{
  "failure_interpretation": "AttributeError suggests missing type definition...",
  "primary_deficiency": {
    "type": "API_DEFINITION",
    "confidence": 0.82,
    "evidence": ["error_type=AttributeError", "top_frame=fields/__init__.py:42"]
  },
  "secondary_deficiencies": [
    {"type": "IMPORT_CONTEXT", "confidence": 0.45}
  ],
  "rejected_assumptions": ["round1_hypothesis_about_X_is_wrong"]
}
```

**消融实验：**

| 变体 | 输入 | 方法 |
|------|------|------|
| Random | - | 随机猜 |
| Single-signal | 只用 error type | 规则一对一映射 |
| Multi-signal | 全信号融合 | LLM 推理 |
| Oracle | Gold context | 上帝视角（上界） |

### 2.2 Compression（使能技术）

**为什么需要：** 实测验证，R1 40+ 次调用后上下文膨胀，模型开始产生 JSON 格式错误，R2 无法正常跑完。

**方法：** 诊断感知的上下文预算分配

- 保留：诊断目标相关代码片段、被修改文件、失败测试输出、关键命令
- 丢弃：冗余 bash stdout、成功的历史工具调用、无关探索轨迹
- 输出：压缩后的 message history（token 量可控）

不作为独立贡献章节，放在 System Design 里。

### 2.3 Information Acquisition（使能技术）

将诊断结果翻译为具体的检索动作：

| 缺失类型 | 检索动作 |
|---------|---------|
| API_DEFINITION | 查找符号定义 |
| INTERFACE_CONSTRAINT | 阅读父类/协议 |
| RELATED_TESTS | 搜索相邻测试 |
| CALLER_CALLEE | 查找调用者/被调用 |
| DEPENDENCY | 跨模块依赖追踪 |

不作为独立贡献章节，放在 System Design 里。

---

## 3. 投稿目标：AAAI

### 叙事重述

> ConDiag: Self-Diagnostic Context Management for LLM Agents

LLM Agent 在交互过程中，其上下文窗口是有限计算资源。测试失败不只是"任务失败"的信号，更是 **Agent 知识边界（epistemic boundary）的指示器**。本文研究 Agent 能否利用这些失败信号自我诊断缺失信息、并主动管理上下文获取策略。

### 三个贡献点

1. **问题定义：** 首次将仓库修复中的测试失败形式化为 **Context Deficiency Signal**，为 Agent 自我诊断提供结构化的观测空间
2. **方法：** 多特征融合的上下文缺失推理机制，不依赖单一一对一映射规则
3. **实验：** 在 SWE-bench + ContextBench 上验证诊断有效性，含 Oracle 上界、多模型泛化、多层次 ablation

### Related Work 定位

| 方向 | 区别 |
|------|------|
| 记忆管理（MemGPT, RAG） | 被动检索，不做诊断 |
| 通用压缩（StreamingLLM） | 任务无关裁剪 |
| Self-Reflection（Reflexion） | 反思行为，不反思信息缺失 |
| Tool-use Agent（ReAct） | 默认工具结果被有效消费 |

---

## 4. 实验设计

### 基础实验矩阵

| 实验 | Diag | Compress | Acquire | 回答的问题 |
|------|------|----------|---------|-----------|
| Full Context (baseline) | ❌ | ❌ | ❌ | 当前 v4 现状 |
| w/o Diagnosis | ❌ | ✅ | ✅ 无脑 | 诊断是否有用 |
| Single-Signal Diag | ✅ 规则 | ✅ | ✅ 定向 | 一对一映射够不够 |
| Multi-Signal Diag | ✅ LLM | ✅ | ✅ 定向 | 多特征是否更好 |
| **Oracle Diag** | ✅ Gold | ✅ | ✅ 定向 | **上界** |
| w/o Compression | ✅ | ❌ | ✅ | 压缩是否必要 |
| w/o Routing | ✅ | ✅ | ❌ 全量 | 定向是否优于全量 |

### 新增 AAAI 专属实验

| 实验 | 目的 |
|------|------|
| **跨模型**（DeepSeek → Claude） | 方法不依赖模型 |
| **Oracle baseline** | 诊断理论上限 |
| **Structured Output Success Rate** | 压缩是否解决格式崩溃 |
| **Diagnosis Consumed Rate** | 诊断是否被 Agent 遵循 |

### 指标

| 维度 | 指标 | 对应模块 |
|------|------|---------|
| 修复效果 | Resolved Rate, Rescue Rate | 整体 |
| 上下文质量 | Context Precision/Recall, MER, Steps-to-Hit | Diagnosis |
| 窗口健康 | Structured Output Success Rate | Compression |
| 诊断消费 | Diagnosis Consumed Rate | 整体 |
| 成本 | Token Consumption, Tool Calls | 整体 |

---

## 5. 代码结构

```
condiag/
├── experiment.py              # 实验编排（需要扩展）
├── round1_runner.py           # ✅ 复用
├── branch_runner.py           # ✅ 复用
├── checkpoint.py              # ✅ 复用
├── branch_builder.py          # 🔧 扩展（注入压缩后上下文+诊断）
│
├── diagnosis/                 # 🆕 新建（核心贡献）
│   ├── __init__.py
│   ├── taxonomy.py            # ContextDeficiencyType 定义
│   ├── schema.py              # 数据结构
│   ├── signals/               # 信号提取（规则）
│   │   ├── error.py
│   │   ├── patch.py
│   │   └── trajectory.py
│   └── reasoner/              # 缺失推理（LLM/规则）
│       ├── base.py
│       ├── rule.py
│       └── llm.py
│
├── compression/               # 🆕 新建（使能技术）
│   ├── __init__.py
│   ├── strategy.py            # 压缩策略
│   └── budget.py              # 预算管理
│
├── acquisition/               # 🆕 新建（使能技术）
│   ├── __init__.py
│   ├── router.py              # 缺失类型→检索动作
│   └── instructions.py        # 检索指令生成
│
└── evaluation/                # 🆕 新建
    ├── __init__.py
    ├── swebench.py            # SWE-bench 评测
    ├── contextbench.py        # ContextBench 评测
    └── metrics.py             # 指标计算
```

---

## 6. 落地执行顺序

### Phase 1（当前最紧急）：Compression

**目标：** 解决上下文膨胀导致的 R2 崩溃，先跑通全流程拿到数据。

**做法：** 启发式压缩（不需要诊断），主要手段：
- 丢弃成功的 bash 命令完整输出，只保留返回码+摘要
- 保留不超过最近 N 轮交互
- 保留被修改的文件内容快照

**产出：** R1 → Compression → R2 → SWE-bench eval 可以完整跑通。

### Phase 2（核心贡献）：Diagnosis

**目标：** 实现多特征融合的缺失推理。

**做法：**
1. 先实现信号提取层（从 trajectory/FW/patch 提取特征）
2. 再实现 Rule baseline（错误类型→缺失类型映射）
3. 再实现 LLM reasoner（多特征融合推理）
4. 做 ablation 对比

### Phase 3（实验加固）：泛化验证

- 换模型（Claude）
- Oracle baseline 实验
- 写论文

---

## 7. 当前环境就绪状态

| 项目 | 状态 | 备注 |
|------|------|------|
| Python 3.11 + venv | ✅ | |
| Docker + 镜像加速 | ✅ | |
| minisweagent | ✅ | v2.4.1 |
| swebench | ✅ | v4.1.0 |
| SWE-bench data | ✅ | HF 镜像缓存 500 实例 |
| astropy-13398 镜像 | ✅ | 已拉取+打env标签 |
| DeepSeek API Key | ✅ | deepseek-v4-pro 可用 |
| SWE-bench 评测 | ✅ | 空 patch 已验证 UNRESOLVED |
| Full canary run | ❌ | R1 repeated_format_error |
| ContextBench data | ❌ | 需从旧机器迁移 |
| 其他实例镜像 | ❌ | 需拉取 |

---

## 8. 已知风险

| 风险 | 缓解 |
|------|------|
| Compression 做太复杂，拖延 Phase 1 | 第一版用启发式规则，不依赖诊断 |
| Diagnosis LLM 推理不准 | 设 Oracle baseline 兜底，至少知道上界 |
| 跨模型实验 API 成本高 | 先用 DeepSeek 跑完全部主实验，选子集做跨模型验证 |
| ContextBench 数据不可用 | 可从 SWE-bench_Verified 构造代理 gold context |
