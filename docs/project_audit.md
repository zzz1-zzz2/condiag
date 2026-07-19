# ConDiag 课题现状全览（2026-07-19）

## 一、相关文献

### 核心相关（与你的研究问题直接相关）

| 论文 | 会议/时间 | 与 ConDiag 的关系 |
|------|----------|-----------------|
| **SWE-Pruner: Self-Adaptive Context Pruning for Coding Agents** | arxiv 2601.16746, 2025 | 最贴近。在 SWE-bench agent 轨迹中做自适应上下文裁剪，压缩 40% token。但它是通用裁剪，不是诊断驱动的。 |
| **Reducing Cost of LLM Agents with Trajectory Reduction** | FSE 2025 | 发现 agent 轨迹中 environment observation 占绝大部分 token。压缩策略可降成本。 |
| **Simple Observation Masking Is as Efficient as LLM Summarization** | arxiv 2508.21433, 2025 | 简单的观察遮盖（不删命令，只删输出内容）就能达到 LLM 摘要同等效果。适合 ConDiag 的第一版 compression。 |
| **Active Context Compression: Autonomous Memory Management in LLM Agents** | arxiv 2601.07190, 2026 | Agent 自主决定保留/丢弃哪些上下文。 |
| **AI Agents Do Not Fail Alone: The Context Fails First** | arxiv 2607.14275, 2026.07（热乎） | Agent 失败不是因为模型能力，是**上下文先失效了**。直接支撑你观察到的现象。 |

### 重要相关工作（需要区分）

| 论文 | 与 ConDiag 的差异 |
|------|------------------|
| **Reflexion** (Shinn et al., 2023) | Agent 反思自己的行为，但不反思"信息缺失"。ConDiag 诊断的是知识边界，不是行为。 |
| **MemGPT** (2023) | 虚拟上下文管理，但被动分页，不做诊断 |
| **ReAct** (Yao et al., 2023) | 推理+行动的循环，但假设工具输出都被有效消费 |
| **Toolformer** (Schick et al., 2023) | 学会用工具，但不处理上下文窗口过载 |
| **StreamingLLM** (Xiao et al., 2024) | 窗口管理策略，但与任务目标无关 |

### 行业参考

| 方向 | 代表性工作 |
|------|-----------|
| SWE-bench 代表性方法 | SWE-Agent, OpenHands, Agentless, Devin |
| Self-Reflection in APR | Self-Refine, LEVER, CodeSimul |
| Agent 记忆管理 | MemGPT, RAG, AgentMemory |
| 工具调用退化 | JetBrains 2025 博客分析工具调用成瘾问题 |

---

## 二、当前运行为止发现的问题

来自实际跑 canary（astropy__astropy-13398）的观察：

| # | 问题 | 具体表现 | 严重性 |
|---|------|---------|--------|
| 1 | **上下文膨胀 → JSON 格式崩溃** | R1 40+ 次成功调用后，DeepSeek 开始连续输出格式错误，15 次后终止。这是你现在最卡脖子的工程问题。 | 🔴 阻塞 |
| 2 | **DeepSeek cost tracking 不兼容** | openai/deepseek-v4-pro 不在 litellm 模型注册表中，成本计算报错。需要用 cost_tracking=ignore_errors 绕过。 | 🟡 低 |
| 3 | **litellm 版本兼容** | 1.92.0 缺少 orjson、fastapi 等依赖，逐个安装后才能用。 | 🟢 已解决 |
| 4 | **HF 数据集网络受限** | 直连 huggingface.co 超时，需要 HF_ENDPOINT=https://hf-mirror.com 走国内镜像。 | 🟢 已解决 |
| 5 | **Docker Hub 访问慢** | 拉取 2GB 镜像需要国内镜像加速。 | 🟢 已解决 |
| 6 | **InstanceRegistry 数据缺失** | ContextBench parquet + instances_v2.jsonl 不在本地，在旧机器 /mnt/d/。 | 🟡 需迁移 |
| 7 | **多实例 Docker 镜像未拉取** | 目前只有 astropy-13398 的镜像，其他待拉取。 | 🟡 待处理 |

---

## 三、需要删除的内容

### 代码

| 路径 | 为什么删 | 处置 |
|------|---------|------|
| `condiag/trajectory_signals.py` | v2 架构的信号提取，与 diagnosis/signals/ 重复 | 迁移有用逻辑后删除 |
| `condiag/context_deficiency_diagnoser.py` | v1-v3 CDType 规则分类，被新的多特征 LLM 推理取代 | 冻结 → 归档 |
| `condiag/search_contract_builder.py` | v2 Diagnostic Search Contract，思路已过时 | 冻结 → 归档 |
| `condiag/contract_renderer.py` | Contract → Markdown，v4 已弃用 | 冻结 → 归档 |
| `condiag/integrated_agent.py` | v4 ConDiagIntegratedAgent，留作参考但不用 | 标记冻结 |
| `condiag/diagnosis_prompt_builder.py` | v4 soft diagnosis prompt，被新的 diagnosis 模块取代 | 冻结 → 归档 |
| `condiag/paired_runner_legacy.py` | v1-v3 遗留 | 已归档 |
| `condiag/paired_runner_prototype_v1.py` | v1-v3 遗留 | 已归档 |

### 文档

| 路径 | 为什么删 |
|------|---------|
| `docs/diagnosis_architecture.md` | 草稿，内容已合并进 research_baseline.md |
| `docs/evaluation_framework.md` | 草稿，内容已合并进 research_baseline.md |
| `docs/adapter_layer.md` | 已标记过时 |
| `docs/baseline_runner_design_v0.2.md` | 已标记过时 |
| `docs/condiag_architecture_v0.1_draft.md` | 已标记过时 |
| `docs/_context_packet_full.md` | 已标记过时 |
| `docs/plans/*` | 已标记过时 |
| `docs/project_state_20260710.md` | 已过时 |
| `docs/project_structure_20260710.md` | 已过时 |
| `docs/taxonomy_v0.3_three_axis_draft.md` | 已过时 |

---

## 四、需要更新/修改的内容

### 文档

| 文件 | 改动 |
|------|------|
| `CLAUDE.md` | 重写：更新课题定位（AAAI）、架构（三层）、实验设计、代码结构 |
| `docs/CONDIAG_HANDOFF.md` | 补充课题方向说明，标记为过渡文档 |
| `docs/research_baseline.md` | 已创建 ✅，后续修改以此为基线 |

### 代码

| 文件 | 改动 |
|------|------|
| `condiag/experiment.py` | 扩展：在 R1 → R2 之间插入 Compression + Diagnosis 阶段 |
| `condiag/branch_builder.py` | 扩展：支持注入压缩后上下文 + 诊断结果 |
| `condiag/round1_runner.py` | 小改：返回 trajectory 信号供 diagnosis 使用 |
| `condiag/branch_runner.py` | 小改：跟踪 Structured Output Success Rate |

### 新建

| 路径 | 内容 |
|------|------|
| `condiag/diagnosis/` | 核心贡献：信号提取 + 缺失推理 |
| `condiag/compression/` | 使能技术：上下文压缩 |
| `condiag/acquisition/` | 使能技术：检索路由 |
| `condiag/evaluation/` | 评测模块 |

---

## 五、建议的操作顺序

### 第 1 步（现在）：清理 + 更新文档

```
git rm 上述所有过时文档
重写 CLAUDE.md
```

### 第 2 步：Phase 1 — Compression 模块

```
新建 condiag/compression/
在 experiment.py 中插入压缩步骤
跑通 R1 → Compression → R2 → eval
```

### 第 3 步：Phase 2 — Diagnosis 模块

```
新建 condiag/diagnosis/
实现信号提取 → 规则 baseline → LLM reasoner
做 ablation 对比
```

### 第 4 步：Phase 3 — 实验加固

```
迁移 ContextBench 数据
跨模型验证
Oracle baseline 实验
写论文
```
