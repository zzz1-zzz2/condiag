# ConDiag v0.2 — Failure-Guided Context Recovery Middleware

ConDiag is a **context recovery module** that sits between a failed repair-agent run and its retry. It does **not** write the patch; it gives the retrying agent better context and repair constraints.

- **主流水线**：`condiag/` (core) + `experiments/` (baselines)
- **测试基准**：ContextBench / SWE-bench Verified (500 instances)
- **主 LLM**：DeepSeek V4 (`deepseek-chat`)
- **修复框架**：5R — REHYDRATE / RETRIEVE / RELOCALIZE / RESTRAIN / RECONCILE + NOOP
- **4 baseline 对比**：base_miniswe / feedback_retry / broad_expansion / condiag_packet_only

---

## 完整架构 (Layer 0–16)

```
                         ┌──────────────────────────────┐
                         │  L16: Seed Regression Guard  │ ← 5 flows locked_v0 diff
                         └──────────┬───────────────────┘
                                    │ 回归测试
    ═══════════════════════════════════════════════════════════════
          评估平面 (离线审计)           │         运行时平面 (agent 可见)
    ┌──────────┬───────────┐          │      ┌──────────────────────┐
    │ Context  │ SWE-bench  │          │      │ ConDiag Runtime Core │
    │ Bench    │ Official   │          │      │ (L3→L12)             │
    │ Metrics  │ Eval       │          │      └──────────────────────┘
    │ (gold)   │ (resolved) │          │
    └──────────┴───────────┘          │
    ════════════════════════════════════════════════════════════════
                                       │
    ┌──────────────────────────────────┼──────────────────────────────┐
    │  L0: Benchmark                   │  L11-L13: Agent Retry        │
    │  (ContextBench Verified 500)     │  (workspace-based attempt_2) │
    └──────────────────┬───────────────┴──────────────────────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L1: Case Bundle Builder        │ ← 解析 agent trajectory
    │  condiag/tools/build_case_bundle │
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L2: Agent Adapter              │ ← agent 格式 → ConDiag 格式
    │  condiag/adapters/              │   只做格式转换，不做诊断
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L3: Runtime Signals (schemas)  │ ← 35字段，含 viewed_spans dict
    │  condiag/schemas.py             │   final_patch_context_files 保留行级
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L4: Leakage Guard              │ ← 禁止 gold 字段进入 runtime
    │  condiag/leakage_guard.py       │   gold_check 白名单除外
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L5: Trigger                    │ ← 自动分类：4 检测器
    │  condiag/trigger.py             │   Trigger-1: validation failure
    │  experiments/retry_trigger.py   │   Trigger-2: patch shape / mismatch
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L6: Scope Guard                │ ← 5 信号打分 (0-5)
    │  condiag/scope_guard.py         │   >=2 warning / >=3 strong
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L7: Normalizer + Action Planner│ ← Pathology → 5R 推导
    │  condiag/diagnosis_normalizer.py│   检索/控制 action 分离
    │  condiag/action_planner.py      │   multi-intent 支持
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L8: Retrieval / Guard Executor │ ← 8 个检索操作 + 4 个控制操作
    │  condiag/retrieval_executor.py  │   REHYDRATE span级排除(不可文件级)
    │  condiag/scope_guard_executor.py│
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L9: Evidence Selector          │ ← top-k picking, 多样性保证
    │  condiag/evidence_selector.py   │   (path,op) 最多 2, budget 限制
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L10: Context Packet Builder    │ ← 渲染 markdown context_packet.md
    │  condiag/context_packet_builder │   检索流 + RESTRAIN 流双模板
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L14: Baseline Handlers         │ ← 4 baseline 独立 handler
    │  experiments/baseline_handlers  │   Broad 走独立 ripgrep
    └──────────────────┬──────────────┘
                       │
    ┌──────────────────┴───────────────┐
    │  L15: Compare Matrix            │ ← 17×4 对比矩阵 + incremental overlap
    │  experiments/summarize_pilot50  │
    └──────────────────┴───────────────┘
```

---

## 逐层详解

### L0 — Benchmark (外部)

ContextBench / SWE-bench Verified 数据集。ConDiag runtime **永远不会直接消费**它——这是被 leakage_guard 保护的"评估平面"。

| 属性 | 值 |
|------|----|
| 输入 | instance_id, base_commit, repo, FAIL_TO_PASS, PASS_TO_PASS, test_patch, gold_patch |
| 输出 | contextbench_metrics.json, resolved 标签 |
| 规则 | 永远不进入 runtime 路径 |
| 代码 | 外部 (ContextBench pip install) |

---

### L1 — Case Bundle Builder

把 agent trajectory (.traj.json) 转成 ConDiag 标准 6 件套。**Parser 严格只提取 agent 可见事实**，不读 gold。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/tools/build_case_bundle.py` (224 行) |
| 输入 | mini-SWE `.traj.json` |
| 输出 | `raw_trajectory.json`, `runtime_signals.json`, `patch.diff`, `local_test_outputs.md`, `final_patch_context.json`, `build_report.json` |
| 子模块 | `condiag/tools/parsers/base.py` — `ParsedTrajectory` 数据类 (40+ 字段) |
| 子模块 | `condiag/tools/parsers/miniswe.py` — mini-SWE 专用解析器 (294 行) |
| 子模块 | `condiag/tools/parsers/common.py` — 共享解析工具 (bash 块提取、行对解析、测试命令检测等) |
| 关键约束 | Parser "MUST NOT read gold/oracle fields"; 派生字段 (viewed_but_not_final / edited_but_not_viewed) 在 parse 末尾统一计算 |
| 状态 | ✓ 贴准 |

---

### L2 — Agent Adapter

每个 agent (mini-SWE, agentless, OpenHands, SWE-agent) 一个适配器。**只做格式转换**：traj → CaseBundle，context_packet.md → retry prompt。不做诊断/检索/规划。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/adapters/base.py` (110 行) — ABC + registry `@register_adapter` |
| 文件 | `condiag/adapters/miniswe.py` (142 行) — v0 主力适配器 |
| 文件 | `condiag/adapters/agentless.py` — Phase 2 跨 agent 验证 (stub) |
| 文件 | `condiag/adapters/openhands.py` — 预留 |
| 文件 | `condiag/adapters/swe_agent.py` — 预留 |
| 抽象方法 | `build_case_bundle`, `extract_runtime_signals`, `extract_patch`, `extract_final_patch_context`, `build_retry_input` |
| 关键约束 | "ConDiag Core only consumes the artifacts produced by these methods; it never reads agent-specific formats directly" |
| 状态 | ✓ 贴准 |

---

### L3 — Runtime Signals (Schemas)

Data structures + error classes，定义了 ConDiag 的数据语言。**stdlib only，零依赖**。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/schemas.py` (239 行) |
| RuntimeSignals | 35 字段：`viewed_spans: dict` (span 级)、`final_patch_context_files: list` (保留 `{file, lines}` 结构)、`edited_spans_per_file: dict` 等 |
| ManualDiagnosis | trigger_assessment, diagnosis, target_hints, retrieval_plan, retry_intent, gold_check (评估专用) |
| TriggerResult | trigger_type, trigger_layer, inferred_pathology_candidates, action_family, confidence_runtime |
| ActionPlan | primary_5r_action, retrieval_actions/control_actions 分离, unknown_operations |
| NormalizedDiagnosis | 从 ManualDiagnosis 派生, secondary_pathologies (multi-intent 支持) |
| 错误类 | ConDiagSchemaError, ConDiagLeakageError, ConDiagTaxonomyError |
| 状态 | ✓ 贴准 |

---

### L4 — Leakage Guard

双层防护：runtime_signals 全量扫描 + manual_diagnosis 白名单扫描。**gold_check 外出现 gold 字段 → 直接抛异常**。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/leakage_guard.py` (110 行) |
| 函数 | `check_runtime_signals(rs, taxonomy)` — runtime 字段永远不能包含 oracle |
| 函数 | `check_manual_diagnosis(md, taxonomy)` — oracle 只能在 `gold_check` 下 |
| 规则 | 遍历 dict 全部层级（递归），发现 forbidden key 立即记录 |
| Whitelist | 只有 `gold_check` 允许持有 oracle 字段 |
| 附加检查 | `gold_check.allowed_for_runtime` 必须显式为 False |
| 状态 | ✓ 贴准 |

---

### L5 — Trigger (自动分类)

从 RuntimeSignals 自动推断 trigger_type + pathology 候选。**两份实现**：core (`condiag/trigger.py`) 用于 ConDiag 运行时管道，experiments (`experiments/retry_trigger.py`) 用于 baseline runner。

| 属性 | 值 |
|------|----|
| 文件 (core) | `condiag/trigger.py` (255 行) |
| 文件 (exp) | `experiments/retry_trigger.py` (332 行) |
| 检测器 | 4 个：runtime validation failure, evidence-edit mismatch, partial-fix suspicion, regression signal |
| 输出 (core) | `TriggerResult` (含 pathology 候选列表，按 confidence 排序) |
| 输出 (exp) | `RetryTriggerResult` (6-rule 优先级 cascade，纯 dict 输入) |
| 偏差 | ⚠ 双实现。阈值不完全同步（retry_trigger 多了 `test_failures>=1`），合理工程隔离但需注意 |
| 校准 | 已对 5 个 seed case 校准：sympy-16597→RUNTIME_VALIDATION_FAILURE, sympy-13877→PATCH_SHAPE_ANOMALY, astropy-13398→EVIDENCE_EDIT_MISMATCH, django-11400→PARTIAL_FIX_SUSPICION, django-13195→NO_TRIGGER |
| 状态 | ⚠ 贴准但需维护同步 |

---

### L6 — Scope Guard

5 信号打分器，纯 runtime-visible。不调 gold、不调 ContextBench。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/scope_guard.py` (94 行) |
| 5 信号 | changed_files>=5, changed_lines>=200, api_calls>=80, repeated_edit_pattern, edited_files_lack_evidence |
| 阈值 | score>=2 → `triggered_warning`; score>=3 → `triggered_strong` |
| 输出 | `ScopeGuardResult` (score, signals dict, triggered_warning/strong) |
| 状态 | ✓ 贴准 |

---

### L7 — Diagnosis Normalizer + Action Planner

ManualDiagnosis → NormalizedDiagnosis (补充 taxonomy 默认值) → ActionPlan (检索/控制分离)。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/diagnosis_normalizer.py` (63 行) |
| 文件 | `condiag/action_planner.py` (50 行) |
| normalize | pathology 查 taxonomy → 补充 action_family / 5r_action / retry_intent |
| build_plan | 遍历 retrieval_plan steps → 按 taxonomy.retrieval_action_enum / control_action_enum 分流 |
| multi-intent | NormalizedDiagnosis 有 secondary_pathologies 字段 |
| 未知操作 | 报告为 unknown_operations，不静默丢弃 |
| 状态 | ✓ 贴准 |

---

### L8 — Retrieval Executor + Scope Guard Executor

**8 个检索操作** + **4 个控制操作**。ConDiag 的核心执行层。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/retrieval_executor.py` (1125 行) |
| 文件 | `condiag/scope_guard_executor.py` (282 行) |

**检索操作 (REHYDRATE / RETRIEVE / RECONCILE 族)**：

| 操作 | 用途 | 关键实现细节 |
|------|------|-------------|
| `FIND_FAILED_TEST` | 定位回归测试函数 | 优先 visible_regressions，fallback test_failures；name 匹配 tolerance 5 行 |
| `REHYDRATE_SEEN_EVIDENCE` | 恢复 agent 看过但未带入 PATCH_CONTEXT 的 span | **span 级排除**：`final_spans: dict[str, set[int]]`，`issubset` 判定；dropped span 得分 0.85+，uncovered 文件 0.65+ |
| `FIND_SYMBOL_DEFINITION` | 目标符号定义 | 精确匹配优先；新增符号 fallback 到 enclosing class |
| `READ_DEPENDENCY_NEIGHBORHOOD` | 兄弟方法实现（如其他 class 的同名 `_eval_is_*`） | 按 method_name 搜索所有 symbol；同 parent 优先 |
| `FIND_NEIGHBOR_TESTS` | 概念关键词 → 相近测试 | concept target_hints 分词 → 匹配 test_index name |
| `FIND_PARALLEL_IMPLEMENTATIONS` | 兄弟类 + 并行实现 | Strategy A: 同文件、同 camel 词的兄弟类；Strategy B: 同目录、同名模式文件 |
| `FIND_IMPORTS` | 导入目标模块的文件 | rg `\b<module>\b` → 筛选 `from`/`import` 行 |
| `FIND_CALLERS` | 调用/引用目标符号的站点 | rg → 给 decorator/def/class/call 分级打分 |

**控制操作 (RESTRAIN 族)**：

| 操作 | 用途 |
|------|------|
| `SCOPE_CONSTRAIN` | 根据 support_map + scope_report 生成约束卡片 |
| `PATCH_PRUNE_CANDIDATES` | 委托 patch_prune_suggester 给出 drop/review/keep 建议 |
| `RUN_RUNTIME_VISIBLE_VALIDATION` | v0: deferred |
| `REVALIDATE_EDIT_SCOPE` | v0: deferred |

| 关键约束 | `_parse_lines_field` 将 `"397-401"` / `[397, 401]` / `397` 正确解析为 `set[int]` |
| 状态 | ✓ 贴准（Step 5 REHYDRATE span-level fix 已落地） |

---

### L9 — Evidence Selector

贪婪选取 top-k 证据 candidate，保证多样性和最大化分数。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/evidence_selector.py` (120 行) |
| 默认 budget | max_files=6, max_lines=300, max_evidence=8 |
| 去重 | 按 (path, start_line, end_line) |
| 多样性 | 每 (path, operation) 最多 2 |
| required_relations | 5 种必选关系兜底：visible_regression_test, target_symbol_definition, enclosing_class_definition, sibling_method_implementation, previously_seen_but_dropped |
| 状态 | ✓ 贴准 |

---

### L10 — Context Packet Builder

把 selected_evidence 渲染成 agent 可读的 markdown packet。**双模板**：检索流 + RESTRAIN/Scope Guard 流。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/context_packet_builder.py` (364 行) |
| 检索流模板 | 5 段：Diagnosis → Runtime Failure Evidence → Retrieved Evidence → Repair Constraints → Retry Instruction |
| Guard 流模板 | Diagnosis → Runtime Failure Evidence (scope shape) → Scope Guard Evidence (support/weak/unsupported) → Constraints → Retry Instruction |
| 约束生成 | 按 5R action 不同：RECONCILE → "Preserve behavior required by visible regressions"; REHYDRATE → "Use rehydrated spans as primary evidence"; RETRIEVE → "Audit sibling logic"; RESTRAIN → "Restrict to files with direct support" |
| Snippet 渲染 | 带行号的 `Lxxxx` 格式代码块 |
| 状态 | ✓ 贴准 |

---

### L11 — Agent Retry Runner

**Workspace-based**（非 direct-diff）。Agent 在 git worktree 中编辑真实文件，`git diff` 产生合法 patch。

| 属性 | 值 |
|------|----|
| 文件 | `experiments/retry_runner_workspace.py` (498 行) |
| 核心函数 | `run_workspace_retry()` — 主入口 |
| 核心函数 | `setup_workspace()` — 创建 git worktree，应用 test_patch |
| 核心函数 | `_extract_code_block()` — 从 LLM 响应提取代码块，处理截断 |
| 核心函数 | `_call_llm()` — DeepSeek V4 API 调用 |
| 核心函数 | `parse_file_edits()` — 解析 `### FILE: path` 标记的文件编辑 |
| 关键修复 | max_tokens 4096→32768；fence stripping (截断时去掉开头的 \`\`\`python)；最终写入前二次 strip |
| 状态 | ✓ 贴准 |

---

### L12 — Retry Input Builder

把 context_packet.md 转成 agent-specific retry 指令。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/adapters/miniswe.py:build_retry_input()` |
| 偏差 | ⚠ `retry_runner_workspace._build_workspace_prompt()` 走了另一条路径，不经过 adapter（packet_only vs real retry 两条 prompt 路径不同） |
| 状态 | ⚠ 功能存在但双路径需统一 |

---

### L13 — Retry Validator (Docker Eval)

在 docker 容器中应用 patch + 运行测试，判定 resolved。

| 属性 | 值 |
|------|----|
| artifact 检查 | `experiments/artifact_validator.py` (208 行) — 文件完整性 + gold 泄漏扫描 |
| docker eval | `experiments/retry_runner_workspace.py:smoke_workspace_retry()` — apply test_patch + model_patch + pytest/django |
| 临时脚本 | `scripts_tmp/smoke_eval_matrix.py` — 批量 docker eval |
| 缺失 | **无独立模块** `experiments/retry_validator.py`；docker eval 逻辑嵌入在 runner 内部 |
| 状态 | ✗ 缺失独立模块 |

---

### L14 — Baseline Handlers

**4 baseline 独立 handler**。Broad Expansion 用独立 ripgrep，不接触 ConDiag 检索。

| 属性 | 值 |
|------|----|
| 文件 | `experiments/baseline_handlers.py` (1079 行) |
| `handle_base_miniswe` | 从 traj 构建 attempt_1，final = attempt_1 (单 attempt 控制) |
| `handle_feedback_retry` | retry_trigger → feedback packet (不含 ConDiag 证据) |
| `handle_broad_expansion` | retry_trigger → 独立 ripgrep 搜索 → generic packet |
| `handle_condiag_packet_only` | retry_trigger → ConDiag retrieval pipeline → typed/5R packet |
| `handle_condiag_retry` | 预留 |
| handler 注册 | `BASELINE_HANDLERS: dict[str, HandlerFn]` |
| 6 类 packet_mode | condiag_noop / condiag_abstain / condiag_diagnostic_only_no_repo / condiag_diagnostic_only_no_actions / condiag_retrieval / condiag_guard |
| 状态 | ✓ 贴准 |

---

### L15 — Compare Matrix

生成 17×4 对比矩阵 + incremental overlap 分析。

| 属性 | 值 |
|------|----|
| 文件 | `experiments/summarize_pilot50.py` — 统一对比矩阵 (CSV + JSON) |
| 文件 | `experiments/packet_gold_overlap.py` — 整个 packet vs gold_context 逐行 overlap |
| 文件 | `experiments/packet_gold_overlap_incremental.py` — per-source 增量 overlap (仅 ConDiag) |
| 文件 | `experiments/broad_source_ablation.py` — Broad Expansion 来源消融 |
| 文件 | `experiments/condiag_5r_group_metrics.py` — ConDiag 按 5R 分组统计 recall |
| 状态 | ✓ 贴准 |

---

### L16 — Seed Regression Guard

改 ConDiag Core 时必须重新跑。**5 flows: RECONCILE / RESTRAIN / REHYDRATE / RETRIEVE / NOOP**。与 locked_v0 baseline 逐字节对比。

| 属性 | 值 |
|------|----|
| 文件 | `condiag/seed_regression.py` (372 行) |
| RECONCILE | `sympy__sympy-16597` — retrieval flow |
| RESTRAIN | `sympy__sympy-13877` — guard flow |
| REHYDRATE | `astropy__astropy-13398` — retrieval flow |
| RETRIEVE | `django__django-11400` — retrieval flow |
| NOOP | `django__django-13195` — noop flow |
| critical files (retrieval) | executed_actions.json, selected_evidence.json, context_packet.md, retrieved_candidates.jsonl |
| critical files (guard) | scope_guard_actions.json, edit_support_map.json, patch_prune_report.json, context_packet.md |
| 对比方式 | 字节级 `read_bytes()` 对比当前 vs `.locked_v0` |
| 输出 | `seed_regression_matrix.csv` + `seed_regression_report.json` |
| 状态 | ✓ 贴准，5/5 PASS |

---

## 其他核心文件

| 文件 | 用途 |
|------|------|
| `condiag/loader.py` | 统一的文件加载器：load_manual_diagnosis, load_taxonomy, load_runtime_signals |
| `condiag/cli.py` | CLI 入口 (`python -m condiag.cli`) |
| `condiag/repo_resolver.py` | 解析 repo 路径 + base_commit |
| `condiag/repository_index.py` | 仓库索引：symbol_index, test_index, file_index, rg_search, read_span |
| `condiag/patch_scope_analyzer.py` | 分析 patch 的 scope 信号（文件数、行数、模式） |
| `condiag/edit_support_checker.py` | 检查每个 edited file 有无 runtime evidence 支持 |
| `condiag/patch_prune_suggester.py` | 根据 support_map 给出 drop/review/keep 建议 |
| `condiag/manual_retrieval.py` | 手工 retrieval flow (seed regression 用) |
| `condiag/manual_guard.py` | 手工 guard flow (seed regression 用) |
| `condiag/report.py` | 报告生成工具 |
| `condiag/tools/find_relocalize_candidates.py` | RELOCALIZE miner (预留) |
| `experiments/baseline_runner.py` | 统一的 baseline runner 调度器 |
| `experiments/broad_expansion.py` | Broad Expansion 的通用检索扩展（独立 ripgrep） |
| `experiments/condiag_packet_only.py` | ConDiag packet_only 流程编排 |
| `experiments/manifest_builder.py` | 构建 pilot50 manifest CSV |
| `experiments/cost_extractor.py` | 从 traj.json 提取 API cost |
| `experiments/artifact_validator.py` | artifact 完整性 + 泄漏检查 |

---

## 完整性总结

```
L0  Benchmark            ✓  外部，泄漏隔离到位
L1  Case Bundle          ✓  最干净的层，parser 严格不读 gold
L2  Agent Adapter        ✓  纯格式转换，无诊断逻辑
L3  Runtime Signals      ✓  35 字段，span 级数据保留
L4  Leakage Guard        ✓  多层扫描，gold_check 白名单
L5  Trigger              ⚠  core + experiments 双实现，阈值不完全同步
L6  Scope Guard          ✓  5 信号，纯 runtime
L7  Normalizer/Planner   ✓  multi-intent + action 分离
L8  Retrieval/Guard Exec ✓  8+4 操作，span 级排除已落地
L9  Evidence Selector    ✓  dedup + diversity + required_relations
L10 Context Packet       ✓  双模板，5 段结构
L11 Retry Runner         ✓  workspace-based (非 direct-diff)
L12 Retry Input Builder  ⚠  双 prompt 路径 (adapter vs runner 自建)
L13 Retry Validator      ✗  功能散落，无独立模块
L14 Baseline Handlers    ✓  4 baseline 独立 handler
L15 Compare Matrix       ✓  17×4 矩阵 + incremental overlap
L16 Seed Regression      ✓  5 flows 5/5 PASS
```

**15/16 层贴准。** 1 个缺失 (L13)，2 个轻微偏差 (L5/L12)。

---

## 命名约定

- **ConDiag (大小写)**：项目名
- **5R action (大写)**：REHYDRATE / RETRIEVE / RELOCALIZE / RESTRAIN / RECONCILE
- **pathology (大写蛇形)**：REGRESSION_AFTER_PARTIAL_FIX / OVER_EXPLORE_OVER_EDIT / EXPLORE_OK_EDIT_MISALIGNED / UNDER_EDIT_PARTIAL_FIX
- **runtime_signals.json**：35 个 agent 可见事实；绝对不含 gold / contextbench_metrics / official_eval
- **gold_check**：ManualDiagnosis 中唯一允许存放 oracle 字段的容器
- **context_packet.md**：注入给 agent retry 的 markdown；feedback_retry 版本不含 ConDiag 检索

---

## 环境

- **代码**：WSL2 Ubuntu 22.04 (`~/condiag`)
- **产物**：`/mnt/d/condiag-artifacts/`
- **Python**：3.11
- **LLM API**：DeepSeek V4 (`deepseek-chat`)

## 仓库

- **GitHub**：[https://github.com/zzz1-zzz2/condiag](https://github.com/zzz1-zzz2/condiag)
- **Gitignore**：排除 `workspaces/`, `ContextBench/`, `scripts_tmp/`, `envs/`, `*.docx`, `*.pdf`, `*.tar`
