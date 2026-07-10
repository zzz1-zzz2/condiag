# ConDiag 项目完整状态 — 2026-07-10

---

## 一、课题定义

**一句话**：ConDiag 是一个 **Failure-Guided Diagnostic Search Controller**，输出 **Diagnostic Search Contract** 来引导 Agent 在 Attempt-2 中更精准地搜索代码。ConDiag 不做检索、不写 patch。

**实验设置**：**Post-Validation / CI-Feedback Repair**，非标准 hidden-test SWE-bench pass@1。

**核心蜕变的起点**：
- v1：ConDiag 自己做检索、产 context packet → 实验证明增量价值为 0（Task 6-beta 0/7 rescue）→ 废弃
- v2：ConDiag 输出 Search Contract，Agent 自己执行检索 → 主指标从 repair rate 转为 ContextBench trajectory metrics

---

## 二、架构管线（输入 → 输出）

```
Attempt-1 ↓
Validation failure output ↓
  → Failure Witness Builder
    → Trajectory Signals (error-edit alignment, exploration mode, ...)
      → Context Deficiency Diagnoser (CDType 7-type scores)
        → Search Contract Builder (JSON contract)
          → Contract Renderer (Markdown)
            → Host Agent Attempt-2 (contract-guided tool use)
              → workspace_git_diff patch
                → Official SWE-bench Eval (resolved/failed)
                → ContextBench Eval (File/Block/Symbol/Line P-R-F1)
                  → Contract Compliance Analyzer (explicit/covered/ignored)
```

### 模块状态

| 模块 | 位置 | 状态 |
|------|------|------|
| experiment_settings.py | experiments/ | ✅ DONE (2026-07-10) |
| instance_manifest.py | experiments/ | ✅ DONE (99 instances) |
| trajectory_signals.py | condiag/ | ✅ DONE (未验证实际运行) |
| context_deficiency_diagnoser.py | condiag/ | ✅ DONE (CDType v2.1-dev) |
| search_contract_builder.py | condiag/ | ✅ DONE (未验证实际运行) |
| contract_renderer.py | condiag/ | ✅ DONE (2026-07-10, 已测试输出) |
| failure_witness_builder.py | experiments/ | ✅ DONE (WSL 侧, 9 个 witness) |
| baseline_handlers.py | experiments/ | ✅ DONE (WSL 侧) |
| host_agent_retry_runner.py | experiments/ | ✅ DONE (WSL 侧) |
| contract_compliance_analyzer.py | experiments/ | ⬜ TODO |

---

## 三、数据集现状（99 实例）

### 3.1 各 Benchmark 分布

| Benchmark | 总量 | Solved | First-Failed | Timeout | Pending | 说明 |
|-----------|------|--------|-------------|---------|---------|------|
| Verified | 52 | 36 | 14 | 0 | 2 | django/sympy/astropy/scikit-learn |
| Pro | 15 | 2 | 13 | 0 | 0 | ansible/NodeBB/flipt 等 |
| Multi | 15 | 4 | 8 | 3 | 0 | ripgrep/cli/jq/express 等 |
| Poly | 15 | 13 | 2 | 0 | 0 | transformers/keras/langchain 等 |
| **Total** | **99** | **55** | **39** | **3** | **2** | |

Pending: element-web (Pro-style), mui-34337 (Multi-style) — 已跑 ContextBench，缺 official eval。

### 3.2 Pipeline 数据覆盖率

| 数据层 | 存量 | 缺口 |
|--------|------|------|
| ContextBench metrics | 99 ✅ | 0 |
| Official eval result | 97 ✅ | 2 (element-web, mui-34337) |
| Trajectory (raw_trajectory.json) | 14 ✅ | 25 first-failed 缺 trajectory |
| Failure witness | 9 ✅ | 30 first-failed 缺 witness |
| Trajectory signals | 0 ⬜ | 全部 39 first-failed 待生成 |
| Search contract | 0 ⬜ | 全部 39 first-failed 待生成 |
| Attempt-2 results | 0 ⬜ | 全部 39 first-failed 待跑 |

---

## 四、要证明的核心问题（实验设计）

### Figure 1 — Motivation: 为什么 agent 需要 ConDiag
- CDType 分布（哪些 deficiency 最常见）
- Error-edit alignment 分布（agent 编辑位置命中率 — 多数 alignment != aligned）
- Exploration mode 分布（focused vs oscillating vs jumping vs shallow_scan）
- **核心论点**：Agent 修复失败不是因为不会写代码，而是 **search 阶段找错了位置**

### Figure 2 — Method: ConDiag 架构图
- Attempt-1 → Failure Witness → Runtime Signals → CDType → Contract → Agent
- 强调 ConDiag 不做检索，只引导搜索

### Figure 3 — Search Contract 实例
- 一个真实 instance 的 Contract JSON → Markdown 渲染
- 显示 Contract 如何转化为 agent 的搜索行为改变
- 对比 attempt_1 的轨迹和 attempt_2 的轨迹差异

### Figure 4 — Trajectory Shift: Attempt-1 vs Attempt-2
- 所有 baseline 的 ΔContext F1 对比
- File/Block/Symbol/Line P-R-F1 的变化
- 分 benchmark 子图
- **核心论点**：ConDiag 引导的搜索在 trajectory 层面显著改变 agent 行为

---

### Table 1 — Dataset Statistics
| 列 | 来源 |
|----|------|
| Total instances, per benchmark split | instance_manifest |
| # repos, languages | instance_manifest |
| EditLoc distribution (min, max, avg) | ContextBench gold |
| Resolved vs first-failed | official eval |

### Table 2 — Attempt-1 ContextBench Baseline
**范围**：55 个 solved 实例的 attempt_1
**目的**：建立论文对照基线，表明 context metric 随 EditLoc 增加而下降的 baseline

| Metric | 测量方式 |
|--------|---------|
| File P-R-F1 | Gold 编辑文件 vs agent 编辑文件 |
| Block P-R-F1 | Gold 编辑块 vs agent 编辑块 |
| Symbol P-R-F1 | Gold 编辑符号 vs agent 编辑符号 |
| Line P-R-F1 | Gold 编辑行 vs agent 编辑行 |
| ΔContext F1 | Search 范围 vs gold 范围 |

### Table 3 — Main Results: Retry Trajectory Metrics（核心）
**范围**：39 first-failed × 4 core baselines（后期 +2 ablation）
**要证明**：condiag_contract 在 ContextBench 指标上显著 > feedback_retry > plain_rerun

| Baseline | ΔFile F1 | ΔBlock F1 | ΔSymbol F1 | ΔLine F1 | ΔContext F1 |
|----------|---------|-----------|-----------|---------|------------|
| plain_rerun | ? | ? | ? | ? | ? |
| feedback_retry | ? | ? | ? | ? | ? |
| broad_expansion | ? | ? | ? | ? | ? |
| **condiag_contract** | **?** | **?** | **?** | **?** | **?** |
| random_expansion | ? | ? | ? | ? | ? |
| rehydrate_only | ? | ? | ? | ? | ? |

**要回答的问题**：
- Contract 的 required_inspections 是否比无 guidance 更命中 gold？
- contract-guided agent 的 exploration_mode 是否更聚焦？
- ΔContext F1（搜索范围与 gold 范围的重叠度）是否更高？

### Table 4 — Repair Outcome（传统 resolved/failed）
**要证明**：ConDiag 救回更多 first-failed 实例

| Baseline | Rescue | No Rescue | P2P Regression | Rescue Rate |
|----------|--------|-----------|---------------|-------------|
| plain_rerun | ? | ? | ? | ? |
| feedback_retry | ? | ? | ? | ? |
| broad_expansion | ? | ? | ? | ? |
| **condiag_contract** | **?** | **?** | **?** | **?** |
| random_expansion | ? | ? | ? | ? |
| rehydrate_only | ? | ? | ? | ? |

**已知 pilot 结果（n=1 django-12125，仅参考）**：
- plain_rerun: F2P 过但 P2P 退步 4 个（过度修复）
- feedback_retry: ✅ rescue
- broad_expansion: F2P 没过（broad context 可能干扰）
- condiag_retry_v2_alpha: ✅ rescue（完整 packet + 失败分析）

Task 6-beta（django-11820, django-13513, ×4 baselines）: 0/7 rescue

### Table 5 — Ablation: Contract Compliance
**目的**：哪些 contract 组件真正被 agent 执行了

| Component | Explicit | Covered | Ignored | Compliance Rate |
|-----------|----------|---------|---------|-----------------|
| Required Inspections | ? | ? | ? | ? |
| Required Searches | ? | ? | ? | ? |
| Anti-Patterns | ? | ? | ? | ? |
| **Overall** | ? | ? | ? | **?** |

---

## 五、实验已证明的教训

### 5.1 v0 pilot（5 实例，旧 ConDiag 自己做检索）
- feedback_retry: 2/4 rescue
- condiag_retry (v1): 1/4 rescue
- condiag_unique: 0（ConDiag 救回的实例 feedback 都能救）
- **结论**：v1 ConDiag 做检索没有增量价值

### 5.2 Task 6-alpha（n=1, django-12125）
- 4/4 baselines completed
- 2/4 rescue（feedback_retry ✅, condiag_retry_v2_alpha ✅）
- plain_rerun: F2P 过但 P2P 退步 4 个（过度修复）
- broad_expansion: F2P 未过（context 过多干扰）
- **结论**：feedback 本身就能救人，ConDiag 要证的是 unique rescue

### 5.3 Task 6-beta（n=2, django-11820 + django-13513, ×4 baselines）
- 0/7 rescue（所有 baseline 全部失败）
- 根因：EOFError（agent 工具调用错误后无法恢复）
- **结论**：retry runner 需要容错，EOFError 是第一杀手

### 5.4 NOOP 假阳性（django-15863）
- Agent 报告 resolved 但实际上是凑巧修好
- ContextBench 指标揭示：agent 编辑位置与 gold 位置不匹配
- **结论**：resolved 标签不够，必须看 trajectory metrics

### 5.5 retry_no_change（django-13513）
- Agent 在收到 failure feedback 后也不改变策略
- 同样的文件、同样的搜索模式、"不同"的 patch 但实际一样
- **结论**：纯 feedback 不够，需要更结构化的 Contract 引导

---

## 六、6 Baseline 的输入对比

| Baseline | Issue | Failure Witness | Contract | Broad Context | Rehydrate Only |
|----------|-------|----------------|----------|---------------|----------------|
| plain_rerun | ✅ | ❌ | ❌ | ❌ | ❌ |
| feedback_retry | ✅ | ✅ | ❌ | ❌ | ❌ |
| broad_expansion | ✅ | ✅ | ❌ | ✅ | ❌ |
| **condiag_contract** | **✅** | **✅** | **✅** | **❌** | **❌** |
| random_expansion | ✅ | ✅ | ❌ | random | ❌ |
| rehydrate_only | ✅ | ✅ | ❌ | ❌ | ✅ |

控制变量：model, temperature, max_steps, timeout, clean base 全部相同。

---

## 七、当前数据缺口和最紧迫的下一步

### 补数据优先级

```
P0: 在已有的 14 个 trajectory + 9 个 failure witness
    上跑完整管线（signals → CDType → contract），验证输出质量
    └── 这决定 ConDiag v2 是不是真的有效，能不能进 Phase 2

P1: 补齐 39 first-failed 中缺失的 trajectory（25 个缺口）
    定位已有的 traj 数据的存放位置（batch3/batch5 产物里可能有）

P2: 补齐 failure witness（30 个缺口）
    experiments/failure_witness_builder.py 已就绪

P3: 官方 eval 剩余的 2 个（element-web, mui-34337）
    给 99 实例画上句号

P4: 然后才是 Phase 2 完整实验（6 baseline × 39 first-failed）
```

### 已齐备可以直接开始的

1. ✅ 14 个 instance 有 trajectory + failure witness（见下）
2. ✅ 管线代码全部就位
3. ✅ Manifest 已建好，路径统一
4. ✅ 产物的新目录结构就绪

### 可直接跑的 14 个实例

| Instance | Trajectory | Failure Witness |
|----------|-----------|-----------------|
| astropy__astropy-13398 | ✅ | ✅ |
| django__django-11400 | ✅ | ✅ |
| django__django-11820 | ✅ | ✅ |
| django__django-12125 | ✅ | ✅ |
| django__django-12858 | ✅ (pilot50) | ❌ |
| django__django-13023 | ✅ (pilot50) | ❌ |
| django__django-13109 | ✅ (pilot50) | ❌ |
| django__django-13449 | ✅ (pilot50) | ❌ |
| django__django-13513 | ✅ (case_bundles) | ✅ |
| django__django-13925 | ✅ (pilot50) | ❌ |
| django__django-15863 | ✅ (pilot50) | ❌ |
| django__django-16454 | ✅ | ✅ |
| sympy__sympy-13877 | ✅ | ❌ |
| sympy__sympy-16597 | ✅ (case_bundles) | ✅ |
| sympy__sympy-17318 | ✅ (pilot50) | ✅ |
| sympy__sympy-20428 | ✅ (case_bundles) | ✅ |
