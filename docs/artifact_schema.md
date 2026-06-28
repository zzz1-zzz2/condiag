# ConDiag Baseline Artifact Schema

**Status:** Day 4 D4-1 已拍板，字段冻结
**Date:** 2026-06-28
**Companion docs:** `baseline_runner_design_v0.2.md` + `adapter_layer.md`

---

## 1. 顶层目录

```
runs/pilot50/<agent>/<baseline>/<instance_id>/
├── attempt_1/
├── intervention/        # optional per baseline
├── attempt_2/           # optional per baseline
├── final/
├── cost.json
└── run_report.json
```

`<agent>` 维度（v0.2 加入）—— 见 `adapter_layer.md`。当前 Pilot50 全部使用 `miniswe`，预留 `agentless` / `openhands` / `swe_agent` 为 v1 扩展。`<baseline>` 命名固定在 agent 内部（不会跨 agent 共享 baseline 名）。

**Baseline 占用矩阵**：

| Baseline | attempt_1 | intervention | attempt_2 | final |
|---|---|---|---|---|
| Base mini-SWE | ✓ | ✗ | ✗ | ✓（= attempt_1） |
| Feedback Retry | ✓ | ✓ | ✓ | ✓（= attempt_2） |
| Broad Expansion (packet_only) | ✓ | ✓ | ✗ | ✓（= attempt_1） |
| Broad Expansion (retry) | ✓ | ✓ | ✓ | ✓（= attempt_2） |
| ConDiag (packet_only) | ✓ | ✓ | ✗ | ✓（= attempt_1） |
| ConDiag (retry) | ✓ | ✓ | ✓ | ✓（= attempt_2） |

---

## 2. attempt_N/ 字段定义（N ∈ {1, 2}）

### 2.1 `raw_trajectory.json`

mini-SWE-agent 原始 message + tool call 流（直接落 agent 的对话上下文）。schema 不重定义，沿用 mini-SWE 原始格式。

**leakage_guard 检查**：禁止出现 `gold_check` / `contextbench_metrics` / `official_eval` / `FAIL_TO_PASS` / `PASS_TO_PASS` 等关键字。

### 2.2 `patch.diff`

agent 最终输出的 patch，统一 diff 格式（`diff --git a/... b/...`）。

### 2.3 `runtime_signals.json`

沿用 build_case_bundle 现有 schema（不重命名）。关键字段：
- `exit_status`、`api_calls`、`n_messages`、`n_assistant_messages`
- `searched_queries`、`search_count`、`viewed_files_count`、`viewed_files`、`viewed_spans`
- `test_runs`、`test_runs_count`、`test_failures_count`
- `final_patch_context_files_count`、`final_patch_context_files`
- 新增字段（retry_trigger 需要）：
  - `runtime_validation_failed: bool`
  - `patch_apply_failed: bool`
  - `timeout: bool`
  - `step_limit_hit: bool`
  - `patch_shape_anomaly: bool`（scope_anomaly_score >= threshold_warning）
  - `evidence_edit_mismatch: bool`

### 2.4 `final_patch_context.json`

```json
{
  "schema_version": "condiag.patch_context.v0",
  "files": [
    {"file": "django/core/management/base.py", "lines": "46-73"},
    {"file": "django/core/management/base.py", "lines": "284-296"}
  ]
}
```

与 ContextBench `<PATCH_CONTEXT>` 对齐。

### 2.5 `local_test_outputs.md`

agent 在 attempt 中跑过的可见 test 输出（markdown 纯文本）。Feedback Retry 的 intervention 直接读这个文件。

### 2.6 `contextbench_metrics.json`

ContextBench evaluate 产物。每个 attempt 一份；final 还有一份指向 selected final patch。

```json
{
  "schema_version": "condiag.contextbench_metrics.v0",
  "instance_id": "django__django-11400",
  "attempt": 1,
  "metrics": {
    "file_coverage": 1.0,
    "file_precision": 0.333,
    "symbol_coverage": 0.66,
    "symbol_precision": 1.0,
    "line_coverage": 0.571,
    "line_precision": 1.0,
    "editloc_recall": 0.0,
    "editloc_precision": 0.0,
    "auc_file": 1.0,
    "redundancy_file": 0.75
  },
  "evaluation_only": true,
  "note": "OFFLINE EVALUATION ONLY; never feed back into retry_trigger or ConDiag runtime"
}
```

### 2.7 `attempt_report.json`

```json
{
  "schema_version": "condiag.attempt_report.v0",
  "baseline": "feedback_retry",
  "instance_id": "django__django-11400",
  "attempt": 1,
  "exit_status": "submitted" | "submitted_with_success" | "errored" | "timeout",
  "started_at": "2026-06-28T12:34:56Z",
  "finished_at": "2026-06-28T12:45:00Z",
  "model": "deepseek-v4-pro",
  "provider": "deepseek",
  "api_calls": 42,
  "n_messages": 87,
  "token_usage": {"prompt": 814675, "completion": 23481, "total": 838156}
}
```

---

## 3. intervention/ 字段定义

### 3.1 `intervention_report.json`（Feedback Retry / Broad Expansion / ConDiag 通用）

```json
{
  "schema_version": "condiag.intervention_report.v0",
  "baseline": "...",
  "instance_id": "...",
  "trigger_reason": "RUNTIME_VALIDATION_FAILURE",
  "trigger_source": "experiments/retry_trigger.py",
  "did_read_gold_check": false,
  "did_read_contextbench_metrics": false,
  "did_read_official_eval": false,
  "did_read_fail_to_pass": false,
  "did_read_pass_to_pass": false,
  "intervention_type": "feedback_only" | "broad_expansion" | "condiag_triage",
  "context_packet_path": "intervention/context_packet.md",
  "selected_evidence_path": "intervention/selected_evidence.json",
  "started_at": "...",
  "finished_at": "..."
}
```

ConDiag 额外字段（Broad Expansion / Feedback Retry 不写）：
```json
{
  "pathology": "OVER_EXPLORE_OVER_EDIT",
  "5r_action": "RESTRAIN",
  "axis_1_context_evidence_types": ["EDIT_SCOPE_EVIDENCE", "DEPENDENCY_CONFIG"],
  "axis_2a_runtime_gap": "NOISY_OVERBROAD",
  "axis_2b_gold_aligned_gap": "GOLD_SCOPE_OVERFLOW",
  "scope_anomaly_score": 2,
  "confidence": 0.7,
  "abstain": false
}
```

### 3.2 `context_packet.md`

注入 attempt_2 的 ContextPacket。三种 baseline 内容不同：

- **Feedback Retry**：local test output + stack trace + failed visible tests + previous patch summary + revise instruction
- **Broad Expansion**：generic expansion（viewed spans + edited neighborhoods + ripgrep top-k + failed test file），**无 pathology / 无 5R 标签**
- **ConDiag**：按 pathology / 5R 生成的 typed ContextPacket（复用 manual-recovery 流程）

### 3.3 `selected_evidence.json`

```json
{
  "schema_version": "condiag.selected_evidence.v0",
  "baseline": "...",
  "instance_id": "...",
  "evidence": [
    {"kind": "file_span", "file": "...", "lines": "...", "source": "viewed" | "neighborhood" | "ripgrep" | "manual_diagnosis"},
    {"kind": "symbol", "value": "...", "source": "..."},
    {"kind": "test", "value": "...", "source": "..."}
  ],
  "total_files": 6,
  "total_lines_estimate": 250
}
```

Broad Expansion 的 `source` 只能是 `viewed | neighborhood | ripgrep | failed_test_file`，**不能**有 `manual_diagnosis`。

### 3.4 `expansion_report.json`（Broad Expansion 专用）

```json
{
  "schema_version": "condiag.expansion_report.v0",
  "baseline": "broad_expansion",
  "instance_id": "...",
  "expansion_sources": {
    "viewed_spans_count": 12,
    "neighborhood_files_count": 5,
    "ripgrep_topk_count": 10,
    "ripgrep_keywords": ["add_subparsers", "ArgumentParser"],
    "failed_test_files_count": 1
  },
  "selection_rule": "lexical_score + path_proximity",
  "token_budget": 4000,
  "actual_tokens": 3812,
  "did_use_condiag_retrieval_executor": false,
  "did_diagnose_5r": false
}
```

---

## 4. final/ 字段定义

### 4.1 `final/patch.diff`

selected final patch。retry baseline → attempt_2 patch；Base → attempt_1 patch。

### 4.2 `final/contextbench_metrics.json`

selected final patch 的 ContextBench metrics。

### 4.3 `final/final_report.json`

```json
{
  "schema_version": "condiag.final_report.v0",
  "baseline": "...",
  "instance_id": "...",
  "selected_attempt": 1 | 2,
  "selection_rule": "base_no_retry_default_attempt_1" | "retry_default_attempt_2" | "best_of_two_by_<metric>",
  "final_patch_path": "final/patch.diff",
  "final_metrics_path": "final/contextbench_metrics.json"
}
```

Day 4 v0 选择规则简单：Base → attempt_1；retry baseline → attempt_2。best-of-two 留待 v1。

---

## 5. 顶层文件

### 5.1 `cost.json`

```json
{
  "schema_version": "condiag.cost.v0",
  "baseline": "feedback_retry",
  "instance_id": "django__django-11400",
  "model": "deepseek-v4-pro",
  "provider": "deepseek",
  "attempts": [
    {"phase": "attempt_1", "api_calls": 42, "prompt_tokens": 814675, "completion_tokens": 23481, "total_tokens": 838156, "wall_time_seconds": 623.5},
    {"phase": "intervention", "api_calls": 1, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "wall_time_seconds": 5.2},
    {"phase": "attempt_2", "api_calls": 38, "prompt_tokens": 920000, "completion_tokens": 21500, "total_tokens": 941500, "wall_time_seconds": 580.1}
  ],
  "total": {"api_calls": 81, "prompt_tokens": 1734675, "completion_tokens": 44981, "total_tokens": 1779656, "wall_time_seconds": 1208.8},
  "estimated_usd": null,
  "pricing_source": null
}
```

`token / api_calls / wall_time` 必填，`estimated_usd` nullable（拍板 4.3）。

### 5.2 `run_report.json`

```json
{
  "schema_version": "condiag.run_report.v0",
  "agent": "miniswe",
  "baseline": "feedback_retry",
  "instance_id": "django__django-11400",
  "mode": "retry",
  "started_at": "2026-06-28T12:34:56Z",
  "finished_at": "2026-06-28T12:55:05Z",
  "has_attempt_1": true,
  "has_intervention": true,
  "has_attempt_2": true,
  "has_final": true,
  "verdict": "completed",
  "validator_status": "ok",
  "failure_reason": null,
  "artifacts": [
    "attempt_1/raw_trajectory.json",
    "attempt_1/patch.diff",
    "...",
    "final/final_report.json"
  ]
}
```

`verdict` ∈ `completed | aborted | leakage_blocked | missing_artifacts`；
`validator_status` ∈ `ok | missing_artifacts | gold_leakage | schema_mismatch`。

---

## 6. Leakage Guard 规则

每个 run 跑完 validator 强制检查：

| 文件 | 检查项 |
|---|---|
| `attempt_*/raw_trajectory.json` | 不含 `gold_check` / `contextbench_metrics` / `FAIL_TO_PASS` / `PASS_TO_PASS` 字符串 |
| `intervention/intervention_report.json` | `did_read_*` 全 false |
| `intervention/context_packet.md` (Broad Expansion) | 不含 5R 标签 / pathology 字段 |
| `intervention/expansion_report.json` (Broad Expansion) | `did_use_condiag_retrieval_executor=false` + `did_diagnose_5r=false` |
| `intervention/selected_evidence.json` (Broad Expansion) | `source` 字段无 `manual_diagnosis` |

违规则 `run_report.json.verdict = leakage_blocked`，该 run 不进入最终汇总。
