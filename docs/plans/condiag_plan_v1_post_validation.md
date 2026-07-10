# ConDiag v1 完整实验计划（Final Executable Version）

**日期**: 2026-06-30
**版本**: plan_v1.0_post_validation
**设定**: Post-Validation / CI-Feedback Repair Setting
**参考 ADR**: docs/adr/ADR-002-post-validation-feedback-setting.md

---

## 第一部分：实验设定与基本定义

### 1.1 实验设定

```
ConDiag: Failure-Guided Context Diagnosis under Post-Validation Repair

我们模拟一个 post-validation feedback loop：
  1. Host Agent attempt_1 产生 patch
  2. 我们（不是真实 CI 系统）运行 SWE-bench validation harness
     （apply test_patch + model_patch, run validation tests）
  3. 将 validation failure output 作为 runtime-visible feedback
     注入 attempt_2 的 context
  4. 所有 retry baseline 收到同一份 failure witness
  5. 唯一变量是 context packet 内容

这不是标准 hidden-test SWE-bench pass@1 setting。
这是"修复失败后，利用验证失败信号诊断缺失上下文并指导二次修复"。
```

严谨表述：We simulate a post-validation feedback loop by running the validation harness ourselves after
attempt_1, then injecting the failure output into attempt_2's context. This mirrors what a CI system would
provide in a real development workflow, but the validation is performed by us, not by an existing CI pipeline.

正确主线：

```
Host Agent attempt_1
→ official validation failure output
→ Failure Witness（结构化失败信号）
→ failure_type → context_type → recovery_intent（诊断缺失上下文类型）
→ targeted retrieval + API navigation（定向检索）
→ context packet（可执行的修复指导）
→ Host Agent attempt_2（tool loop，workspace edit）
→ workspace_git_diff patch
→ official SWE-bench / ContextBench eval
→ rescue matrix
```

### 1.2 与原始课题的对应

原始课题：

```
Patch → Test → Context Diagnosis → Targeted Retrieval → Patch
```

新方案核心链路：

```
attempt_1 patch
→ post-validation harness（我们运行）
→ validation failure output
→ Failure Witness（结构化失败信号）
→ failure_type → context_type → recovery_intent（诊断缺失上下文类型）
→ targeted retrieval + API navigation（定向检索）
→ context packet（可执行的修复指导）
→ Host Agent attempt_2（tool loop，workspace edit）
→ workspace_git_diff patch
→ official eval
```

### 1.3 红线边界

允许暴露给 agent 的信息：
- validation failure output（traceback, assertion message）
- stack trace
- expected vs actual（从 assertion 解析）
- validation command（复现失败的命令）
- test_patch 本身（SWE-bench 评测用的测试补丁，用于运行验证测试）
- public API signature（argparse, functools 等标准库）
- repo-visible source（base commit 上的代码）
- runtime introspection（inspect.signature 等）
- issue text（原始问题描述）
- attempt_1 的 patch summary（改了哪些文件，不是 patch 内容）
- failure witness

**[修正1] 精确禁止列表：**

- gold solution patch（官方正确修复 patch）
- gold context（官方正确上下文范围）
- resolved label（这个 case 是否在 benchmark 上 resolved）
- F2P/P2P 作为 benchmark 语义标签（给 agent 看时只能说 "validation test"，不能说 "F2P test" 或 "fail-to-pass test" 或 "benchmark target test"）
- ContextBench oracle metrics（file_cov, span_cov, EditLoc 等）
- feedback_success_patch（其他 baseline 的成功 patch）
- manual_hindsight_only hint（纯人工后见之明，无运行时 artifact 支持）
- 任何"这是 benchmark 目标测试"的暗示

关键区分：test_patch 是 SWE-bench 评测用的测试补丁，允许用于运行 validation tests（apply 到 repo 后跑测试）。gold solution patch 是官方正确修复 patch，禁止读取。两者不同。

Failure Witness 中必须标注：`source = "post_validation_output"`, `oracle_labels_hidden = true`。

API Navigation Hint 中必须标注 hint_source：
- 允许：`public_api_signature`, `repo_source_signature`, `issue_keyword_api_match`, `runtime_introspection`
- 禁止：`gold_patch`, `feedback_success_patch`, `manual_hindsight_only`, `contextbench_oracle`

---

## 第二部分：Baseline Input Contract（硬规则）

### 2.1 四个 baseline 的输入定义

| baseline | original issue | failure witness | context packet | broad context | API hint | diagnosis routing |
|---|---|---|---|---|---|---|
| plain_rerun | YES | NO | NO | NO | NO | NO |
| feedback_retry | YES | YES | NO | NO | NO | NO |
| broad_expansion | YES | YES | NO | YES | NO | NO |
| condiag_retry | YES | YES | YES | NO | YES | YES |

每个 baseline 的非输入变量全部相同：
- 相同 model（DeepSeek V4-pro / GLM-5）
- 相同 temperature / sampling config
- 相同 max_steps（50）
- 相同 timeout（1800s）
- 相同 clean base repo（base commit）
- 相同 retry runner（host_agent_retry_runner）
- 相同 Host Agent（mini-SWE）
- 相同 patch collection 方式（workspace_git_diff）
- 相同 official eval（同一 Docker image, 同一 validation tests）

### 2.2 代码实现

```python
# experiments/experiment_settings.py

POST_VALIDATION_MODE = True
FAILURE_WITNESS_VISIBLE_TO = ["feedback_retry", "broad_expansion", "condiag_retry"]
PLAIN_RERUN_VISIBLE_TO = []

BASELINE_INPUT_CONTRACT = {
    "plain_rerun": {
        "failure_witness": False,
        "context_packet": False,
        "broad_context": False,
        "api_navigation": False,
        "diagnosis_routing": False,
    },
    "feedback_retry": {
        "failure_witness": True,
        "context_packet": False,
        "broad_context": False,
        "api_navigation": False,
        "diagnosis_routing": False,
    },
    "broad_expansion": {
        "failure_witness": True,
        "context_packet": False,
        "broad_context": True,
        "api_navigation": False,
        "diagnosis_routing": False,
    },
    "condiag_retry": {
        "failure_witness": True,
        "context_packet": True,
        "broad_context": False,
        "api_navigation": True,
        "diagnosis_routing": True,
    },
}

PLAIN_RERUN_CONSTRAINTS = {
    "same_model": True,
    "same_temperature": True,
    "same_max_steps": True,
    "same_timeout": True,
    "same_clean_base": True,
    "same_runner": True,
    "single_sample_estimate": True,  # 标注非总体统计量
}

ALLOWED_HINT_SOURCES = {
    "public_api_signature": "标准库公开 API 的 inspect.signature 输出",
    "repo_source_signature": "repo 内公开函数/类的签名",
    "issue_keyword_api_match": "issue text 中提到的方法名/模块名与 repo symbol 的匹配",
    "runtime_introspection": "通过 inspect/dir/type 等运行时内省获得的信息",
}

FORBIDDEN_HINT_SOURCES = {
    "gold_patch": "官方正确 solution patch",
    "feedback_success_patch": "feedback baseline 的成功 patch",
    "manual_hindsight_only": "纯人工后见之明，无运行时 artifact 支持",
    "contextbench_oracle": "ContextBench 的 gold context 范围",
}

PACKET_V2_CONSTRAINTS = {
    "max_packet_chars": 5000,
    "max_evidence_lines_per_item": 40,
    "max_total_code_lines": 120,
    "min_evidence_score": 0.85,
    "max_anti_patterns": 3,
    "no_taxonomy_enums_in_output": True,
    "no_benchmark_labels_in_output": True,   # [修正1] 禁止 F2P/P2P/fail-to-pass
    "no_oracle_labels": True,
    "failure_witness_section_required_when_available": True,
}

CANONICAL_MATRIX_PATH = (
    "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/"
    "canonical_base_eval_matrix.csv"
)
```

### 2.3 plain_rerun 随机性标注

plain_rerun 的 rescue 率是单样本估计（single-sample estimate），不是总体统计量。
LLM 同 temperature 同 prompt 两次运行结果不同，plain_rerun 可能因采样波动偶发 rescue。

处理方式：
1. 每次运行记录 plain_rerun_run_id
2. 结果表标注 "single_sample_estimate: true"
3. 论文/报告中明确标注此限制
4. 如资源允许，可跑 2-3 次取众数（非阻塞，作为补充实验）

---

## 第三部分：Gate 状态机（9-Gate）

```
Gate 0: Base Attempt
  输入: instance_id, issue_text, repo@base_commit
  执行: mini-SWE attempt_1 (tool loop)
  产出: patch.diff, raw_trajectory.json, runtime_signals.json
  已实现: DONE

Gate 1: Official Eval (attempt_1)
  输入: attempt_1/patch.diff, test_patch, validation tests
  执行: Docker 容器内 apply + test
  产出: base_resolved, raw eval log
  已实现: DONE (smoke_eval_matrix.py + norm_v3_inline.py)
  缺失: raw eval log 未统一保存, canonical_base_eval_matrix.csv 未生成

Gate 2: Failure Witness Builder
  输入: Gate 1 raw eval log（优先）或重新运行 Docker（fallback）
  执行: 从 post-validation output 提取 failure_witness.json
  产出: failure_witness.json per instance
  已实现: TODO

Gate 3: Retry Eligibility
  输入: Gate 1 结果 + Gate 2 结果 + intervention report
  执行: 筛选 eligible first-failed cases
  产出: first_failed_candidate_matrix.csv
  已实现: TODO

Gate 4: Packet Generation
  输入: failure_witness, diagnosis, evidence, api_navigation
  执行: 按 baseline input contract 生成各 baseline 的输入
  产出: context_packet.md (condiag only), failure_witness injection (feedback/broad)
  已实现: PARTIAL（v1 框架有，缺 Failure Witness / API Hint / Do Not Do This / Validation Target sections）

Gate 5: Host-Agent Retry
  输入: context_packet + original issue + failure_witness (per baseline contract)
  执行: mini-SWE attempt_2 (tool loop)
  产出: attempt_2 workspace
  已实现: DONE (MinisweRetryInjectionAdapter)

Gate 6: Patch Collection
  输入: attempt_2 workspace
  执行: git diff HEAD
  产出: patch from workspace_git_diff
  已实现: DONE

Gate 7: Official Eval (attempt_2)
  输入: attempt_2/patch.diff
  执行: 同 Gate 1
  产出: attempt_2_resolved
  已实现: DONE

Gate 8: Rescue Matrix
  输入: Gate 1 + Gate 7 结果, cost 数据
  执行: 汇总统计
  产出: retry_rescue_matrix_v1.csv + packet_to_patch_trace_v1.csv
  已实现: PARTIAL（10 条记录）
```

---

## 第四部分：失败分类

**[修正9] 重命名：去掉 "hidden-failure" 概念**

在 post-validation setting 下，validation failure output 会被提取成 witness，所以不存在 "hidden-failure"。分类改为：

```python
FAILURE_CLASSIFICATION = {
    "post_validation_failure": {
        "condition": "has_failure_witness=True AND failure_type != ''",
        "description": "post-validation harness 产生明确失败输出",
        "suitable_for": ["condiag_retry", "feedback_retry", "broad_expansion"],
        "reason": "有 failure witness 可驱动 diagnosis routing",
    },
    "no_witness_available": {
        "condition": "has_failure_witness=False AND base_resolved=False",
        "description": "validation 测试通过但 official eval 判定 unresolved，"
                     "或无法提取有意义的 failure output",
        "suitable_for": ["plain_rerun"],
        "reason": "无 failure witness，ConDiag 无法做 guided diagnosis，"
                  "只能依赖 agent 自己的随机探索",
    },
    "localization_error": {
        "condition": "has_failure_witness=True AND top_repo_frame "
                     "不在 attempt_1 edited_files 中",
        "description": "错误来源在 agent 没改过的文件",
        "suitable_for": ["condiag_retry", "broad_expansion"],
        "reason": "需要 relocalization",
    },
    "over_edit": {
        "condition": "attempt_1 changed_files > 3 AND p2p_regressed > 0",
        "description": "改了太多文件导致 regression",
        "suitable_for": ["condiag_retry", "broad_expansion"],
        "reason": "RESTRAIN 型 pathology",
    },
    "under_edit": {
        "condition": "attempt_1 changed_files == 1 AND f2p_passed == 0",
        "description": "只改了一个文件但 validation test 仍失败",
        "suitable_for": ["condiag_retry", "feedback_retry"],
        "reason": "方向对但不够，需要更精确的 context",
    },
    "no_runtime_signal": {
        "condition": "attempt_1 submitted_without_tests=True "
                     "AND test_runs=0",
        "description": "agent 没跑测试就提交了",
        "suitable_for": ["plain_rerun", "feedback_retry"],
        "reason": "可能只是运气差，再跑一次可能过",
    },
    "env_anomaly": {
        "condition": "eval_error != ''（patch 无法 apply, Docker 启动失败等）",
        "description": "eval 基础设施问题",
        "suitable_for": [],
        "reason": "排除出实验，修复基础设施后重试",
    },
}
```

---

## 第五部分：核心 Schema 定义

### 5.1 FailureWitness（新增到 condiag/schemas.py）

```python
@dataclass
class FailureWitness:
    """Structured failure signal extracted from post-validation test output.

    IMPORTANT:
      - source is always "post_validation_output"
      - oracle_labels_hidden is always True
      - failed_tests must NOT be labeled as "F2P" or "benchmark target"
        in any agent-facing output
    """
    instance_id: str = ""
    has_failure_witness: bool = False
    failure_type: str = ""
    failed_tests: list = field(default_factory=list)
    error_message: str = ""
    stack_trace: list = field(default_factory=list)
    top_repo_frames: list = field(default_factory=list)
    expected_actual: dict = field(default_factory=dict)
    validation_command: str = ""
    raw_output_path: str = ""           # [修正4] raw eval log 路径（用于溯源）
    raw_output_source: str = ""         # [修正4] "from_eval_log" | "from_docker_run"
    mode: str = "no_witness_available"
    source: str = "post_validation_output"
    oracle_labels_hidden: bool = True
    version: str = "v1"
    builder_version: str = "v1"
```

### 5.2 ApiNavigationHint（新增到 condiag/schemas.py）

```python
@dataclass
class ApiNavigationHint:
    """API navigation hint with provenance tracking.

    Every hint must record its source to prevent hindsight/gold leakage.
    """
    hint_text: str
    hint_source: str
    supporting_artifact: str
    target_symbol: str
    confidence: float = 0.0
    generation_method: str = ""
```

### 5.3 Failure Type → Context Type 路由表

```python
FAILURE_TYPE_ROUTE = {
    "AssertionError": {
        "context_type": "EXPECTED_BEHAVIOR",
        "intent": "RETRIEVE",
        "description": "Agent 的行为不符合预期 → 需要查找预期行为的定义",
        "retrieval_focus": "validation test + sibling expected behavior tests",
    },
    "AttributeError": {
        "context_type": "API_DEFINITION",
        "intent": "RETRIEVE",
        "description": "访问了不存在的属性 → 需要查找正确的 API/symbol",
        "retrieval_focus": "symbol definition + caller usage + parent class",
    },
    "TypeError": {
        "context_type": "INTERFACE_CONSTRAINT",
        "intent": "RETRIEVE",
        "description": "类型不匹配 → 需要查找函数签名和接口约束",
        "retrieval_focus": "function signature + sibling call sites",
    },
    "ImportError": {
        "context_type": "PUBLIC_SYMBOL",
        "intent": "RECONCILE",
        "description": "模块/符号找不到 → 需要检查导出/导入关系",
        "retrieval_focus": "__init__.py + __all__ + import references",
        "anti_pattern": "Do not delete/rename public symbols without checking import references.",
    },
    "ModuleNotFoundError": {
        "context_type": "PUBLIC_SYMBOL",
        "intent": "RECONCILE",
        "description": "模块找不到 → 需要检查模块路径和依赖",
        "retrieval_focus": "module structure + setup.py / pyproject.toml",
        "anti_pattern": "Do not move modules without updating import paths.",
    },
    "IndentationError": {
        "context_type": "SYNTAX_FIX",
        "intent": "RELOCALIZE",
        "description": "缩进错误 → patch 的代码结构有问题",
        "retrieval_focus": "修改位置的上下文缩进",
        "anti_pattern": "Do not mix tabs and spaces; follow the file's existing indentation style.",
    },
    "SyntaxError": {
        "context_type": "SYNTAX_FIX",
        "intent": "RELOCALIZE",
        "description": "语法错误 → patch 破坏了代码结构",
        "retrieval_focus": "修改位置周围的语法上下文",
        "anti_pattern": "Do not generate partial patches that break file syntax.",
    },
    "KeyError": {
        "context_type": "DATA_FLOW",
        "intent": "RETRIEVE",
        "description": "字典键不存在 → 数据流问题",
        "retrieval_focus": "dict construction + expected keys",
    },
    "ValueError": {
        "context_type": "DATA_FLOW",
        "intent": "RETRIEVE",
        "description": "值不合法 → 数据约束问题",
        "retrieval_focus": "value source + validation logic",
    },
    "Regression": {
        "context_type": "REGRESSION_CONSTRAINT",
        "intent": "RECONCILE",
        "description": "新 patch 破坏了已有功能",
        "retrieval_focus": "regression test + previous working code",
        "anti_pattern": "Do not remove behavior that the previous patch already fixed.",
    },
}
```

当 has_failure_witness=False 时，fallback 到 ManualDiagnosis taxonomy（保留现有 diagnosis_normalizer.py 逻辑）。

### 5.4 Do Not Do This 生成规则

```python
ANTI_PATTERN_RULES = [
    {
        "trigger": "factory/override/builder/registry/callback",
        "pattern": "Avoid monkey-patching returned objects; inspect public constructor/customization hooks first.",
    },
    {
        "trigger": "ImportError/ModuleNotFoundError",
        "pattern": "Do not delete/rename public symbols without checking all import references.",
    },
    {
        "trigger": "Regression",
        "pattern": "Do not remove behavior that the previous patch already fixed.",
    },
    {
        "trigger": "over_edit/default",
        "pattern": "Do not modify files without direct failure/issue evidence support.",
    },
    {
        "trigger": "under_edit/default",
        "pattern": "Do not assume a single-class fix is sufficient; audit sibling implementations.",
    },
]
```

生成逻辑：
1. 从 failure_witness.failure_type 匹配 trigger
2. 从 failure_witness.top_repo_frames 的代码上下文匹配 trigger（如 factory pattern）
3. 从 normalized_diagnosis.primary_5r_action 匹配 trigger
4. 取并集，最多 3 条

---

## 第六部分：ContextPacket v2 结构

### 6.1 完整 packet 模板

```markdown
# Retry Context

## Failure Witness
{validation failure 的自然语言描述，不使用 F2P/P2P 术语}
{error_message 最后 200 chars}
Expected: {expected_actual.expected}
Actual: {expected_actual.actual}
Error location: {top_repo_frames[0].file}:{top_repo_frames[0].line} in {top_repo_frames[0].func}

## What Went Wrong
{基于 failure_type 的 plain language diagnosis}
{不用 taxonomy enum，不用 5R 术语}

## Primary Edit Target
- **File**: `{target.file}`
- **Target**: `{target.symbol}` (lines {start}-{end})
- **Goal**: {target.what}

## API / Symbol Navigation Hint
{api_navigation.hint_text}
来源类型: {api_navigation.hint_source}

## Relevant Evidence
{少量高质量证据，每个 <= 40 lines，总计 <= 120 lines}

## Do Not Do This
- {anti_pattern_rule_1}
- {anti_pattern_rule_2}
- {anti_pattern_rule_3}

## Validation Target
Run: {validation_command}

## Retry Instruction
{具体、短、可执行的 instruction}
```

### 6.2 硬限制

```python
PACKET_V2_CONSTRAINTS = {
    "max_packet_chars": 5000,
    "max_evidence_lines_per_item": 40,
    "max_total_code_lines": 120,
    "min_evidence_score": 0.85,
    "max_anti_patterns": 3,
    "no_taxonomy_enums_in_output": True,
    "no_benchmark_labels_in_output": True,   # [修正1] 禁止 F2P/P2P/fail-to-pass
    "no_oracle_labels": True,
    "failure_witness_section_required_when_available": True,
}
```

### 6.3 Diagnosis routing 改造

v2 逻辑：

```python
if has_failure_witness and failure_type:
    FAILURE_TYPE_ROUTE[failure_type] → context_type + intent
    # 用 context_type 驱动 evidence selection 和 packet generation
else:
    # fallback 到 ManualDiagnosis taxonomy（保持向后兼容）
    ManualDiagnosis.pathology → taxonomy 表 → action_family → 5r_action
```

### 6.4 [修正7] 向后兼容

```python
def build_context_packet_md(
    repo_root: Path,
    nd: NormalizedDiagnosis,
    md: ManualDiagnosis,
    rs: RuntimeSignals,
    selected: dict,
    # v2 新增参数 — 全部默认 None，旧调用不崩
    failure_witness: Optional[FailureWitness] = None,
    api_hint: Optional[ApiNavigationHint] = None,
    anti_patterns: Optional[list[str]] = None,
    settings: Optional[dict] = None,
) -> str:
    # 所有 v2 section 用 if guard 保护：
    # if failure_witness and failure_witness.has_failure_witness:
    #     render_failure_witness(...)
    # else:
    #     (跳过，和 v1 行为一致)
```

验收要求：旧调用（不传新参数）产出与 v1 相同的 packet。新调用产出 v2 packet。现有 seed regression 测试不挂。

---

## 第七部分：任务详细规格

### Task 0: Canonicalize Experiment State（前置闸门）

优先级：最高。Task 0 未通过，不允许启动 Task 3/4/5。

目标：消除 artifact 口径冲突，生成唯一真值表

产出文件：`canonical_base_eval_matrix.csv`

字段：
```
instance_id
batch_id
traj_path
patch_path
base_resolved                     # true / false / NOT_EVALUATED
eval_report_path
eval_status                       # EVALUATED / NOT_EVALUATED / ENV_ERROR
f2p_passed, f2p_total
p2p_regressed, p2p_total
patch_apply_ok
patch_chars
submitted_without_tests
test_runs_count
test_failures_count
failure_class                     # post_validation_failure / no_witness_available / ...
raw_eval_log_path                  # [修正4] raw eval log 路径（供 Task 3 复用）
source_of_truth                    # 哪个 eval artifact 是权威来源
method_version                     # v0
```

怎么做：

1. 扫描所有 eval artifact：
   - `/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/repair_smoke_eval_matrix.json`
   - `/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/patch_apply_report.json`
   - `/mnt/d/condiag-artifacts/condiag/v0/case_bundles/*/official_eval.json`
   - `/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/eval_anomaly_inspection.json`
2. 对每个 instance 合并 eval 结果。冲突时优先级：
   - 最新时间戳
   - 最完整的（同时有 F2P 和 P2P 结果）
   - 记录所有来源，标注 source_of_truth
3. [修正4] 对未 eval 的 instance 补跑，同时保存 raw eval log：
   - 运行 eval 时，保存完整 raw output 到：`<runs>/<baseline>/<instance>/attempt_1/eval_raw_output.log`
   - 这份 raw log 供 Task 3 的 from_eval_log() 复用
4. 生成 failure_class 字段
5. 硬规则：后续所有脚本只能读 canonical_base_eval_matrix.csv

```python
# experiments/experiment_settings.py
CANONICAL_MATRIX_PATH = (
    "/mnt/d/condiag-artifacts/condiag/v0/d4_9_batch2_17x4/"
    "canonical_base_eval_matrix.csv"
)
```

验收标准：
- 所有 discovered base_miniswe instance 都有 eval_status
- 每个 instance 的 base_resolved 有且仅有一个值
- 每个 EVALUATED instance 有 raw_eval_log_path
- 能列出 first-failed pool（base_resolved=false 的 instance 列表和数量）
- 能列出每个 failure class 的 instance 数量
- 后续脚本不能直接读其他 eval JSON，只能读 canonical_base_eval_matrix.csv

依赖：无
预估工作量：中（Docker eval + 数据合并，约 2-3 小时 Docker 时间）

---

### Task 1: Define Post-Validation Setting + Failure Witness Visibility Contract

目标：把实验设定和 baseline input contract 写成代码约束

产出文件：
1. `experiments/experiment_settings.py` — 实验设定配置
2. `condiag/schemas.py` — 新增 FailureWitness, ApiNavigationHint dataclass
3. 更新 `condiag/leakage_guard.py` — 新增对 hint_source 和 benchmark label 的检查

怎么做：

1. 创建 `experiments/experiment_settings.py`（内容见第二部分 2.2）
2. 在 `condiag/schemas.py` 新增 FailureWitness 和 ApiNavigationHint（内容见第五部分）
3. 更新 `leakage_guard.py`：

```python
LEAKAGE_BENCHMARK_TERMS = [
    "F2P", "P2P", "fail_to_pass", "pass_to_pass",
    "FAIL_TO_PASS", "PASS_TO_PASS",
    "benchmark target test",
    "gold_check",
    "contextbench_metrics",
]

def check_hint_source(source: str) -> bool:
    if source in FORBIDDEN_HINT_SOURCES:
        raise ConDiagLeakageError(f"hint_source '{source}' is forbidden")
    return True
```

验收标准：
- experiment_settings.py 可 import 无报错
- FailureWitness.from_dict() / to_dict() 序列化正确
- ApiNavigationHint 同上
- check_hint_source("gold_patch") 抛出 ConDiagLeakageError
- check_hint_source("public_api_signature") 通过

依赖：无
预估工作量：低

---

### Task 2: Implement handle_plain_rerun

目标：新增 plain_rerun baseline handler

**[修正5] 架构澄清：**
- handle_plain_rerun 在 baseline_handlers.py 中负责生成 intervention artifacts（标记 should_retry=True, packet_source="none"）
- 真正的 attempt_2 执行由 host_agent_retry_runner.py 统一处理，不新建独立执行路径
- host_agent_retry_runner 检测到 packet_source="none" 时，不注入任何 context packet

产出文件：
1. `experiments/baseline_handlers.py` — 新增 handle_plain_rerun
2. `experiments/artifact_validator.py` — 新增 plain_rerun 的 REQUIRED_ARTIFACTS
3. `experiments/host_agent_retry_runner.py` — 确认 packet_source="none" 的分支已处理

怎么做：

baseline_handlers.py 新增：

```python
def handle_plain_rerun(
    run_dir: Path,
    instance_id: str,
    mode: str,
    adapter,
    config: dict,
) -> dict:
    """Plain rerun: no context packet, no failure witness, no broad.

    Generates minimal intervention artifacts so host_agent_retry_runner
    can pick it up. The runner checks packet_source="none" and skips
    all context injection.
    """
    # 1. Copy attempt_1 from base_miniswe (same as other handlers)
    # 2. Generate intervention/intervention_report.json:
    #    {
    #        "should_retry": True,
    #        "packet_source": "none",
    #        "context_packet_kind": "none",
    #        "trigger_type": "PLAIN_RERUN",
    #    }
    # 3. NO context_packet.md
    # 4. NO failure_witness.json
    # 5. NO retry_trigger_result.json with complex logic
```

host_agent_retry_runner.py 确认：
```python
# 在 build_retry_input() 中：
# if request.context_packet_path is None or not request.context_packet_path.is_file():
#     packet_content = ""
#     # plain_rerun falls through here — no context injected
```

验收标准：
- baseline_runner --baseline plain_rerun 能生成 intervention artifacts
- host_agent_retry_runner --baseline plain_rerun --instance <id> 能启动 attempt_2
- attempt_2 的 task_message 中没有 "Additional Context" section
- attempt_2 的 task_message 中没有 "Failure Witness" section
- validate_host_agent_run 返回 valid
- patch_source = workspace_git_diff
- artifact_validator 对 plain_rerun 通过

依赖：无
预估工作量：低

---

### Task 3: Implement failure_witness_builder.py

优先级：核心任务。

**[修正1] 术语规范**：所有 agent-facing 内容使用 "validation test" / "validation failure output" / "validation command"。不使用 "F2P" / "fail-to-pass" / "benchmark target test"。

**[修正4] 优先复用已有 raw eval log**：

```python
# experiments/failure_witness_builder.py

def build_failure_witness(
    instance_id: str,
    # 优先从已有 raw eval log 解析
    raw_eval_log_path: Optional[Path] = None,
    # 缺失时重新运行 Docker
    patch_path: Optional[Path] = None,
    docker_image: Optional[str] = None,
    test_patch: Optional[str] = None,
    validation_tests: Optional[list] = None,  # [修正1] 不叫 f2p_tests
    repo_path_in_container: str = "/testbed",
) -> FailureWitness:
    """Build FailureWitness from eval output.

    Resolution order:
    1. If raw_eval_log_path exists and is readable → parse from_eval_log()
    2. Else → run Docker and parse from_docker_run()

    Output is NEVER exposed to agent as benchmark terminology.
    """
```

from_eval_log() 实现：

```python
def from_eval_log(log_path: Path) -> FailureWitness:
    """Parse failure witness from existing official eval raw output.

    Looks for standard test failure patterns in the log:
      - AssertionError: ...
      - AttributeError: '...' object has no attribute '...'
      - TypeError: ...
      - Traceback (most recent call last):
      - FAILED ... - ...

    Returns FailureWitness with raw_output_source="from_eval_log".
    """
    raw = log_path.read_text(encoding="utf-8", errors="ignore")
    failure_type = _parse_failure_type(raw)
    stack_trace = _parse_stack_trace(raw)
    top_repo_frames = _extract_top_repo_frames(stack_trace)
    failed_tests = _parse_failed_test_names(raw)
    expected_actual = _parse_expected_actual(raw)

    has_witness = bool(failure_type or failed_tests or expected_actual)

    return FailureWitness(
        instance_id="",  # filled by caller
        has_failure_witness=has_witness,
        failure_type=failure_type,
        failed_tests=failed_tests,
        error_message=raw[-500:] if raw else "",
        stack_trace=stack_trace,
        top_repo_frames=top_repo_frames,
        expected_actual=expected_actual,
        validation_command=_infer_validation_command(raw),
        raw_output_path=str(log_path),
        raw_output_source="from_eval_log",
        mode="post_validation_failure" if has_witness else "no_witness_available",
    )
```

from_docker_run() 实现（fallback）：

```python
def from_docker_run(
    instance_id: str,
    patch_path: Path,
    docker_image: str,
    test_patch: str,
    validation_tests: list,  # [修正1]
    repo_path_in_container: str = "/testbed",
) -> FailureWitness:
    """Run validation harness in Docker and extract failure witness.

    Steps:
    1. Start Docker container
    2. git apply test_patch
    3. git apply model_patch (attempt_1)
    4. Run validation tests  [修正1] 不叫 "run F2P tests"
    5. Collect raw output
    6. Parse failure witness
    7. Save raw output as eval_raw_output.log for future reuse

    IMPORTANT: The test names and failure output must not be labeled
    as "F2P" or "benchmark target" in any agent-facing context.
    """
    # ... (Docker 交互，同 smoke_eval_matrix.py 模式)
    # save raw output to:
    #   <runs>/<baseline>/<instance>/attempt_1/eval_raw_output.log
    # then call from_eval_log() on saved path
```

内部解析函数（agent 不可见）：

```python
def _parse_failure_type(output: str) -> str:
    """Extract primary error type. Internal use only."""
    m = re.search(r"(\w+Error):", output)
    if m:
        return m.group(1)
    m = re.search(r"(\w+Exception):", output)
    if m:
        return m.group(1)
    return ""

def _parse_stack_trace(output: str, repo_root: str = "/testbed") -> list:
    """Parse traceback into structured frames."""
    frames = []
    for m in re.finditer(r'File "(.*?)", line (\d+), in (\w+)', output):
        frames.append({
            "file": m.group(1),
            "line": int(m.group(2)),
            "func": m.group(3),
            "repo_frame": m.group(1).startswith(repo_root),
        })
    return frames

def _extract_top_repo_frames(stack_trace: list) -> list:
    """Return topmost repo frames."""
    return [f for f in stack_trace if f["repo_frame"]][:3]

def _parse_failed_test_names(output: str) -> list:
    """Extract test names from FAILED lines."""
    # Matches: FAILED test_name (module.path) - ...
    tests = []
    for m in re.finditer(r"FAILED (.+?) -", output):
        tests.append(m.group(1).strip())
    return tests

def _parse_expected_actual(output: str) -> dict:
    """Extract expected vs actual from assertion output."""
    m = re.search(r"AssertionError: (.+?) != (.+?)$", output, re.MULTILINE)
    if m:
        return {"expected": m.group(1).strip(), "actual": m.group(2).strip()}
    m = re.search(r"expected=([^,\n]+)", output)
    if m:
        expected = m.group(1).strip()
        m2 = re.search(r"actual=([^,\n]+)", output)
        actual = m2.group(1).strip() if m2 else "unknown"
        return {"expected": expected, "actual": actual}
    return {}
```

Django vs pytest 适配：

```python
def _is_django_instance(instance_id: str) -> bool:
    return instance_id.startswith("django__django-")

def _run_validation_in_docker(container, iid, tests, is_django):
    """Run validation tests, adapting for framework."""
    if is_django:
        specs = " ".join(_swb_to_django(t) for t in tests)
        cmd = f"cd /testbed && python tests/runtests.py --verbosity=2 {specs} 2>&1"
    else:
        test_file = _infer_test_file(tests[0] if tests else "")
        k_expr = " or ".join(tests)
        cmd = f"cd /testbed && python -m pytest {test_file} -k '{k_expr}' -v --tb=long 2>&1"
    # docker exec ...
```

验收标准：
- django-16454 产出 failure_witness.json，has_failure_witness=True
- raw_output_source = "from_eval_log" 或 "from_docker_run"
- source = "post_validation_output"
- oracle_labels_hidden = True
- packet 输出中不出现 "F2P"/"P2P"/"fail_to_pass" 术语
- 人工抽检 >= 30% 的 failure_witness.json，确认 failure_type 与原始 test output 一致
- 当 raw_eval_log 已存在时，不启动 Docker（复用已有结果）

依赖：Task 0（需要 canonical matrix + raw_eval_log_path）
预估工作量：高（4-6 小时开发）

---

### Task 4: Implement API Navigation with hint_source

**[修正6] 扩展输入来源**：API hint 不只靠 failure_witness.top_repo_frames，还要综合多个信号。

输入：

```python
def generate_api_hint(
    failure_witness: Optional[FailureWitness],
    edit_target: Optional[dict],
    issue_text: str,
    selected_evidence: Optional[dict],
    repo_root: Path,
    attempt_1_patch_summary: Optional[str],
) -> Optional[ApiNavigationHint]:
    """Generate API navigation hint from multiple signals.

    Input priority:
    1. failure_witness.top_repo_frames (primary)
    2. issue_text keyword matches (secondary)
    3. edit_target file + symbol (tertiary)
    4. selected_evidence context (supplementary)
    5. attempt_1_patch_summary (edge case)

    Output: ApiNavigationHint with hint_source in ALLOWED_HINT_SOURCES.
    """
```

策略实现：

```python
def _hint_from_stdlib(error_frames, repo_root):
    """Strategy 1: If error involves stdlib, hint at stdlib extension point.

    Example: target file imports argparse, error in argparse code
    → hint at argparse.ArgumentParser.add_subparsers() customization.
    """
    # 1. Find imports in target file
    # 2. If imported module is stdlib, inspect its public API
    # 3. Generate hint from inspect.signature

def _hint_from_class_override(edit_target, repo_root):
    """Strategy 2: If target overrides parent method, hint at parent.

    Example: CommandParser overrides ArgumentParser.add_subparsers
    → check if parent has customization parameters.
    """
    # 1. Read target class, find parent
    # 2. Check if overridden method has customization in parent
    # 3. hint_source = "repo_source_signature"

def _hint_from_issue_keywords(issue_text, repo_root):
    """Strategy 3: Match issue text keywords to repo symbols.

    Example: issue mentions "subparser" and "custom parser class"
    → check argparse subparsers mechanism.
    """
    # 1. Extract keywords from issue_text
    # 2. Search repo for matching symbols
    # 3. hint_source = "issue_keyword_api_match"

def _hint_from_introspection(edit_target, repo_root):
    """Strategy 4: Runtime introspection on target symbol.

    Uses inspect.signature / inspect.getmembers in Docker container.
    """
    # 1. docker exec ... python3 -c "import inspect; ..."
    # 2. hint_source = "runtime_introspection"
```

验收标准：
- django-16454 产出 hint，hint_source = "public_api_signature" 或 "repo_source_signature"
- hint_source 不在 FORBIDDEN_HINT_SOURCES 中
- 每个 hint 有 supporting_artifact 非空
- hint 文本不包含 gold patch 代码
- 对 hint_source="manual_hindsight_only" 的 attempt 抛出 warning 或 error

依赖：Task 3（需要 failure_witness）
预估工作量：中（3-4 小时）

---

### Task 5: Packet Builder v2

目标：改造 context_packet_builder.py，新增 4 个 section + diagnosis routing 改造

产出文件：
1. `condiag/context_packet_builder.py` — 主要修改

新增参数（[修正7] 全部默认 None）：

```python
def build_context_packet_md(
    repo_root: Path,
    nd: NormalizedDiagnosis,
    md: ManualDiagnosis,
    rs: RuntimeSignals,
    selected: dict,
    failure_witness: Optional[FailureWitness] = None,
    api_hint: Optional[ApiNavigationHint] = None,
    anti_patterns: Optional[list[str]] = None,
    settings: Optional[dict] = None,
) -> str:
```

Section 渲染逻辑：

```python
def build_context_packet_md(...):
    lines = []

    # 1. Header
    lines.append("# Retry Context")

    # 2. Failure Witness (v2 新增，仅当 failure_witness 可用时渲染)
    if failure_witness and failure_witness.has_failure_witness:
        lines.append("## Failure Witness")
        # 用 "validation test" 术语，不用 F2P/P2P [修正1]
        lines.append(f"**Validation failure**: {failure_witness.error_message[:200]}")
        if failure_witness.expected_actual:
            lines.append(f"Expected: {failure_witness.expected_actual.get('expected', '?')}")
            lines.append(f"Actual: {failure_witness.expected_actual.get('actual', '?')}")
        if failure_witness.top_repo_frames:
            top = failure_witness.top_repo_frames[0]
            lines.append(f"Error location: `{top['file']}:{top['line']}` in `{top['func']}`")
        lines.append("")

    # 3. What Went Wrong (v2: 基于 failure_type，不用 taxonomy)
    if failure_witness and failure_witness.has_failure_witness:
        ft = failure_witness.failure_type
        route = FAILURE_TYPE_ROUTE.get(ft, {})
        lines.append("## What Went Wrong")
        lines.append(route.get("description", "The previous attempt did not resolve the issue."))
        lines.append("")
    else:
        # v1 fallback
        lines.append("## What Went Wrong")
        lines.append(_plain_diagnosis(nd, rs))
        lines.append("")

    # 4. Primary Edit Target (v2: failure witness 驱动优先)
    edit_target = _primary_edit_target_v2(failure_witness, nd, selected, repo_root)
    if edit_target:
        lines.append("## Primary Edit Target")
        lines.append(f"- **File**: `{edit_target['file']}`")
        if edit_target.get("symbol"):
            lines.append(f"- **Target**: `{edit_target['symbol']}`")
        lines.append("")

    # 5. API Navigation Hint (v2 新增)
    if api_hint:
        lines.append("## API / Symbol Navigation Hint")
        lines.append(api_hint.hint_text)
        lines.append("")

    # 6. Relevant Evidence (保留 v1 逻辑)

    # 7. Do Not Do This (v2 新增)
    if anti_patterns:
        lines.append("## Do Not Do This")
        for ap in anti_patterns[:3]:
            lines.append(f"- {ap}")
        lines.append("")

    # 8. Validation Target (v2 新增)
    if failure_witness and failure_witness.validation_command:
        lines.append("## Validation Target")
        lines.append(f"Run: `{failure_witness.validation_command}`")
        lines.append("")

    # 9. Retry Instruction (v2: 引用 failure witness + api hint)
    instruction = _build_retry_instruction_v2(failure_witness, api_hint, edit_target)
    lines.append("## Retry Instruction")
    lines.append(instruction)
    lines.append("")

    # 10. Guidelines (保留 v1)
    lines.append("## Guidelines")
    lines.append("- Make the smallest change that fixes the issue.")
    lines.append("- Run the validation tests to verify before submitting.")
    lines.append("")

    return "\n".join(lines)
```

_primary_edit_target_v2：

```python
def _primary_edit_target_v2(failure_witness, nd, selected, repo_root):
    """v2: failure witness 的 top_repo_frame 优先，evidence fallback。"""
    if failure_witness and failure_witness.has_failure_witness:
        for frame in failure_witness.top_repo_frames:
            file = frame["file"].replace("/testbed/", "")
            symbol = _find_enclosing_symbol(repo_root, file, frame["line"])
            if symbol:
                return {
                    "file": file,
                    "symbol": symbol,
                    "start_line": max(1, frame["line"] - 10),
                    "end_line": frame["line"] + 30,
                    "what": f"Fix the error in `{symbol}` at {file}:{frame['line']}",
                    "source": "failure_witness_top_frame",
                }
    # v1 fallback
    return _primary_edit_target(nd, selected, repo_root)
```

验收标准：
- 旧调用（不传新参数）产出与 v1 相同
- 新调用（传 failure_witness + api_hint）产出 v2 packet
- packet < 5000 chars
- 不包含 taxonomy enum（REHYDRATE/RETRIEVE/RECONCILE/RESTRAIN/RELOCALIZE）
- 不包含 benchmark 标签（F2P/P2P/fail_to_pass/pass_to_pass）
- 包含 Failure Witness section
- 包含 API Navigation Hint
- 包含 Do Not Do This section
- 包含 Validation Target section
- 5-flow seed regression 全部 PASS
- leakage_guard.py 对 v2 packet 输出通过

依赖：Task 3, Task 4
预估工作量：中（3-4 小时）

---

### Task 6: Regression Mini-Suite

**[修正8] 必须包含全部 4 个 baseline。**

目标：验证 packet v2 不只是过拟合 django-16454

三类 case：

| 类别 | instance | 目的 |
|---|---|---|
| Target case | django-16454 | feedback wins, ConDiag loses → 期望 condiag_v2 wins |
| Safety case | 一个 unrelated first-failed instance | 确保不伤害 |
| NOOP case | 一个 base already-resolved instance（如 django-11099） | 确保不乱干预 |

每个 case 跑 4 个 baseline：plain_rerun / feedback_retry / broad_expansion / condiag_retry

同时：跑现有 5-flow seed regression + NOOP test + leakage_guard

验收标准：
- target case：condiag_v2 resolved（理想），至少不比 v0 差
- safety case：condiag_v2 不比 plain_rerun 差
- NOOP case：condiag_v2 正确 NOOP
- 5-flow seed regression 全部 PASS
- leakage_guard 全部 PASS
- 结果表包含全部 4 个 baseline（不能漏 broad）

依赖：Task 0, Task 2, Task 3, Task 4, Task 5
预估工作量：中（Docker eval 时间约 3-4 小时）

---

### Task 7: Full First-Failed Retry Matrix

目标：在全部 eligible first-failed cases 上跑 4 个 baseline

流程：
1. 从 canonical_base_eval_matrix.csv 读 base_unresolved
2. 过滤 eligible
3. 对每个 eligible case 跑 4 个 baseline
4. official eval 每个 attempt_2
5. 收集 ContextBench 过程指标

产出文件：`retry_rescue_matrix_v1.csv`

```
instance_id,
base_resolved,
failure_class,
plain_rerun_resolved, plain_rerun_tool_calls, plain_rerun_wall_time, plain_rerun_packet_chars,
plain_rerun_patch_chars,
feedback_resolved, feedback_tool_calls, feedback_wall_time, feedback_packet_chars, feedback_patch_chars,
broad_resolved, broad_tool_calls, broad_wall_time, broad_packet_chars, broad_patch_chars,
condiag_resolved, condiag_tool_calls, condiag_wall_time, condiag_packet_chars, condiag_patch_chars,
condiag_unique_rescue,
method_version, context_packet_version, failure_witness_version, api_navigation_version,
```

核心指标：
- rescue = base_resolved=false AND retry_resolved=true
- condiag_unique = base failed AND plain failed AND feedback failed AND broad failed AND condiag resolved

验收标准：
- 所有 eligible case 都有 4 个 baseline 的结果
- 每个结果有 version 字段
- 能计算 rescue rate × 4 baselines + unique rescue

依赖：Task 0, Task 1, Task 2, Task 3, Task 5
预估工作量：高（取决于 first-failed pool 大小）

---

### Task 8: Packet-to-Patch Trace + Cost + ContextBench

目标：解释 ConDiag 成功/失败原因

产出文件：`packet_to_patch_trace_v1.csv`

```
instance_id,
baseline, method_version,
target_file_hit,
target_symbol_hit,
api_hint_mentioned,
patch_uses_hint,
anti_pattern_avoided,
failure_witness_mentioned,
resolved,
contextbench_file_overlap,
contextbench_line_overlap,
packet_chars,
patch_chars,
tool_calls,
wall_time_seconds,
cost_estimate_usd,
```

验收标准：
- 能回答"ConDiag 没过是因为 agent 没看 hint / 看了不会用 / hint 本身错了"

依赖：Task 7
预估工作量：低-中

---

## 第八部分：依赖图与执行顺序

```
Phase 0（可并行）:
  Task 0: canonical_base_eval_matrix     [中] ← 前置闸门
  Task 1: experiment_settings + schemas [低]

Phase 1（依赖 Phase 0）:
  Task 2: handle_plain_rerun            [低]

Phase 2（依赖 Phase 1）:
  Task 3: failure_witness_builder        [高] ← 核心任务
  Task 4: api_navigation                [中] ← 可与 Task 3 尾部并行

Phase 3（依赖 Phase 2）:
  Task 5: packet builder v2              [中]

Phase 4（依赖 Phase 3）:
  Task 6: regression mini-suite          [中] ← 全部 4 baseline

Phase 5（依赖 Phase 4）:
  Task 7: full first-failed retry matrix [高] ← 全部 4 baseline
  Task 8: packet-to-patch trace          [低-中]
```

硬闸门：

- Task 0 未完成 → 不允许启动 Task 3/4/5
- Task 3 未完成 → 不允许启动 Task 5
- Task 5 未完成 → 不允许启动 Task 6/7
- Task 6 safety case 失败 → 不允许启动 Task 7

---

## 第九部分：安全回归测试

每次修改核心模块后必须跑：

| 测试 | 验证内容 |
|---|---|
| 5-flow seed regression | 4 recovery flow + NOOP 不被破坏 |
| Host-Agent protocol test | tool_calls > 0, patch_source = workspace_git_diff |
| Leakage guard test | 不含 gold/contextbench/oracle/F2P/P2P 关键词 |
| Packet no-taxonomy test | packet 输出不含 5R enum |
| Packet no-benchmark-label test | packet 输出不含 F2P/P2P/fail-to-pass |
| Artifact validator smoke | required artifacts 存在 |
| API hint source check | hint_source 不在 FORBIDDEN 列表 |
| Backward compatibility test | 不传新参数的旧调用产出与 v1 相同 |

---

## 第十部分：Versioning 规则

```python
RESULT_VERSION_FIELDS = {
    "method_version": "v0" | "v1",
    "context_packet_version": "v0" | "v1" | "v2",
    "failure_witness_version": "v1" | "none",
    "api_navigation_version": "v1" | "none",
    "eval_version": "smoke_eval_v3",
    "retry_runner_version": "v1",
    "plan_version": "plan_v1.0_post_validation",
}
```

---

## 第十一部分：成本指标

所有 matrix 必须包含：

```python
COST_FIELDS = {
    "tool_calls": "int",
    "wall_time_seconds": "float",
    "api_calls": "int",
    "packet_chars": "int",
    "patch_chars": "int",
    "cost_estimate_usd": "float",
}
```

---

## 第十二部分：ContextBench 过程指标连接

```python
CONTEXTBENCH_METRICS = {
    "file_overlap": "attempt_2 访问文件与 gold context 重叠",
    "block_overlap": "attempt_2 访问 code block 与 gold context 重叠",
    "line_overlap": "attempt_2 访问行与 gold context 重叠",
    "evidence_drop": "attempt_1 看过但 attempt_2 没看的 evidence",
    "precision": "访问 context 中 gold context 占比",
    "recall": "gold context 中被访问占比",
}
```

---

## 第十三部分：broad_expansion 在新方案中的位置

broad_expansion 保留为核心 baseline，不降级。

与 ConDiag 的区别：
- broad_expansion: failure witness + generic/lexical expansion，无 typed diagnosis
- condiag_retry: failure witness + failure_type routing + targeted retrieval + API hint

broad 回答"是不是多塞上下文就行"。如果 broad ≈ condiag，说明 diagnosis routing 无额外价值。

---

## 第十四部分：已知风险和缓解

| 风险 | 缓解 |
|---|---|
| Docker 镜像拉取失败 | 用已有本地镜像，见 feedback_condiag_docker_mirror.md |
| patch.diff truncated | norm_v3_inline.py 容错 |
| failure_type 解析不准 | 人工抽检 >= 30% |
| API hint 变成隐形 gold | 强制 hint_source + FORBIDDEN_HINT_SOURCES |
| plain_rerun 采样波动 | 标注 single-sample estimate |
| 过拟合 django-16454 | Task 6 强制 3 类 case |
| Django 测试框架适配 | 已有 runtests.py 适配 |
| artifact 口径冲突 | Task 0 唯一真值表 + 后续脚本只能读它 |
| F2P/P2P 术语泄漏 | leakage_guard 新增 benchmark term 检查 |

---

## 修正索引

| 编号 | 修正内容 | 影响任务 |
|---|---|---|
| 修正1 | 禁止 F2P/P2P 作为 agent 可见术语 | Task 3, Task 5, leakage_guard |
| 修正2 | 精确区分 test_patch（允许）vs gold solution patch（禁止） | 1.3 红线 |
| 修正3 | Task 0 是前置闸门，未通过禁止 Task 3/4/5 | 依赖图 |
| 修正4 | failure_witness_builder 优先复用 raw eval log | Task 3 |
| 修正5 | plain_rerun 通过 baseline_handlers 生成 artifact + runner 统一执行 | Task 2 |
| 修正6 | API hint 输入来源扩展到 issue_text + evidence + patch_summary | Task 4 |
| 修正7 | packet builder 新参数全部默认 None，旧调用不崩 | Task 5 |
| 修正8 | Task 6 必须包含全部 4 个 baseline（含 broad） | Task 6 |
| 修正9 | hidden-failure → no_witness_available（post-validation setting 下） | 第四部分 |

---

## ChangeLog

| Date | Version | Change |
|---|---|---|
| 2026-06-30 | plan_v1.0_post_validation | Initial detailed version. 9-Gate state machine (Gate 6 split: Patch Collection + Official Eval → Gate 6/7, Rescue Matrix → Gate 8). Full schema definitions, code implementations, task specs. Aligned with ADR-002. |
