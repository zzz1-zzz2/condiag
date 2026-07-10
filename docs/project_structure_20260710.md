# ConDiag 项目结构全景（2026-07-10）

> 总规模：~38,000 行 Python，~160 个源文件
> 分支：`context-deficiency-v1`

---

## 一、目录架构

```
condiag/
├── CLAUDE.md                     # 项目规范 → 需更新
├── README.md                     # 简介
├── robust_eval_wrapper.py        # 评估包装器
│
├── condiag/                      # ← 核心库（~7,300 行 / 31 文件）
│   ├── *.py (当前架构 7 文件)     # 活跃
│   ├── *.py (旧架构 ~20 文件)     # 待归档
│   ├── adapters/                 # Host Agent 适配层
│   └── tools/                    # 工具函数
│
├── experiments/                  # ← 实验层（~8,800 行 / 32 文件）
│   ├── failure_parsers/          # [FROZEN] 12 file | 输入规范化
│   ├── failure_witness_builder   # [DONE]  Witness 构建
│   ├── baseline_handlers/runner  # [ACTIVE] Baseline 编排
│   ├── packet_consumption/       # 旧消费分析
│   └── *.py (测试 / 审计脚本)
│
├── scripts/                      # ← 稳定 CLI（~40 文件）
│   ├── inspect_*.py              # 检查工具
│   ├── batch_*.py                # 批处理
│   ├── pick_*.py                 # 实例选择
│   ├── verify_*.py               # 验证
│   └── ...                       # 其他
│
├── scripts_tmp/                  # ← 临时脚本（~45 文件）→ 归档
│   ├── gen_*.py                  # 生成脚本（旧 pipeline）
│   ├── eval_*.py                 # 评估脚本
│   ├── rebuild_*.py              # 重建
│   └── ...                       # 一次性任务
│
├── docs/                         # ← 文档（~20 文件）
│   ├── plans/                    # 计划文档
│   ├── adr/                      # 架构决策记录
│   ├── condiag_architecture_*.md # 架构文档
│   ├── *_context_packet_full.md  # 实例分析
│   └── project_state_*.md        # 状态记录
│
├── archive/                      # ← 已有但接近空
├── envs/                         # 环境文件
├── workspaces/                   # Agent 工作区（大文件）
└── logs/                         # 运行日志
```

---

## 二、condiag/ 核心库 — 按状态分类

### 🟢 当前架构（活跃 / v2）

| 文件 | 行数 | 角色 |
|---|---|---|
| `condiag/schemas.py` | 440 | **数据模型** — FailureWitness / DiagnosticSearchContract / TrajectorySignals |
| `condiag/trajectory_signals.py` | 727 | **轨迹信号提取** — error-edit alignment, exploration mode, test behavior |
| `condiag/search_contract_builder.py` | 761 | **Contract 构建** — 从诊断到结构化动作 |
| `condiag/contract_renderer.py` | 219 | **Contract 渲染** → Markdown |
| `condiag/leakage_guard.py` | 109 | **Gold leakage 检测** |
| `condiag/path_utils.py` | — | 路径工具 |
| `condiag/adapters/base.py` | — | Host Agent 接口抽象 |
| `condiag/adapters/miniswe.py` | — | mini-SWE 适配器 |
| `condiag/adapters/miniswe_retry_injection.py` | 486 | Retry 注入 |
| `condiag/adapters/swe_agent.py` | — | SWE-agent 适配器 |
| `condiag/adapters/agentless.py` | — | Agentless 适配器 |
| `condiag/adapters/openhands.py` | — | OpenHands 适配器 |

### 🔴 待归档（旧架构 / v1）

| 文件 | 行数 | 被替换者 |
|---|---|---|
| `context_packet_builder.py` | 827 | `search_contract_builder.py` |
| `diagnosis_generator.py` | 521 | `context_deficiency_diagnoser.py`（未实现） |
| `diagnosis_normalizer.py` | — | — |
| `retrieval_executor.py` | 1124 | Host Agent tool loop |
| `api_navigation.py` | 563 | Host Agent tool loop |
| `action_planner.py` | — | Contract |
| `evidence_selector.py` | — | — |
| `manual_retrieval.py` | — | — |
| `manual_guard.py` | — | — |
| `edit_support_checker.py` | 384 | — |
| `patch_scope_analyzer.py` | — | — |
| `patch_prune_suggester.py` | — | — |
| `repo_resolver.py` | — | — |
| `repository_index.py` | — | — |
| `seed_regression.py` | 371 | — |
| `scope_guard.py` | — | — |
| `scope_guard_executor.py` | — | — |
| `cli.py` | 545 | — |
| `report.py` | — | — |
| `trigger.py` | — | — |
| `loader.py` | — | — |

### ⚪ 其他

| 文件 | 角色 |
|---|---|
| `condiag/tools/build_case_bundle.py` | Case bundle 构建 |
| `condiag/tools/find_relocalize_candidates.py` | RELOCALIZE miner |
| `condiag/tools/parsers/*.py` | Trajectory parser 工具 |

---

## 三、experiments/ — 按状态分类

### 🟢 当前活跃

| 文件 | 行数 | 角色 |
|---|---|---|
| `failure_parsers/` (12 files) | ~2000 | **[FROZEN v2.0]** — 输入规范化完成 |
| `failure_witness_builder.py` | 263 | Witness 构建入口 |
| `baseline_handlers.py` | 1204 | 5 baseline intervention 实现 |
| `host_agent_retry_runner.py` | 1024 | Attempt-2 运行器 |
| `baseline_runner.py` | 289 | Baseline 编排层 |
| `instance_manifest.py` | 304 | 实例清单 |
| `experiment_settings.py` | 159 | 实验配置 |
| `condiag_packet_only.py` | 324 | ConDiag packet handler |
| `broad_expansion.py` | 791 | Broad expansion handler |

### 🔴 待归档

| 文件 | 原因 |
|---|---|
| `archived/retry_executor.py` | 已归档 |
| `retry_executor.py` (root) | 旧版重复 |
| `retry_trigger.py` | 旧版 |
| `packet_consumption/` | 旧版分析 |
| `packet_gold_overlap*.py` | 旧版分析 |
| `regenerate_dev5_packets.py` | 包已过时 |
| `run_task6_alpha_*.py` | 一次性 |

### ⚪ 测试 & 辅助

| 文件 | 角色 |
|---|---|
| `test_*_handler.py` (4 files) | Handler 单元测试 |
| `test_baseline_runner_smoke.py` | Runner smoke test |
| `test_host_agent_retry_protocol.py` | Retry 协议测试 |
| `artifact_validator.py` | 产物验证 |
| `canonicalize_base_eval.py` | 基础评估规范化 |
| `cost_extractor.py` | 成本提取 |
| `check_docker_deps.py` | Docker 依赖检查 |

---

## 四、数据流架构（当前）

```
                        ┌──────────────────────────┐
                        │  official_eval.json      │
                        │  (patch_apply / timeout)  │ ← authoritative for stage
                        └────────────┬─────────────┘
                                     │
┌─────────────────┐    ┌─────────────▼──────────────┐
│ raw test_output │───▶│  FailureWitness Builder    │
│ (log file)      │    │  v2.0 (frozen)             │
└─────────────────┘    │  + failure_parsers/        │
                       └─────────────┬──────────────┘
                                     │
                                     ▼
                       ┌─────────────────────────────┐
                       │   FailureWitness             │
                       │   + TrajectorySignals        │
                       │   (runtime + oracle audit)   │
                       └─────────────┬───────────────┘
                                     │
                       ┌─────────────▼───────────────┐
                       │  context_deficiency_diagnoser│ ← ❗ MISSING
                       │  → CDType 7-type scoring     │    需实现
                       └─────────────┬───────────────┘
                                     │
                       ┌─────────────▼───────────────┐
                       │  search_contract_builder     │ ← EXISTS (761 lines)
                       │  → DiagnosticSearchContract  │    未批量审计
                       └─────────────┬───────────────┘
                                     │
                       ┌─────────────▼───────────────┐
                       │  contract_renderer           │ ← EXISTS (219 lines)
                       │  → rendered_contract.md      │
                       └─────────────┬───────────────┘
                                     │
                       ┌─────────────▼───────────────┐
                       │  host_agent_retry_runner     │
                       │  → Attempt-2 trajectory      │
                       └─────────────┬───────────────┘
                                     │
                       ┌─────────────▼───────────────┐
                       │  contract_compliance_analyzer│ ← ❌ MISSING
                       │  → compliance report         │    需实现
                       └─────────────┬───────────────┘
                                     │
                       ┌─────────────▼───────────────┐
                       │  ContextBench eval           │
                       │  → trajectory metrics        │
                       └─────────────────────────────┘
```

---

## 五、需要立即做的事

### 5.1 关键时刻

| # | 动作 | 原因 |
|---|---|---|
| 1 | 实现 `context_deficiency_diagnoser.py` | Blocking — CLAUDE.md 撒谎说 DONE |
| 2 | 归档 `condiag/` 中 ~20 个旧文件 | 当前架构 ~31 文件，只有 7 个是活的 |
| 3 | 归档 `scripts_tmp/` 全部 ~45 文件 | 全部是一次性任务 |
| 4 | 删除或归档 `experiments/` 中 ~10 个旧文件 | 减少混乱 |
| 5 | 更新 `CLAUDE.md` 反映真实状态 | 多处声称与实际不符 |
| 6 | 提交 git + push | 目前大量 untracked 新文件 |

### 5.2 冻结声明

FailureWitness Builder v2.0 — 以下文件不再修改（除非 schema 错误或 crash）：

```
experiments/failure_parsers/  (12 files) ← FROZEN
experiments/failure_witness_builder.py   ← FROZEN
experiments/rebuild_witnesses_v2.py      ← FROZEN
```

### 5.3 缺失模块优先级

| 模块 | 预计行数 | 优先级 |
|---|---|---|
| `condiag/context_deficiency_diagnoser.py` | ~400-600 | **P0** — Blocking 整个管线 |
| `experiments/contract_compliance_analyzer.py` | ~300-500 | **P1** — 需要 contract schema 稳定后实现 |

---

## 六、文件清理计划

### Batch 1 — 归档旧 condiag 模块

移入 `archive/condiag_v1/`：
- context_packet_builder.py
- diagnosis_generator.py, diagnosis_normalizer.py
- retrieval_executor.py, api_navigation.py
- action_planner.py, evidence_selector.py
- manual_retrieval.py, manual_guard.py
- edit_support_checker.py, patch_scope_analyzer.py
- patch_prune_suggester.py, repo_resolver.py
- repository_index.py, seed_regression.py
- scope_guard.py, scope_guard_executor.py
- cli.py, report.py, trigger.py, loader.py

### Batch 2 — 归档 scripts_tmp

整目录移入 `archive/scripts_tmp/`

### Batch 3 — 清理 experiments 旧文件

- retry_executor.py, retry_trigger.py
- packet_consumption/, packet_gold_overlap*.py
- regenerate_dev5_packets.py, run_task6_alpha*.py
- summarize_pilot50.py, task3c_preflight.py

---

## 七、当前实例状态

```
39 first-failed
├── 16 eligible (ConDiag 实验池)
│   ├── 7 SWE-bench Verified
│   ├── 8 SWE-bench Multi
│   ├── 1 SWE-bench Poly
│   ├── 12 python · 3 js · 1 rust
│   ├── 3 high quality · 13 medium
│   └── 0 generic · 0 low
├── 18 infrastructure / build failure (excluded)
└── 5 patch-apply / missing-log (excluded)

Dev Pool (6):  django-11820, django-13513, sympy-20428,
               ansible-8127ab, axios-5661, ripgrep-1367
Held-out (10): remaining eligible
```
