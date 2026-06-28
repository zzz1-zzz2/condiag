# ConDiag Baseline Runner Design v0.2

**Status:** Day 4 D4-1 已拍板，待 D4-2 retry_trigger 实现
**Author:** ConDiag project
**Date:** 2026-06-28（v0.1 draft）/ 2026-06-28（v0.2 拍板）
**Drivers:** 学长 2026-06-28 反馈（见 `feedback_condiag_minimal_core_full_system.md`）+ 三轴 taxonomy v0.3（见 `taxonomy_v0.3_three_axis_draft.md`）
**Changelog (v0.1 → v0.2):** 3 个 Open Question 全部决策；schema 目录加 `final/` 子目录；retry_trigger 共用结论落地；详细字段定义抽到 `artifact_schema.md`

---

## 1. Goals

- **方法简单，系统完整。** ConDiag Core 已经有 5-flow seed regression 兜底，v0 不再继续打磨内部；优先把 baseline 实验闭环搭起来。
- **4 个 baseline 在同一批 ContextBench 实例上单独跑、统一产出、统一评价。** 没有这个闭环，回答不了"ConDiag 是否真的比普通 retry / 多塞上下文好"。
- **Artifact 完整可复现。** 每个 run 都能被未来的论文表格、ablation、case study 直接消费。

## 2. Out of Scope（推后到 Day 5+）

| 事项 | 触发再做 |
|---|---|
| RELOCALIZE flow 实现（TRACE_ERROR_ORIGIN + RERANK_LOCATIONS） | Pilot50 / Batch2 跑出 strong RELOCALIZE candidate |
| django-16454 manual-guard | 证明它是高价值 RESTRAIN case 且 baseline 已通 |
| django repo dirty 7089 文件清理 | 要跑 seed_regression 或 manual-recovery 前 |
| 学习型 classifier / cross-agent adapter / auto_diagnoser | Pilot50 baseline 对比有结论后 |

---

## 3. Artifact Schema（D4-1）

详细字段定义见 `artifact_schema.md`。本节给目录树和 baseline 占用。

### 3.1 目录树

```
runs/pilot50/<agent>/<baseline>/<instance_id>/   # v0.2 加 <agent>/ 维度，见 §4.5
├── attempt_1/
│   ├── raw_trajectory.json
│   ├── patch.diff
│   ├── runtime_signals.json
│   ├── final_patch_context.json
│   ├── local_test_outputs.md
│   ├── contextbench_metrics.json     # 拍板 4.2：每 attempt 各算一份
│   └── attempt_report.json
├── intervention/                     # Base mini-SWE 无此目录
│   ├── intervention_report.json
│   ├── context_packet.md
│   ├── selected_evidence.json
│   └── expansion_report.json         # Broad Expansion 专用；其它 baseline 可空
├── attempt_2/                        # Base mini-SWE 无此目录；packet_only 模式也无
│   ├── raw_trajectory.json
│   ├── patch.diff
│   ├── runtime_signals.json
│   ├── final_patch_context.json
│   ├── contextbench_metrics.json
│   └── attempt_report.json
├── final/                            # 拍板 4.2 加：最终交付 patch + metrics
│   ├── patch.diff                    #   retry baseline → attempt_2 patch; Base → attempt_1 patch
│   ├── contextbench_metrics.json     #   指向 final patch 的 metrics
│   └── final_report.json             #   patch_selector_report（哪个 attempt 被选为 final）
├── cost.json
└── run_report.json
```

### 3.2 各 baseline 占用的子目录

| Baseline | attempt_1 | intervention | attempt_2 | final |
|---|---|---|---|---|
| Base mini-SWE | ✓ | ✗ | ✗ | ✓（= attempt_1） |
| Feedback Retry | ✓ | ✓ | ✓ | ✓（= attempt_2） |
| Broad Expansion | ✓ | ✓ | ✓（或 packet_only 模式跳过） | ✓ |
| Base + ConDiag | ✓ | ✓ | ✓ retry / ✗ packet_only | ✓ |

### 3.3 关键约定（详见 artifact_schema.md）

- `cost.json` 含 `model / provider / api_calls / prompt_tokens / completion_tokens / total_tokens / wall_time_seconds / estimated_usd (nullable) / pricing_source (nullable)`；token / api_calls / wall_time 必填，美元估算可选 nullable（拍板 4.3）
- `final/contextbench_metrics.json` 是 final patch 的 metrics；`attempt_N/contextbench_metrics.json` 是各 attempt 的 metrics；ContextBench metrics **evaluation-only**，不进入 retry_trigger 或 ConDiag runtime（拍板 4.2）
- `final/patch.diff` 由 patch_selector 决定来源（retry baseline 通常 = attempt_2 patch；Base = attempt_1 patch）

---

## 4. Four Baselines（D4-2）

### 4.1 Base mini-SWE

- **attempt_1**：跑一次 mini-SWE-agent
- **不做**任何 intervention 或 retry
- **作用**：主对照基线；也用于 ConDiag triage 的离线分析输入

### 4.2 Feedback Retry

- **attempt_1** → 看 exit_status / runtime_signals 判断"是否需要 retry"（rule：exit_status != "submitted_with_success" OR visible test failures > 0）
- **intervention 内容**（**严格限制**）：
  - 可见 local test 输出（attempt_1/local_test_outputs.md 原文）
  - stack trace（如有）
  - failed visible tests 列表
  - previous patch summary（attempt_1/patch.diff + 改了哪些文件）
  - instruction："revise the patch based on the feedback above"
- **严禁**注入：
  - ConDiag retrieved evidence
  - ContextBench gold context
  - official FAIL_TO_PASS / PASS_TO_PASS
- **attempt_2**：把 intervention 喂给同一 mini-SWE-agent 续跑
- **作用**：检验 ConDiag 是否比"普通 feedback retry"好

### 4.3 Broad Expansion

**Status (D4-6 + D4-6.1, updated 2026-06-28):** packet_only framework PASS.
Real ripgrep-backed lexical expansion **completed** in D4-6.1 — broad_rg fires
end-to-end on smoke (12 queries / 60 hits on django-10880) and is wired into
the D4-8.5+ smoke matrix as `packet_mode=broad_rg`.

- **attempt_1** → 判断是否 retry（共用 `retry_trigger.py`）
- **intervention 内容**（**generic / lexical expansion only**，不做 typed diagnosis）：
  - `EDITED_FILE_WINDOW`：edited files 周围 ±40 行窗口（拍板 1）
  - `VIEWED_SPAN_CARRYOVER`：attempt_1 viewed spans top-k（来自 runtime_signals.viewed_spans）
  - `RG_ISSUE_KEYWORD_SEARCH`：issue keywords → **独立 ripgrep 子进程**（拍板 1，D4-6.1 落地）
  - `RG_FAILURE_KEYWORD_SEARCH`：error tokens / stack-trace 类名 → 独立 ripgrep
  - `RG_FAILED_TEST_NAME_SEARCH`：test_failures 名字 → 独立 ripgrep
  - 按 lexical score + path proximity 排序，控制 token budget
  - 拼成 generic ContextPacket（**无 pathology / 无 5R / 无 typed evidence 标签**）
- **严禁**调用 ConDiag `retrieval_executor` 任何方法（FIND_FAILED_TEST / FIND_NEIGHBOR_TESTS / REHYDRATE_SEEN_EVIDENCE / FIND_PARALLEL_IMPLEMENTATIONS / FIND_CALLERS / FIND_IMPORTS / READ_DEPENDENCY_NEIGHBORHOOD 全部禁用，拍板 1）
- **隔离执行**（三道防线）：
  1. 物理隔离 — `broad_expansion.py` 不 `import condiag.retrieval_executor`
  2. 源码审计 — `FORBIDDEN_IMPORTS` + `FORBIDDEN_TOKENS` 自动扫描
  3. runtime leakage scan — `broad_candidates.jsonl` / `context_packet.md` / `expansion_report.json` 全扫
- **Day 4 范围**：先做 `packet_only`（生成 generic ContextPacket 不 retry），smoke 验证后接 retry
- **作用**：检验 ConDiag 是否比"无脑多塞上下文"好；这是审稿人最可能问的对照
- **输出**：`intervention/{context_packet.md, expansion_report.json, broad_candidates.jsonl}`

> **Note (status qualifier):** D4-6 framework 验证的是 baseline 边界和 artifact schema；
> D4-6.1 已经把独立 ripgrep 接上（D4-8.5 smoke 已确认 broad_rg 真实执行）。
> Batch2 `d4_6_full` 跑（17 instance）是 D4-8.5 之前的产物，所以那时 `RG_*_SEARCH=0`；
> Step 3（Batch2 17×4 official compare）会重新跑 broad_expansion，RG_* 会 fire。

### 4.4 Base + ConDiag

- **attempt_1** → build_case_bundle → ConDiag trigger/triage → 生成 ContextPacket
- **两种 mode**：
  - `packet_only`：只生成 ContextPacket，不 retry（**Day 4 先做**，拍板 2）
  - `retry`：把 ContextPacket 注入 mini-SWE 续跑 attempt_2
- **作用**：我们的方法

### 4.5 Agent Adapter Layer（v0.2 新增，见 `adapter_layer.md`）

四个 baseline 都通过 Agent Adapter 与具体 repair agent 解耦：

```
raw agent run (traj.json / events / log)
        ↓ Input-side Adapter
case_bundle (runtime_signals + patch.diff + local_test_outputs + final_patch_context)
        ↓
ConDiag Core (trigger / taxonomy / 5R / retrieval / guard / packet builder)
        ↓
context_packet.md + recovery_report.json
        ↓ Output-side Adapter
agent-specific retry input (user_message / localized_candidates / event_injection / ...)
        ↓
原 agent 再修
```

- **当前 v0**：只实现 `MinisweAdapter`（thin wrapper 调 `tools/build_case_bundle.py`）
- **Planned skeleton**：`AgentlessAdapter` / `OpenhandsAdapter` / `SweAgentAdapter`（注册到 registry 但方法 NotImplementedError）
- **ConDiag Core 不 import agent-specific 模块**：`condiag.trigger` / `condiag.retrieval_executor` / `condiag.scope_guard` / `condiag.context_packet_builder` 都只消费 case_bundle 里的统一 JSON
- **目录树加 `<agent>/` 维度**：`runs/pilot50/<agent>/<baseline>/<instance>/` —— 后续接 Agentless 时不需要重构目录

**Adapter 选择**（见 `condiag/adapters/__init__.py`）：

| Adapter | status | 何时用 |
|---|---|---|
| `miniswe` | implemented | Pilot50 v0 全程 |
| `agentless` | planned | v1 跨 agent 验证；RELOCALIZE 场景特别好（file localization 是命名失败点） |
| `openhands` | planned | v1 跨 agent 验证 |
| `swe_agent` | planned | v1 跨 agent 验证 |

### 4.5.1 Phase 2 跨 agent 验证 plan（Agentless 优先，Pilot50 主线之后）

Phase 1（mini-SWE × 4 baseline）回答：**ConDiag 机制本身在同 agent 下是否比 Feedback Retry / Broad Expansion 好？**

Phase 2 用 Agentless 回答：**ConDiag Adapter 是否可迁移到结构不同的 agent？** 不复刻 mini-SWE 全部 4 baseline（会爆炸），只跑：

| Baseline | 规模 | 验证目标 |
|---|---|---|
| `Base Agentless` | 小规模 5 instance | Agentless trajectory 能否被 adapter 转成 runtime_signals；是否暴露更多 wrong-localization |
| `Agentless + ConDiag packet_only` | 小规模 5 instance（可选） | ConDiag runtime_gap_status / 5R 是否能复用 |
| `Agentless + ConDiag retry` | 后续 | 真正跨 agent end-to-end |

**Phase 2 5 instance 选择**（覆盖 RELOCALIZE 主场景 + 多样性）：
- 3 个 Django RELOCALIZE 高候选（待 Batch2 完后从 triage matrix 选；django-11820 / django-13449 是 batch1 反推候选）
- 1 个 sympy（如 sympy-13372）
- 1 个 NOOP-like（django-13195）

**当前不做**：OpenHands / SWE-agent（留 v2+）。

---

## 5. Runner Interface（D4-3）

### 5.1 目录结构

```
~/condiag/experiments/
├── baseline_runner.py             # 统一入口
├── baseline_configs/
│   ├── base_miniswe.yaml
│   ├── feedback_retry.yaml
│   ├── broad_expansion.yaml
│   └── condiag.yaml
├── validate_run.py                # D4-4
└── summarize_pilot50.py           # 汇总 4 baseline × 50 instance → CSV
```

### 5.2 调用

```bash
python3 -m experiments.baseline_runner \
    --agent <miniswe|agentless|openhands|swe_agent> \
    --baseline <base_miniswe|feedback_retry|broad_expansion|condiag> \
    --instances /mnt/d/condiag-artifacts/condiag/v0/pilot50/selected_instances.txt \
    --out /mnt/d/condiag-artifacts/condiag/v0/pilot50/runs/<agent>/<baseline> \
    [--mode packet_only|retry]      # ConDiag only
    [--limit N]                     # smoke test 用
```

`--agent` v0 只接受 `miniswe`（其他 3 个 adapter 是 planned skeleton，见 §4.5）。

### 5.3 Config 字段（YAML）

```yaml
# base_miniswe.yaml
name: base_miniswe
attempt_1:
  agent: miniswe
  model: deepseek-v4-pro
intervention: null
attempt_2: null
validator:
  required_artifacts:
    - attempt_1/raw_trajectory.json
    - attempt_1/patch.diff
    - attempt_1/runtime_signals.json
    - attempt_1/final_patch_context.json
    - contextbench_metrics.json
    - cost.json
    - run_report.json
  leakage_check: false              # Base 不需要 gold leak check
```

```yaml
# feedback_retry.yaml
name: feedback_retry
attempt_1: { agent: miniswe, model: deepseek-v4-pro }
retry_trigger:
  rule: exit_status_not_success_or_visible_test_failures
intervention:
  type: feedback_only
  allowed_inputs:
    - local_test_outputs
    - stack_trace
    - failed_visible_tests
    - previous_patch_summary
  forbidden_inputs:
    - condiag_retrieved_evidence
    - contextbench_gold_context
    - official_fail_to_pass
    - official_pass_to_pass
attempt_2: { agent: miniswe, model: deepseek-v4-pro }
validator: { required_artifacts: [...], leakage_check: true }
```

`broad_expansion.yaml` / `condiag.yaml` 同结构（详细字段待 D4-3 实现时填）。

---

## 6. Validator（D4-4）

每个 baseline 跑完自动跑 validator：

1. **artifacts 完整性**：config 里 `required_artifacts` 都存在
2. **no gold leakage**：
   - 检查 `raw_trajectory.json` 里是否出现 `gold_check` / `contextbench_metrics` / `official_eval` 等关键字
   - ConDiag 额外检查 `intervention/intervention_report.json` 里 `did_not_read_gold_check=true` + `did_not_read_official_eval=true`
3. **schema 校验**：JSON 文件 schema_version 对得上
4. **结果**：写到 `run_report.json` 的 `validator_status` 字段；`leakage_blocked` 时 run_report.verdict = `leakage_blocked`，该 run 不进入最终汇总

---

## 7. Smoke Test（D4-5）

第一批 smoke 选 **2 个 instance**：

| Instance | 角色 | 选择理由 |
|---|---|---|
| `django__django-13195` | NOOP-like | 已有 manual_diagnosis = LIKELY_CORRECT_NOOP；Base 应该 ✓，ConDiag 应该 dispatch=noop |
| `sympy__sympy-16597` 或 batch2 新失败 case | likely-failure / RECONCILE 候选 | 已有 manual_diagnosis = REGRESSION_AFTER_PARTIAL_FIX；ConDiag 应该 dispatch=reconcile |

跑 **4 baseline × 2 instance = 8 runs**，检查：
- artifact schema 都齐
- validator 全 ok
- ConDiag run 的 dispatch 与 manual_diagnosis 一致
- Broad Expansion / Feedback Retry 的 attempt_2 patch 与 Base 的 patch 不同（确认 retry 真的在跑）

---

## 8. Seed Regression（D4-6）

| 改动 | 是否跑 |
|---|---|
| baseline_runner.py / configs / validator | ✗ |
| ConDiag trigger / retrieval_executor / scope_guard / context_packet_builder / diagnosis schema | ✓ |
| 三轴 taxonomy mapping 表 | ✓ |

---

## 9. Decisions（v0.2 全部拍板）

| # | 问题 | 决策 | 原因 |
|---|---|---|---|
| 1 | Broad Expansion grep 实现 | **独立 ripgrep 子进程**，**严禁**复用 ConDiag `retrieval_executor` | 公平性：复用会让 baseline 偷 ConDiag 的工，对比不干净 |
| 2 | ConDiag `retry` vs `packet_only` 优先级 | **先 `packet_only`**（Day 4 范围），smoke 跑通后再接 `retry` | 减少 v0 变量：ContextPacket 质量 + retry prompt + patch selector 是独立问题，分开做 |
| 3 | Retry trigger rule 是否共用 | **共用 `experiments/retry_trigger.py`** | 触发策略不同会污染对比；trigger 只读 runtime-visible signals |
| 4.1 | smoke instance 选择 | **Batch2 完后挑 1 高价值 failure + `django__django-13195`**；Batch2 没完先用 `sympy__sympy-16597` + `django__django-13195` | 同时测 retry/feedback/packet 三类 + NOOP false positive |
| 4.2 | metrics 计算时点 | **attempt_1 和 final 都算**；ContextBench metrics **evaluation-only** | 支持 attempt_1 → final delta 比较；防 metrics 漏进 runtime |
| 4.3 | cost.json 是否含美元 | **含 `estimated_usd` 但 nullable**；token / api_calls / wall_time 必填 | 价格表易失真；美元估算可选 |

### 9.1 retry_trigger.py v0 接口（拍板 3 落地）

```python
# experiments/retry_trigger.py
def should_retry(runtime_signals: dict, patch_summary: dict) -> tuple[bool, str]:
    """
    所有 retry 型 baseline 共用。只读 runtime-visible signals。
    严禁读 gold_check / contextbench_metrics / official_eval / FAIL_TO_PASS / PASS_TO_PASS。
    """
    if runtime_signals.get("runtime_validation_failed"):
        return True, "RUNTIME_VALIDATION_FAILURE"
    if runtime_signals.get("patch_apply_failed"):
        return True, "PATCH_APPLY_FAILURE"
    if runtime_signals.get("timeout") or runtime_signals.get("step_limit_hit"):
        return True, "TERMINATION_FAILURE"
    if runtime_signals.get("patch_shape_anomaly"):  # scope_anomaly_score >= threshold
        return True, "PATCH_SHAPE_ANOMALY"
    if runtime_signals.get("evidence_edit_mismatch"):
        return True, "EVIDENCE_EDIT_MISMATCH"
    return False, "NO_TRIGGER"
```

Base mini-SWE 不调用此函数（它是 single attempt 对照）。

---

## 10. Day 4 工作序列（v0.2 拍板版）

```
D4-1   Write baseline_artifact_schema.md          ← 详细字段定义（落 artifact_schema.md）            ✓
D4-2   Implement experiments/retry_trigger.py     ← 共用 trigger（拍板 3）                              ✓
D4-2.5 Agent Adapter Layer framework              ← adapter_layer.md                                    ✓
D4-3   Implement baseline_runner skeleton         ← 入口 + configs 加载 + attempt 调度                  ✓
D4-4   Implement Base mini-SWE runner wrapper     ← 主对照，跑通才能往下                                ✓
D4-5   Implement Feedback Retry baseline          ← 第一个对照（共用 trigger）                          ✓
D4-6   Implement Broad Expansion framework        ← packet_only 框架 + 隔离审计（拍板 1）               ✓
D4-6.1 Real ripgrep execution in Broad Expansion  ← 独立 rg 子进程接 issue/error/test 查询              ✓
D4-7   Implement ConDiag packet_only baseline     ← 复用 manual-recovery（拍板 2）                      ✓
D4-7.1 ConDiag repo-backed retrieval              ← REHYDRATE / FIND_NEIGHBOR_TESTS 真 fire             ✓
D4-8   Smoke test 2 instance × 4 baseline         ← 拍板 4.1                                            ✓
D4-8.5 Repo plumbing for packet baselines         ← manifest 加 repo_base_path + issue 注入             ✓
D4-9   Generate compare matrix (smoke)            ← summarize_pilot50.py                                ✓
Step 3 Batch2 17×4 official compare matrix        ← broad_rg + condiag_retrieval 都已 fire，可正式跑    ☐
Step 4 packet_gold_overlap.py (Phase B)           ← Feedback/Broad/ConDiag 与 gold context overlap      ☐
D4-10  Run seed regression if ConDiag core touched ← 拍板 D4-6 触发条件；D4-7.1 完成后已重跑 4/4 PASS   ✓
```

不在 D4-1 schema 没定稿前写代码；每个 D4-N 完成后确认再进下一步。
