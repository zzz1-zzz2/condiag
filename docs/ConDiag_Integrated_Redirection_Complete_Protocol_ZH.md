# ConDiag 转向后完整研究与实验协议

**暂定系统名称**：Integrated ConDiag / ConDiag v4  
**研究方向**：Failure-Guided Context Diagnosis for Repository-Level Program Repair  
**宿主系统**：mini-SWE-agent（单宿主，直接修改原生工作循环）  
**文档状态**：转向后的统一设计基线  
**日期**：2026-07-15  

---

## 0. 文档目的

本文档统一 ConDiag 转向后的研究问题、系统架构、实验协议、指标、数据使用、实现边界和论文叙事，取代此前以“独立 Attempt 2 + Markdown ContextPacket + 外部注入”为核心的重试方案。

本文档需要解决以下问题：

1. 为什么原先的两次独立尝试存在结构性缺陷；
2. ConDiag 转向后究竟研究什么；
3. 如何把验证、诊断、检索和修订集成到同一个持续修复 Episode；
4. 如何使用隔离 evaluator 获得真实 FailureWitness，同时避免 oracle 泄漏；
5. Stateful Feedback 等强 baseline 应如何设计；
6. 哪些指标能够客观量化机制与最终效果；
7. 之前约 100 个实例和历史资产如何复用；
8. 哪些旧模块保留、重构或停止使用；
9. 实现顺序、实验门槛和论文研究问题如何冻结。

---

## 1. 背景与原始研究动机

现有 repository-level program repair Agent 在生成 Patch 后，可能通过测试、静态检查或外部评测发现修复仍然失败。常见处理方式包括：

- 在当前上下文中自由继续尝试；
- 将测试失败文本直接反馈给模型；
- 重新启动一次修复；
- 扩大搜索范围；
- 检索更多文件并重新生成 Patch。

这些方法通常把失败视为“再试一次”的反馈，却没有显式分析：

> 当前修复失败究竟暴露了哪一种上下文缺口，Agent 下一步应当获取什么证据？

FailureWitness 中可能包含：

- failed tests；
- assertion message；
- expected-versus-actual；
- exception type；
- stack trace；
- top repository frames；
- regression tests；
- timeout、collection error 或 runtime error；
- 当前 Patch Diff 与修改范围。

这些信号可能与缺失上下文有关。例如，AttributeError 可能与 API 定义或接口使用约束有关；AssertionError 可能暴露遗漏的行为条件或相关测试；新增 regression 可能说明当前修改忽略了调用关系、依赖或并行实现。

但是，这些关系只能作为诊断线索，不能写成确定性的异常类型映射：

```text
AttributeError ≠ 必然缺少 API Definition
TypeError      ≠ 必然缺少 Interface Constraint
AssertionError ≠ 必然缺少 Related Tests
```

真正的诊断必须联合考虑：

```text
FailureWitness + 当前 Patch + 已查看上下文 + 当前修复轨迹
```

---

## 2. 原方案的结构性问题

### 2.1 原方案

旧版流程为：

```text
Attempt 1
→ 生成 Patch
→ 外部验证失败
→ ConDiag 规则诊断与检索
→ 生成 ContextPacket.md
→ 从 clean base 启动 Attempt 2
→ 将 packet 注入新 Agent
```

### 2.2 第一次上下文丢失

Attempt 1 中，Agent 已经形成了大量无法由简短 Markdown 完整还原的状态：

- 查看过哪些文件和符号；
- 排除过哪些定位方向；
- 为什么修改当前位置；
- 当前 Patch 的设计意图；
- 已观察到哪些调用关系；
- 哪些假设已被证据支持或否定。

Attempt 2 从 clean base 重新启动后，只获得 issue、FailureWitness 和压缩后的 packet。系统一边补充缺失上下文，一边丢弃第一次已经获得的上下文，形成结构性抵消。

### 2.3 规则系统承担了语义程序理解

规则适合：

- 提取测试名、异常名和 stack frame；
- 检测修改文件数和 patch shape；
- 执行预算、去重与格式校验。

规则不适合：

- 判断 stack trace 中的症状和根因；
- 将失败与当前 Patch 意图关联；
- 判断应检查接口、测试、调用者还是并行实现；
- 生成高质量的语义修复方向。

因此，旧版“关键词规则 → 缺失类型 → Markdown 指令”的精度和鲁棒性不足。

### 2.4 Packet 是弱通信通道

历史审计已经发现 ContextPacket 可能处于 C0：Agent 没有确认、检查、推理或遵循 packet。即使 packet 内容正确，也不能保证它进入真实模型上下文并改变后续行为。

### 2.5 Restart 不等于 Resume

ConDiag 真正需要支撑的是：

```text
Resume：保留已有认知，根据新失败证据修正当前假设
```

而不是：

```text
Restart：清空状态后重新完成整个任务
```

---

## 3. 转向后的核心研究问题

新版问题定义为：

> 在一次持续的 repository repair episode 中，能否利用中间验证产生的 FailureWitness，诊断当前修复过程尚缺少的上下文，并引导同一个 Agent 有针对性地继续仓库探索和 Patch 修订？

对应的核心证据链为：

```text
Persistent Repair State
        +
Patch_1 + FailureWitness
        ↓
Context Deficiency Diagnosis
        ↓
Targeted Repository Exploration
        ↓
Patch Revision
        ↓
Final Validation
```

研究重点从“如何把 packet 喂给下一次独立尝试”转为：

1. 如何在验证失败后保留已有修复状态；
2. 如何从 FailureWitness 和当前轨迹中形成上下文诊断；
3. 如何将诊断转化为可执行的仓库探索动作；
4. 在相同状态、反馈和预算下，定向诊断是否优于自由继续、随机检索和宽泛检索。

---

## 4. 系统定位与贡献边界

### 4.1 系统定位

ConDiag 不是新的基础修复模型，也不是独立的第二个 Agent。它是嵌入 SWE-agent 原生工作循环的 failure-guided repair control mechanism。

SWE-agent 仍然负责：

- 理解 issue；
- 调用工具；
- 阅读仓库；
- 进行语义推理；
- 编辑代码；
- 生成最终 Patch。

ConDiag 负责：

- 捕获验证失败；
- 将验证结果结构化为 FailureWitness；
- 保持并组织 repair state；
- 在失败后切换到显式诊断阶段；
- 约束诊断必须基于 FailureWitness 与现有轨迹；
- 将诊断映射为目标化探索类别；
- 要求 Agent 获取证据后再修订；
- 记录诊断、检索、修改和验证之间的因果链。

### 4.2 不增加第二个 LLM

ConDiag 不额外调用一个独立 LLM。诊断阶段仍由宿主 SWE-agent 使用的同一个模型完成。

因此：

- 没有 diagnosis model 与 repair model 的能力混淆；
- 不增加额外模型训练；
- 不把 ConDiag 变成第二个修复模型；
- 消融可以比较“同一模型自由继续”和“同一模型在 ConDiag 控制阶段继续”。

### 4.3 单宿主约束

第一版只修改 SWE-agent：

- 不建立跨 Agent Adapter；
- 不同时修改 mini-SWE-agent、OpenHands 和 Agentless；
- 不为未来迁移提前设计通用兼容层；
- 不将宿主切换作为当前主线变量。

mini-SWE-agent 的历史结果仅作为前期探索、问题发现和失败动机。

---

## 5. 新版整体架构

### 5.1 Episode 而非两次 Attempt

新版单位定义为一个 persistent repair episode，内部包含两轮修复和一次中间反馈：

```text
Repair Round 1
→ Intermediate Isolated Validation
→ Diagnosis / Feedback
→ Repair Round 2
→ Final Official Evaluation
```

Round 1 与 Round 2 共享：

- 同一条原生 message history；
- 同一个工作树状态；
- 同一组已查看文件和工具结果；
- 同一个 Patch 演化过程；
- 同一个修复 Episode 标识。

### 5.2 状态机

```text
EXPLORE_AND_REPAIR
        ↓ candidate patch
INTERMEDIATE_VALIDATE
        ↓ failure
DIAGNOSE
        ↓ context route
TARGETED_EXPLORE
        ↓ evidence acquired
REVISE_PATCH
        ↓ final patch
FINAL_EVALUATE
        ↓
RESOLVED / UNRESOLVED / INVALID
```

若中间验证已通过，则不进入 ConDiag 阶段，Round 1 Patch直接进入最终确认。

### 5.3 SWE-agent 内部改动位置

对 SWE-agent 源码的改动应限制在以下控制点：

1. 将第一次 candidate submission 改为中间 checkpoint，而非立即终止；
2. 导出当前 Patch 和原生 conversation state；
3. 调用隔离验证执行器；
4. 将经过清洗的 FailureWitness 作为新的 environment observation 返回同一 Episode；
5. 切换 `phase = DIAGNOSE`；
6. 记录后续检索、推理、修改和最终提交；
7. 在 Round 2 或预算耗尽后正式终止。

不应大改：

- 原有模型调用层；
- 基础工具系统；
- Docker/SWEEnv 的通用能力；
- 与 ConDiag 无关的编辑协议；
- 官方最终 grading 逻辑。

---

## 6. 中间隔离验证

### 6.1 为什么不能在 Agent 工作区直接运行官方 evaluator

SWE-bench 的官方评测可能应用 `test_patch`，并根据 FAIL_TO_PASS 与 PASS_TO_PASS 判断结果。如果将 test patch 或 gold metadata 直接暴露到 Agent 工作区，可能泄漏：

- 隐藏测试代码；
- 精确断言行为；
- gold test location；
- benchmark 内部标签；
- 与正确修改高度相关的 oracle 信息。

### 6.2 隔离验证协议

中间验证必须在 Agent 不可访问的独立环境中执行：

```text
SWE-agent 当前 Patch
        ↓ export
隔离 evaluator 容器
        ↓
clean base + current patch + evaluator-only test patch
        ↓
执行目标测试与回归测试
        ↓
FailureWitness sanitizer
        ↓
允许字段返回原 SWE-agent Episode
```

Agent 工作区中不得出现 test patch 文件或 gold patch。

### 6.3 允许返回的 FailureWitness 字段

- validation status；
- failed test identifiers；
- exception type；
- exception message；
- assertion message；
- expected/actual（若原始测试输出自然暴露）；
- stack trace；
- top repository frames；
- timeout、collection failure、import failure；
- Round 1 新引入的 regression 标记；
- 当前 Patch Diff 的结构化摘要。

### 6.4 禁止返回的字段

- gold patch；
- gold edit location；
- test patch 源码全文；
- 人工修复说明；
- 从 gold patch 反推的修复目标；
- 直接描述应修改哪一行的 oracle 指令；
- 官方 PR、commit message 或历史修复内容。

### 6.5 研究设定声明

由于 Agent 获得一次隔离 evaluator 的中间反馈，本研究不是标准的单次 SWE-bench setting，而是：

> one persistent repair episode with one intermediate validation-feedback round

论文必须明确这一设定，并确保所有反馈型 baseline 获得完全相同的 FailureWitness。

---

## 7. Persistent Repair State

新版不通过 Markdown 重建第一次上下文，而是保留 SWE-agent 原生对话与工作树。额外的结构化 state 仅用于控制和测量，不替代原始 history。

建议记录：

```json
{
  "episode_id": "...",
  "phase": "DIAGNOSE",
  "round": 2,
  "inspected_files": [],
  "inspected_symbols": [],
  "modified_files": [],
  "modified_symbols": [],
  "commands": [],
  "validation_history": [],
  "failure_history": [],
  "diagnosis_history": [],
  "retrieval_history": [],
  "budget_usage": {}
}
```

下列语义内容不得由关键词规则擅自猜测：

- current hypothesis；
- root cause；
- edit objective；
- necessary context。

它们应由宿主 Agent 在诊断阶段显式输出，并保留证据引用。

---

## 8. Context Deficiency Taxonomy

Taxonomy 用于组织诊断和检索动作，不假设每个实例只有唯一标签。

### 8.1 主 taxonomy

1. **API Definition**：缺少类、函数、属性、协议或数据结构的定义信息；
2. **Interface Constraint**：缺少参数、返回值、继承、异常、形状或格式约束；
3. **Related Tests**：缺少相邻测试、同类行为断言或回归条件；
4. **Caller/Callee Context**：缺少上游调用者、下游实现或数据流信息；
5. **Dependency Context**：缺少跨模块依赖、导入、配置或生命周期关系；
6. **Parallel Implementation**：缺少同一接口、同类组件或其他后端中的对应实现；
7. **Registration Site**：缺少路由、注册表、插件、序列化、导出或绑定位置；
8. **Localization Direction**：当前编辑位置是症状位置，真正修改目标位于其他文件或符号。

### 8.2 不进入主 taxonomy 的内容

- **Historical Fixes**：SWE-bench 中容易泄漏历史 PR 或 gold 信息，暂不作为主类；
- **Coverage Stagnation**：coverage 工具跨仓库不稳定，仅作为可选信号；
- **Repeated Failure**：这是 failure pattern，不是上下文类型；
- **Regression Failure**：这是验证结果，可支持多个上下文诊断，不是唯一标签。

### 8.3 诊断输出要求

每次诊断至少包含：

```json
{
  "failure_interpretation": "...",
  "candidate_context_types": [
    {"type": "...", "confidence": 0.0, "evidence": ["..."]}
  ],
  "next_questions": ["..."],
  "retrieval_targets": ["..."],
  "forbidden_or_unsupported_assumptions": ["..."],
  "validation_target": "..."
}
```

诊断不能直接给出从 gold 信息推导的精确修复配方。

---

## 9. Targeted Context Routing

ConDiag 不直接生成最终 Patch，而是要求 Agent先执行与诊断一致的证据获取动作。

典型动作包括：

- 查找符号定义；
- 阅读父类、接口或协议；
- 检查 failing stack 中的 repo frames；
- 搜索调用者与被调用者；
- 阅读相邻或同类测试；
- 查找 sibling/parallel implementation；
- 查找注册、配置、导出与绑定位置；
- 比较 Round 1 修改位置与真正失败位置；
- 检查新 regression 对应的依赖路径。

在进入 `REVISE_PATCH` 之前，至少应满足：

1. 查看一个由 FailureWitness 支持的目标；
2. 说明新证据如何支持或否定 Round 1 假设；
3. 明确最终修改与失败之间的关系；
4. 指定最终需要重新验证的行为。

---

## 10. Baseline 设计

### 10.1 B0：Standard SWE-agent

- 原始 SWE-agent；
- 正常探索、修改和提交；
- 可运行其自行选择的仓库测试；
- 不获得隔离 evaluator 的中间反馈；
- Round 1 Patch直接进入最终官方评测。

作用：给出标准单次修复能力。

### 10.2 B1：Stateless Feedback Retry

- 使用同一个 Round 1 Patch 和 FailureWitness；
- 清空 Round 1 conversation；
- 从 clean base 或独立会话开始；
- 提供 issue、当前 Patch/必要摘要和相同 FailureWitness；
- 不使用 ConDiag。

作用：量化“丢失原上下文”的影响。该组是辅助消融，不是新版最强 baseline。

### 10.3 B2：Stateful Feedback（核心强 baseline）

- 从完全相同的 Round 1 checkpoint 继续；
- 保留原始 conversation 和 working tree；
- 获得与 ConDiag 完全相同的 FailureWitness；
- 仅添加简单指令：继续调查并修订当前 Patch；
- 不提供类型诊断、检索目标、编辑目标或约束。

作用：回答仅靠状态保持和失败反馈能达到什么效果。

### 10.4 B3：Stateful Random Retrieval

- 保留相同状态和 FailureWitness；
- 在相同检索预算下随机选择可访问文件或候选符号；
- 不使用诊断结果。

作用：控制“只要增加上下文就会提升”的解释。建议作为次要 baseline 或小规模机制实验。

### 10.5 B4：Stateful Broad Retrieval

- 保留相同状态和 FailureWitness；
- 按宽泛策略检索 stack 邻近文件、关键词结果或更多候选文件；
- 检索 token、工具调用和时间预算与 ConDiag 对齐；
- 不执行 typed diagnosis。

作用：比较定向路由与广泛扩张。

### 10.6 B5：Integrated ConDiag

- 保留相同 Round 1 checkpoint；
- 获得相同 FailureWitness；
- 进入显式 DIAGNOSE 阶段；
- 生成 evidence-grounded context diagnosis；
- 执行 targeted repository exploration；
- 基于新证据修订 Patch；
- 在相同 Round 2 预算下提交最终 Patch。

### 10.7 可选 Wrong Routing 干预

在小规模样本中，将正确诊断替换为不相关上下文路由，用于验证收益是否来自正确路由，而非额外 prompt 或更多动作。该组不进入大规模主表。

---

## 11. 公平性与分叉协议

### 11.1 Round 1 只运行一次

对每个实例，先运行一次 SWE-agent Round 1：

```text
Issue → SWE-agent Round 1 → candidate Patch_1 → native checkpoint
```

不得为 Stateful Feedback 和 ConDiag 分别重新跑 Round 1，否则第一次 Patch 差异会污染比较。

### 11.2 固定中间 FailureWitness

Patch_1 在隔离 evaluator 中运行一次，生成固定 FailureWitness。所有反馈型分支使用同一份不可变 witness。

### 11.3 从同一 checkpoint 分叉

分支开始时必须保持一致：

- message history；
- working tree；
- Patch_1；
- inspected files/symbols；
- FailureWitness；
- 模型、温度与随机种子设置；
- 剩余 token、工具、时间和验证预算。

### 11.4 第一版只允许一次中间反馈

主实验冻结为：

```text
Round 1 → Intermediate Evaluation → Round 2 → Final Evaluation
```

不在第一版引入无限的 Patch-Test-Diagnose 循环，以避免反馈次数、成本和停止条件成为额外变量。

---

## 12. 数据集与旧实例复用

### 12.1 约 100 个历史实例可复用的资产

可以复用：

- instance ID；
- issue description；
- repository 与 base commit；
- Docker 镜像和 repo cache；
- benchmark metadata；
- 官方 evaluator；
- 历史 test logs；
- 历史 FailureWitness，用于 parser 和机制开发；
- 旧 Patch 和轨迹，用于失败分析与动机案例。

不能直接进入新版主实验：

- mini-SWE-agent 原生 trajectory 作为 SWE-agent checkpoint；
- 旧 ContextPacket；
- 旧 retry contract；
- 旧 adapter 注入结果；
- 规则匹配产生的编辑建议；
- 不同宿主产生的 Round 1 Patch 作为配对比较起点。

### 12.2 选择偏差检查

如果这 100 个实例是在看到 mini-SWE-agent 失败后筛选出来的，它们不能作为唯一的无偏主测试集。需要：

- 明确披露筛选过程；
- 将其用于开发、验证和 failure-conditioned 分析；
- 尽可能补充一批未根据旧模型结果选择的新实例；
- 或将结论限定为“在给定失败候选池上的 rescue effectiveness”。

### 12.3 建议拆分

若约 100 个实例均可运行：

- Development：15–20；
- Validation：约 20；
- Held-out Test：其余约 60；
- 若存在 selection bias，额外加入独立抽样测试集。

Development 用于调试；Validation 用于冻结 taxonomy、prompt 和预算；Test 在方法冻结后只运行一次主实验。

### 12.4 主实验纳入条件

每个实例需满足：

- 镜像可构建或已存在；
- 基线仓库状态可运行；
- 中间 evaluator 可完成；
- Round 1 Patch 可应用；
- 可生成非空、非基础设施噪声的 FailureWitness。

Round 1 已解决的实例计入总体 Resolved Rate，但不进入 Rescue Rate 分母。

---

## 13. 研究问题（Research Questions）

### RQ1：Integrated ConDiag 能否提高最终修复效果？

比较：Standard SWE-agent、Stateful Feedback、Broad Retrieval、Integrated ConDiag。

核心指标：Resolved Rate、Rescue Rate、Unique Rescue、Failure Resolution Rate。

### RQ2：ConDiag 是否获取了更相关、更少冗余的上下文？

核心指标：Context Precision/Recall、Missing Evidence Recovery、Steps-to-Relevant-Context、Redundancy。

### RQ3：收益是否来自显式诊断，而不只是状态保持、失败反馈或更多上下文？

比较：Stateless Feedback、Stateful Feedback、Random Retrieval、Broad Retrieval、ConDiag。

核心证据：配对 rescue、同预算检索质量、Wrong Routing 小规模干预、Routing Compliance。

### RQ4：ConDiag 的成本和风险是什么？

核心指标：Additional Tokens、Tool Calls、Wall-clock、Regression Rate、Invalid Patch Rate、Patch Size。

### RQ5（分析性）：哪些 FailureWitness 特征最常支持哪些上下文路由？

该 RQ 使用统计分布和小规模人工审查，不将异常类型视为唯一标签，也不要求大规模人工 missing-context gold。

---

## 14. 核心指标定义

### 14.1 Resolved Rate

\[
\text{Resolved Rate}=
\frac{\#\text{最终通过官方评测的实例}}
{\#\text{全部评测实例}}
\]

主结果指标。

### 14.2 Rescue Rate

\[
\text{Rescue Rate}=
\frac{\#\text{Round 1失败但Round 2成功}}
{\#\text{Round 1失败且进入反馈阶段}}
\]

直接衡量中间反馈后的救回能力。

### 14.3 Pairwise Unique Rescue

报告：

- Both resolved；
- ConDiag-only resolved；
- Feedback-only resolved；
- Neither resolved。

其中 ConDiag-only 是最关键的机制结果之一，但必须同时报告 Feedback-only，避免单向选择性叙述。

### 14.4 Failure Resolution Rate

\[
\text{FRR}=
\frac{\#\text{Round 1目标失败在最终验证中消失}}
{\#\text{Round 1目标失败}}
\]

失败演化分类：

- Resolved：原失败消失且无新失败；
- Progressed：原失败减少但未完全解决；
- Stagnated：原失败基本不变；
- Regressed：出现新的回归失败；
- Crashed：语法、导入、collection 或运行时崩溃。

### 14.5 Context Precision / Recall / F1

以 ContextBench PATCH_CONTEXT 或 gold-edit context 作为代理集合 \(G\)，Round 2 实际查看的上下文为 \(R\)：

\[
P=\frac{|R\cap G|}{|R|},\quad
R_c=\frac{|R\cap G|}{|G|},\quad
F1=\frac{2PR_c}{P+R_c}
\]

按可获得程度报告 file、symbol 和 span 三个粒度。必须称为 proxy，不宣称 gold patch context 是唯一必要上下文。

### 14.6 Missing Evidence Recovery

Round 1 已查看集合为 \(R_1\)，Round 2 新查看集合为 \(R_2-R_1\)：

\[
\text{MER}=
\frac{|(R_2-R_1)\cap G|}
{|G-R_1|}
\]

该指标最直接对应“第一次遗漏的相关上下文中，第二轮找回了多少”。

### 14.7 Steps-to-Relevant-Context

记录 Round 2 开始后第几个 inspect/search 动作首次命中相关文件或符号。可报告：

- Hit@1 / Hit@3 / Hit@5；
- Mean Reciprocal Rank；
- median steps to first hit。

### 14.8 Regression Rate

\[
\text{Regression Rate}=
\frac{\#\text{Round 2引入新PASS\_TO\_PASS失败的实例}}
{\#\text{进入Round 2的实例}}
\]

### 14.9 Token Consumption

分别记录：

- Round 1 input/output tokens；
- Round 2 input/output tokens；
- diagnosis tokens；
- 总 tokens；
- additional tokens；
- tokens per rescue。

\[
\text{Tokens per Rescue}=
\frac{\text{Round 2总额外tokens}}
{\#\text{成功救回实例}}
\]

### 14.10 Tool Calls

记录：

- inspect/read；
- search/grep；
- edit；
- test；
- 其他 shell；
- 重复查看；
- 总工具调用。

### 14.11 Patch Size 与 Scope

记录：

- modified files；
- added/deleted lines；
- modified symbols；
- Round 2 新增修改文件；
- 是否撤销 Round 1 修改；
- gold-edit file overlap；
- unrelated edit ratio。

### 14.12 Invalid Patch Rate

包括：

- SyntaxError；
- ImportError；
- patch apply failure；
- test collection failure；
- 大规模符号删除；
- evaluator crash；
- 空 Patch 或不可解析输出。

---

## 15. 诊断与消费指标

### 15.1 Diagnosis Coverage

\[
\text{Diagnosis Coverage}=
\frac{\#\text{生成完整有效诊断的实例}}
{\#\text{有效FailureWitness实例}}
\]

有效诊断至少包含 interpretation、context type、evidence、retrieval target 和 validation target。

### 15.2 Routing Compliance

\[
\text{Routing Compliance}=
\frac{\#\text{实际执行的诊断相关检索动作}}
{\#\text{诊断要求的检索动作}}
\]

### 15.3 Consumption Ladder

保留此前 C0–C5 的思想，但降为机制分析：

- C0：忽略；
- C1：确认；
- C2：检查目标上下文；
- C3：基于新证据修订假设；
- C4：按诊断约束完成修改；
- C5：完成重新验证并修复。

主文可报告 C2+、C3+、C4+ Rate，完整分布放附录。

---

## 16. 人工分析方案

### 16.1 不进行大规模 Missing Context Gold 标注

原因：

- 单实例理解成本高；
- 标签可能多义；
- gold patch 不等于唯一必要上下文；
- 两名标注者的仓库理解成本过高；
- 容易让论文退化为主观 taxonomy 分类任务。

### 16.2 小规模机制审查

建议抽取 20–40 个分层实例，两名标注者回答：

1. 诊断是否受到 FailureWitness 和现有 trajectory 支持？
2. 检索方向是否与诊断一致且合理？
3. 检索到的证据是否实际被后续修复使用？

评分：

- 0：不支持；
- 1：部分支持；
- 2：明确支持。

报告一致性系数与分歧解决方式。该结果只作为机制补充，不作为主结论。

### 16.3 深度案例

建议选择 4–6 个案例：

- ConDiag 独有救回；
- Stateful Feedback 与 ConDiag 都救回；
- Feedback 独有救回；
- 诊断合理但最终修复失败；
- 错误诊断导致错误路由；
- Broad Retrieval 成本高而 ConDiag 更高效。

---

## 17. 主论文指标集合

主表控制在以下八项：

| 维度 | 指标 | 方向 |
|---|---|---|
| 最终效果 | Resolved Rate | 越高越好 |
| 救回能力 | Rescue Rate | 越高越好 |
| 配对优势 | ConDiag-only / Feedback-only Rescue | 前者高且后者低更好 |
| 失败演化 | Failure Resolution Rate | 越高越好 |
| 上下文质量 | Context Precision / Recall | 越高越好 |
| 缺失证据恢复 | Missing Evidence Recovery | 越高越好 |
| 安全性 | Regression Rate | 越低越好 |
| 成本 | Additional Tokens / Tool Calls | 越低越好 |

附录指标：

- Steps-to-Relevant-Context；
- Routing Compliance；
- C2+/C3+/C4+；
- Patch Size；
- Wall-clock；
- Invalid Patch Rate；
- 人工 Support Rate；
- evaluator timeout/crash。

---

## 18. 统计分析

### 18.1 成功率

- 报告比例及 95% bootstrap confidence interval；
- ConDiag 与 Stateful Feedback 使用配对 McNemar 检验；
- 同时报告 absolute improvement 和 relative improvement。

### 18.2 连续成本指标

对 tokens、tool calls、steps 和 time：

- 优先报告 median、IQR 和 mean；
- 使用配对 Wilcoxon signed-rank test；
- 报告效应量；
- 多项比较时使用 Holm 校正。

### 18.3 随机性

主实验优先固定温度和配置。若 API 仍存在随机性：

- 在验证集上评估多 seed 方差；
- 主测试集至少保存 seed/请求标识；
- 资源允许时对核心方法做 3 次重复或在代表性子集上重复。

---

## 19. 每实例日志 Schema

每个实例必须保存原始记录，而非只保存汇总指标：

```json
{
  "instance_id": "...",
  "dataset": "...",
  "host_agent_commit": "...",
  "method_commit": "...",
  "model": "...",
  "seed": 0,
  "budgets": {
    "round1_tokens": 0,
    "round2_tokens": 0,
    "round2_tool_calls": 0,
    "wall_clock_seconds": 0
  },
  "round1": {
    "trajectory_path": "...",
    "checkpoint_path": "...",
    "patch_path": "...",
    "viewed_files": [],
    "viewed_symbols": [],
    "modified_files": [],
    "modified_symbols": [],
    "tool_calls": [],
    "token_usage": {},
    "runtime_seconds": 0
  },
  "intermediate_validation": {
    "status": "failed",
    "failed_tests": [],
    "passed_tests": [],
    "new_regressions": [],
    "failure_witness_path": "...",
    "raw_log_path": "...",
    "sanitizer_version": "...",
    "runtime_seconds": 0
  },
  "round2": {
    "method": "stateful_feedback|random|broad|condiag",
    "diagnosis_path": "...",
    "retrieved_files": [],
    "retrieved_symbols": [],
    "modified_files": [],
    "modified_symbols": [],
    "tool_calls": [],
    "token_usage": {},
    "patch_path": "...",
    "runtime_seconds": 0
  },
  "final_validation": {
    "resolved": false,
    "fail_to_pass": {},
    "pass_to_pass": {},
    "new_regressions": [],
    "invalid_reason": null,
    "raw_log_path": "...",
    "runtime_seconds": 0
  },
  "derived_metrics": {}
}
```

所有可变 prompt、配置、commit、镜像标识和 evaluator 版本必须记录。

---

## 20. 主结果表建议

### 表 1：总体修复效果

| Method | Round-1 Resolved | Final Resolved | Rescue Rate | ConDiag/Method Unique | Regression Rate |
|---|---:|---:|---:|---:|---:|

### 表 2：上下文检索质量

| Method | File Precision | File Recall | Symbol Recall | MER | Steps-to-Hit | Redundancy |
|---|---:|---:|---:|---:|---:|---:|

### 表 3：成本与安全性

| Method | Add. Tokens | Tool Calls | Eval Time | Modified Files | Invalid Patch Rate |
|---|---:|---:|---:|---:|---:|

### 表 4：配对分支结果

|  | Feedback Resolved | Feedback Unresolved |
|---|---:|---:|
| ConDiag Resolved | Both | ConDiag-only |
| ConDiag Unresolved | Feedback-only | Neither |

---

## 21. 实施路线

### Phase 0：冻结协议

- 冻结本文件；
- 冻结宿主为 SWE-agent；
- 冻结一次中间反馈；
- 冻结 FailureWitness 允许/禁止字段；
- 冻结主 baseline 和预算原则；
- 停止继续扩展旧 packet 路线。

### Phase 1：拉取并固定 SWE-agent

- clone 官方源码；
- 记录 upstream commit；
- 建立 `condiag-integrated` 分支；
- 在未修改版本上跑通一个原生任务；
- 定位 candidate submission、trajectory、workspace 和终止控制点。

### Phase 2：实现原生 checkpoint 与恢复

- 第一次 candidate submission 时保存原生 history；
- 保存 working tree/patch；
- 验证恢复后消息与工作树不丢失；
- 不生成 Markdown summary 作为恢复载体。

### Phase 3：实现隔离 evaluator

- 输入 Patch_1；
- 在隔离容器运行测试；
- 输出 raw log 和 sanitized FailureWitness；
- 验证 Agent 无法读取 test patch 与 gold metadata；
- 记录 evaluator 版本和超时。

### Phase 4：实现 Stateful Feedback

- 从 checkpoint 恢复；
- 注入相同 FailureWitness；
- 自由继续 Round 2；
- 验证同一 trajectory 和 working tree 被保留；
- 把它作为第一强 baseline，而不是先实现 ConDiag。

### Phase 5：实现 DIAGNOSE 阶段

- 添加 phase state；
- 定义结构化诊断输出；
- 强制 evidence citation；
- 定义最小 targeted exploration requirement；
- 完成后进入 Patch revision。

### Phase 6：小规模 pilot

使用 5–10 个开发实例验证：

- 中间验证可运行；
- FailureWitness 不泄漏；
- Stateful Feedback 能继续；
- ConDiag 诊断被消费；
- 两个分支预算一致；
- 日志足以离线计算指标。

### Phase 7：验证集冻结

使用约 20 个实例：

- 冻结 prompt；
- 冻结 taxonomy；
- 冻结 Round 2 token/tool/time budget；
- 冻结 random/broad 策略；
- 冻结停止条件；
- 方法冻结后不得再针对测试实例修改。

### Phase 8：正式测试

- 运行共享 Round 1；
- 缓存 checkpoint 和 witness；
- 运行全部分支；
- 执行最终官方评测；
- 离线计算指标和统计检验；
- 抽取人工机制分析样本。

---

## 22. 阶段门槛（Gates）

### Gate A：状态保持

- 恢复后 conversation history 完整；
- working tree 与 Patch_1 一致；
- Agent 能引用 Round 1 已查看证据；
- 无 Markdown 重建依赖。

### Gate B：验证隔离

- evaluator 可运行；
- test patch 不进入 Agent 工作区；
- FailureWitness 可复现；
- 无 gold patch/location 泄漏。

### Gate C：反馈公平

- Stateful Feedback 与 ConDiag 使用同一 checkpoint；
- 同一 FailureWitness；
- 同一 Round 2 预算；
- 同一最终评测。

### Gate D：诊断消费

- 至少达到 C2：实际查看目标上下文；
- 能够记录诊断到工具动作的链接；
- 不存在旧版 C0 式“生成但未进入轨迹”。

### Gate E：机制信号

在验证集上至少出现以下之一：

- ConDiag 提高 Missing Evidence Recovery；
- 降低 Steps-to-Relevant-Context；
- 降低无关检索；
- 产生相对于 Stateful Feedback 的 unique rescue。

若 Gate E 完全不成立，不应直接投入大规模主实验。

---

## 23. 主要风险与应对

### 风险 1：收益全部来自状态保持

应对：Stateful Feedback 是核心强 baseline。若两者相同，应诚实结论为 state persistence 有效，typed diagnosis 未显示增量。

### 风险 2：收益全部来自隐藏测试反馈

应对：所有反馈型 baseline 共享完全相同 FailureWitness；明确研究为 interactive validation-feedback setting。

### 风险 3：ConDiag 退化为额外 prompt 或更多 token

应对：匹配 Round 2 token、tool call、wall-clock 和检索预算；加入 Broad/Random 或 Wrong Routing 分析。

### 风险 4：诊断 taxonomy 主观

应对：不将大规模分类准确率作为主指标；主要测量路由行为、检索质量和最终修复效用。

### 风险 5：gold context 代理不等于必要上下文

应对：始终称为 proxy；结合最终 rescue、Failure Resolution 和小规模人工机制审查。

### 风险 6：中间 evaluator 成本过高

应对：第一版只运行一次中间验证；缓存 Round 1 checkpoint 与 witness；分支复用同一验证结果。

### 风险 7：旧 100 实例存在选择偏差

应对：检查抽样来源；用于 failure-conditioned rescue 时明确限定；补充独立未筛选实例。

### 风险 8：持续上下文过长

应对：首先保留原生历史，不提前引入复杂 memory；记录上下文截断行为；若确有必要再做与各 baseline 一致的通用压缩。

### 风险 9：错误假设被持续保留

应对：DIAGNOSE 阶段要求显式指出被 FailureWitness 否定的假设，并记录 hypothesis revision，而不是只追加信息。

---

## 24. 旧模块处置

### 保留并重构

- FailureWitness parser；
- validation log 收集；
- stack trace/repo frame 提取；
- patch diff 与 scope 统计；
- token、trajectory、tool-call 统计；
- evidence budget/dedup/diversity 中可验证的通用逻辑；
- Consumption Ladder 的行为测量思想。

### 仅作历史分析

- 旧 seed cases；
- 旧 ContextPacket；
- 旧 plain_rerun/feedback_retry 结果；
- 旧 5R 流程名称；
- 旧 rescue audit。

### 停止进入新版主线

- 独立 Attempt 2；
- 从 clean base 重启作为 ConDiag 主方法；
- Markdown ContextPacket 作为核心通信方式；
- MinisweRetryInjectionAdapter；
- 基于异常名称的一对一 taxonomy 规则；
- 额外 ConDiag LLM；
- 在当前阶段构建跨 Agent Adapter；
- 将历史 PR 或 gold patch 作为检索来源。

---

## 25. 论文贡献叙事

建议的贡献表述：

1. **问题定义**：提出 persistent repair episode 中的 Failure-Guided Context Diagnosis 问题，研究验证失败暴露的上下文缺口，而非简单增加上下文；
2. **方法**：设计嵌入 SWE-agent 原生工作循环的 Integrated ConDiag，在保留 repair state 的同时，将 FailureWitness 转化为 evidence-grounded diagnosis 与 targeted repository exploration；
3. **受控实验**：通过共享 Round 1 checkpoint、共享 FailureWitness 和相同预算，将 state persistence、simple feedback、broad retrieval 与 typed diagnosis 的作用分离；
4. **机制分析**：从 repair outcome、missing-evidence recovery、routing compliance、regression 和 cost 多层量化诊断是否真正被消费并产生修复效用。

不建议表述为：

- “我们开发了一个全面优于现有系统的新 Agent”；
- “每种异常唯一对应一种上下文缺失”；
- “ConDiag 能准确预测唯一 missing context gold”；
- “标准 SWE-bench 单次设定下的直接可比 SOTA”。

---

## 26. 最终冻结决策

1. 宿主选择 **SWE-agent**；
2. 直接修改其原生 repair loop，不建立 Adapter；
3. 使用 **一个 persistent episode + 一次中间隔离验证 + 一次 Round 2 修订**；
4. 不再使用独立 Attempt 2 和 Markdown ContextPacket 作为主方法；
5. ConDiag 不增加第二个 LLM，语义诊断由宿主同一模型完成；
6. 中间 evaluator 与 Agent 工作区隔离；
7. Stateful Feedback 是最重要的强 baseline；
8. 所有反馈型方法共享同一 Round 1 checkpoint 和 FailureWitness；
9. Resolved Rate、Rescue Rate、Context Retrieval、Regression 和 Cost 构成主证据链；
10. 不进行大规模主观 Missing Context 标签标注，仅做 20–40 个实例的机制审查；
11. 旧约 100 个实例及镜像继续复用，但新版 SWE-agent Round 1 必须重新运行；
12. 若旧实例按 mini-SWE-agent 失败筛选，需补充独立样本或限定结论；
13. 在验证集 Gate E 未出现机制信号前，不直接进行昂贵的大规模实验。

---

## 27. 一句话总结

新版 ConDiag 不再尝试把第一次失败压缩成一个低质量 Markdown 包交给失忆的第二个 Agent，而是在同一条 SWE-agent 修复 Episode 中保留原始轨迹和工作树，通过一次隔离验证获得 FailureWitness，再以显式上下文诊断控制后续仓库探索与 Patch 修订；其有效性必须在相同状态、相同反馈和相同预算下，相对于 Stateful Feedback 与 Broad Retrieval 得到验证。
