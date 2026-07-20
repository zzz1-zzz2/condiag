# ConDiag 审计台账与修复计划（第一轮）

审计对象：GitHub `master`（`359518b...`）——当前最新提交，无更新
审计日期：2026-07-19
审计方式：静态代码审计 + 本地产物检查

> ⚠️ 阅读前说明：本文件是**第一轮审计**的正式记录，不代表仓库全部问题。后续 9 项补充问题列在第七节。当前已知问题至少有 46 项。

---

## 总览

| 类别 | 数量 | 判定标准 |
|------|------|---------|
| **第一轮审计问题** | **37 项** | 见以下各节 |
| 后续补充问题 | **至少 9 项** | 见第七节 |
| **当前已知问题** | **至少 46 项** | |
| 其中实验完全阻断项 | **至少 12 项** | |
| 当前可用于论文的 paired SF/CD 结果 | **0** | 全部作废 |
| 可保留的独立组件与旧 baseline | 有，需分别审计 | 见第八节 |

---

## 一、阻断级问题（B1–B12）

### B1. API Key 明文泄露（⚠️ 当前 master 尚未修复）

**已确认：**
- 当前 `master` 的 `run_canary.py` 仍包含明文 Key：`sk-9236ebc647c24f44bbb6fa47b24bd67b`
- Key 已进入 Git 历史（自 `078e3ec` 起）
- `experiments/v2c_entry.py` 没有硬编码 Key（依赖 LiteLLM 环境变量链），但**没有启动时显式校验** `DEEPSEEK_API_KEY`

**修复优先级最高：** 撤销旧 Key → 即使清理 Git 历史也绝不能恢复使用旧 Key → Git `filter-repo` 清理历史 → 强制环境变量入口检测 → `.gitignore` 加入 `.env`

---

### B2. 正式入口仍使用错误 Prompt

**位置：** [`experiments/v2c_entry.py:39-42`](experiments/v2c_entry.py#L39-L42)

```python
system_template="You are a software engineer..."
instance_template="{{task}}"
max_tokens=1024
```

无标准工作流（分析→复现→编辑→验证→提交），`max_tokens=1024` 不够生成 patch。`run_canary.py` 已独自修正但正式入口未同步。两套 Agent factory、冲突的模型名和 max_tokens。

---

### B3. Patch 完整性门禁缺失

**位置：** [`condiag/experiment.py:85-88`](condiag/experiment.py#L85-L88)

只检查 `termination_reason == "submitted"`，不检查 patch 内容。

**修复不应一刀切：** 少数 issue 本身就是配置问题（如构建或依赖类）。门禁应为分级结果：

```
VALID_SOURCE_PATCH         ✅ 正常源码改动
VALID_CONFIG_PATCH         ✅ 配置 issue 场景下允许
INVALID_EMPTY_PATCH        ❌ 空提交
INVALID_TEST_ONLY_PATCH    ❌ 只改测试文件
INVALID_ARTIFACT_PATCH     ❌ 只含 patch.txt/reproduction 等临时文件
SUSPICIOUS_CONFIG_ONLY     ⚠️ 触发人工审计
```

---

### B4. 提交内容与评测 Patch 不一致

**代码层面确认：** runner 没有使用 `Submitted` 异常中的 submission 作为评测 Patch，改为从 workspace 执行 `git diff --binary <base_commit>`（含 `git add -N .`）。

**实例层面观察（Astropy canary）：** `trajectory submission=""`，但不是所有实例的必然定律。

`git add -N .` 可能将 `patch.txt`、reproduction scripts、临时 notes 等未跟踪文件纳入 diff，导致评测 Patch 与 Agent 自认为提交的 Patch 不一致。

**修复：** 三分 Patch 校验——submitted SHA / workspace SHA / evaluation SHA，不一致则 RUN_INVALID。

---

### B5. Round 2 Workspace 恢复按代码契约错误

**位置：** [`condiag/branch_runner.py:111-121`](condiag/branch_runner.py#L111-L121)

```python
subprocess.run(
    ["docker", "cp", "-", f"{container_id}:/tmp/restore.diff"],
    input=patch_text, ...
)
```

`docker cp - CONTAINER:DEST` 的 stdin 必须是 **tar archive**，不是普通 diff 文本。同时不检查 `docker cp` 或 `git apply` 的 return code，异常被捕获后实验继续。

**修复方案（不限于 tar）：** 方案 A：宿主临时文件 → `docker cp`；方案 B：Base64 注入 → 容器内解码。之后必须 `git apply --check` → `git apply` → 验证恢复后 SHA。

---

### B6. Checkpoint 不是真实持久状态

**位置：** [`condiag/checkpoint.py:105-110`](condiag/checkpoint.py#L105-L110)

实验中 `_Stub` 的 `container_id=""`，`_get_cwd()` 回退到 `os.getcwd()`（宿主进程目录）。

**结果可能有两种：**
1. 宿主当前目录不是 Git 仓库 → workspace patch 为空；
2. 宿主当前目录恰好是 ConDiag 仓库 → 错误保存 ConDiag 自己的 workspace diff。

后者比"永远为空"更危险——会引入静默数据污染。

`_write()` 只保存最近 100 条消息。`fork_agent()` 只恢复消息和计数器，不恢复工作区。

---

### B7. 工作区公平性检查恒成立

**位置：** [`condiag/experiment.py:151-152`](condiag/experiment.py#L151-L152)

```python
out.sf_workspace_sha = _sha(r1.patch_text)  # 同一变量
out.cd_workspace_sha = _sha(r1.patch_text)  # 永远相等
```

不从容器内 `git diff` 提取真实 SHA。

---

### B8. 实验产物会被重跑覆盖

**位置：** [`condiag/experiment.py:69-70`](condiag/experiment.py#L69-L70)

```python
inst_dir = output_dir / instance_id  # 无 episode_run_id
```

文件名固定，与官方 harness 日志（用时间戳 run_id）无法关联。

---

### B9. DiagnoserCore 未接入主流程

**位置：** [`condiag/experiment.py:118-121`](condiag/experiment.py#L118-L121)

硬编码旧的 `DiagnosisPromptBuilder`。新的 `condiag.diagnosis.diagnoser_core.DiagnoserCore` 从未被调用（仅独立脚本测试过）。

---

### B10. 新版 FailureWitness 结构化信号被丢弃

**位置：** [`condiag/evaluators/official_harness.py:48-56`](condiag/evaluators/official_harness.py#L48-L56)

`_extra` 包含所有新版结构化信号（error_types、error_messages 等），但 `to_dict()` 显式排除。`experiment.py` 调 `fw.to_dict()` → 所有结构化信号在进入流程前消失。

---

### B11. 官方 Harness 硬编码 pytest 提取器

**位置：** `condiag/evaluators/official_harness.py:192`

硬编码 `pytest_extractor`，不为 Django dispatch。统一入口 `signals.extract_test_log()` 已实现但未被使用。

---

### B12. 无真正的 Targeted Acquisition

`condiag/acquisition/` 不存在。CD 分支只比 SF 多一段自然语言诊断 message。当前 ConDiag = Feedback + extra guidance prompt。

---

## 二、高风险问题（H1–H13）

| # | 问题 | 状态 |
|---|------|------|
| H1 | `_failure_summary()` 无 `return`，诊断 prompt 丢失整个 Failure Summary | ✅ 确认 |
| H2 | 主流程传入空 `TrajectorySnapshot()`（`viewed_files=[]`） | ✅ 确认 |
| H3 | FW 传给 Round 2 只含 failed_tests 和 error_message，无栈帧 | ✅ 确认 |
| H4 | 多失败不聚类，`call_chains` 实现为 `pass` | ✅ 确认 |
| H5 | `_cap_total_size` 逐个删除可能破坏 tool_call 配对 | ✅ 确认 |
| H6 | Branch builder 多 tool 响应只补第一个 | ✅ 确认 |
| H7 | `load_instance()` 忽略 instance_id 参数，始终读同一 cache 文件 | ✅ 确认 |
| H8 | R1（15）与 R2（3）格式错误上限不一致——不一定是 bug 但需要显式论证 | ✅ 确认 |
| H9 | 本地 `LIMITS["cost_limit"]` 死配置；Agent 自身 `cost_limit=5.0` 可能生效；`cost_tracking="ignore_errors"` 导致日志 `cost=0` | ⚠️ 降级为"不可审计"而非"完全未使用" |
| H10 | Harness timeout=600s 对重型实例风险，但缺历史持续时间分布 | ⚠️ 降级为"待验证风险"而非"已确认" |
| H11 | R1 resolved 时 verdict 误标 `both_succeed`，SF/CD 未运行 | ✅ 确认 |
| H12 | `--no-condiag` 无诊断时仍运行 CD 分支 | ✅ 确认 |
| H13 | Pilot 汇总读取旧字段（`stateful_feedback.status`） | ✅ 确认 |

---

## 三、中等问题（M1–M8）

| # | 问题 | 文件 | 状态 |
|---|------|------|------|
| M1 | Checkpoint 写盘只保存最后 100 条 | `checkpoint.py:143` | ✅ |
| M2 | `key_location` 取第一个非测试帧而非真实崩溃点 | `diagnoser_core.py` | ✅ |
| M3 | Confidence 写死 high/medium，无校准 | `diagnoser_core.py` | ✅ |
| M4 | 默认 fallback 为 API_DEFINITION，应 ABSTAIN | `diagnoser_core.py` | ✅ |
| M5 | Localization 只比较 basename，同名文件误判 | `diagnoser_core.py` | ✅ |
| M6 | Django extractor 向 Pydantic model 写未声明 `_extra`，存在 Pydantic 版本兼容性风险 | `django_extractor.py` | ⚠️ 运行时兼容风险，需真实 Django 日志单测验证 |
| M7 | 路径硬编码 `/home/swelite`、`/home/zz`、`/mnt/d` | 多处 | ✅ |
| M8 | 依赖无版本锁定（无 lockfile） | — | ✅ |

---

## 四、未实现模块

| 模块 | 预期路径 | 缺失部分 |
|------|---------|---------|
| LLM Multi-Signal Diagnoser | `condiag/diagnosis/reasoner/llm.py` | 完全未实现 |
| Diagnosis-Aware Compression | `condiag/compressor.py` | 压缩未接入诊断结果 |
| Deficiency-Guided Router | `condiag/acquisition/router.py` | 完全不存在 |
| 完整评测矩阵 | `condiag/evaluation/` | ContextBench、Oracle 等未就绪 |

---

## 五、修复计划

### P0：安全与实验完整性（先做，不做就不能跑 canary）

| # | 关联问题 | 操作 | 预期产出 |
|---|---------|------|---------|
| 1 | B1 | 撤销旧 Key → `git filter-repo` 清理历史 → 强制 `DEEPSEEK_API_KEY` 环境变量 → `.gitignore` + `.env` 模板 | 无硬编码 Key |
| 2 | B2 + H7 + 配置漂移 | 抽取共享 Agent 配置模块（读取官方 YAML，仅覆盖必要字段），统一两入口 | `condiag/agent/config.py` |
| 3 | B3 | 实现 `PatchIntegrityGate`：分级门禁 | `condiag/integrity.py` |
| 4 | B4 + B5 | 重写 workspace restore（方案 A: tempfile/docker cp 或方案 B: base64），三分 Patch SHA 校验 | `condiag/branch_runner.py` |
| 5 | B6 + B7 | 容器内执行 git diff，第一次 LLM 调用前抓取两个分支 workspace SHA，检查一致性 | `condiag/experiment.py` |
| 6 | B8 | 目录结构改为 `output_dir / instance_id / {episode_run_id}/`，产物只追加不覆盖 | `condiag/experiment.py` |

### P1：数据链接通

| # | 关联问题 | 操作 |
|---|---------|------|
| 1 | B9 + B10 | 不调 `.to_dict()`。构建完整数据链：test_log extractor + patch extractor + trajectory extractor + instance runtime-safe fields → `FailureFeatureBundle` → `DiagnoserCore` |
| 2 | B11 | `official_harness.py` 改调统一入口 `signals.extract_test_log()` |
| 3 | H1 + H4 | `_failure_summary` 加 return + `call_chains` 实现 + 失败聚类 |
| 4 | H5 + H6 | `_cap_total_size` 原子级删除（pop assistant 时一并 pop 其后所有 tool）+ branch_builder 修复多 tool 处理 |

### P2：配置统一

| # | 操作 |
|---|------|
| 1 | R1/R2 格式错误上限统一并论证 |
| 2 | 启用真实 cost tracking |
| 3 | `--no-condiag` 不运行 CD 分支 |
| 4 | Pilot 汇总字段修正（`status` → `termination_reason`） |

---

## 六、当前实验结果有效性

| 产物 | 判断 | 依据 |
|------|------|------|
| V2c canary Run 1–4 | **INVALID** | 空 patch、格式错误、压缩 bug、prompt bug 依次阻断。判定依据来自本地产物 + 静态代码 |
| V2c canary Run 5 — R1 Agent 行为 | 待 Patch 审计 | 需检查是否为非空源码 Patch |
| V2c canary Run 5 — R1 官方评测 | 若 evaluation patch 合法可保留 | 但需验证未被临时文件污染 |
| V2c canary Run 5 — FailureWitness | **可保留** | 作为 extractor 调试样本 |
| V2c canary Run 5 — SF/CD Revision | **无效** | workspace restore 按代码契约不正确 |
| V2c canary Run 5 — SF vs CD 比较 | **无效** | 不具备比较效力 |
| 标准 mini-SWE-agent 99 实例 baseline | **保留但需验证历史产物完整性** | 独立流程 |
| 压缩率（内容字符 86%） | **有效** | 内容字符层面正确 |
| 压缩率（估算 token 86%） | **不可信** | `estimate_tokens()` 只计 `message.content`，忽略 tool_calls、actions、协议开销 |
| 真实模型输入 token 压缩率 | **未测量** | 需用 tokenizer 实际计算 |
| pytest 帧提取修复 / Django 提取器 | **保留** | 单元可验证 |
| FailureFeatureBundle Schema / DiagnoserCore | **保留** | 独立于流程的设计 |

---

## 七、补充审计（第一轮未覆盖）

以下 9 项问题已在审核中被指出，尚未逐项代码核验，建议作为第二轮审计项。

| # | 问题 | 性质 |
|---|------|------|
| A1 | 并非真实 same-agent persistent episode——`run_branch()` 创建新 Agent 和新容器。准确名称应为 "checkpoint-forked revision episode" | 论文表述需修正 |
| A2 | Agent 环境未完整复用官方配置——手动 `DockerEnvironment` 未设置 `BASH_ENV=/root/.bashrc`，Python 环境可能与 Harness 不一致 | 待验证 |
| A3 | `TrajParser` 从 assistant `content` 提取命令，但真实轨迹命令位于 `tool_calls` / `extra.actions`，导致 viewed_files 等特征可能为空 | 需 10 条真实轨迹做 field-level coverage audit |
| A4 | Runtime 与 Oracle 字段无类型隔离——`InstanceSignals` 含 `fail_to_pass` 等评测字段，未来 LLM Diagnoser 可能误看到 Gold 信息 | 架构风险 |
| A5 | Harness `ERROR` 无 eligibility gate——镜像缺失、评测异常不应进入诊断和 Revision | 流程缺陷 |
| A6 | 当前 injection gate 只验证消息格式，不验证 Patch 恢复、workspace、Harness Patch 一致性 | 门禁覆盖面窄 |
| A7 | `condiag/__init__.py` 默认导出旧架构（CDType、Search Contract），而非新 `condiag.diagnosis` | 包接入口径错误 |
| A8 | 实验审计产物不足——未独立保存 FW bundle、DiagnosisResult、compressed messages、workspace restore 前后 diff 等 | 事后不可复查 |
| A9 | 当前 SF/CD 都经过压缩，实际比较的是 "Compressed Feedback vs Compressed Feedback + legacy guidance"，不是实验矩阵中的 Full Context / w/o Compression / w/o Routing | 基线名实不符 |

---

## 八、核心判断

| 层次 | 状态 |
|------|------|
| 研究问题 | **仍然成立，有潜力** |
| Failure log 基础提取 | **开始可信，但多失败聚类未完成** |
| Round 1 Agent 配置 | **多套配置漂移，尚未统一** |
| Intermediate evaluation | **官方 Harness 可调用，但阶段控制不完整** |
| Checkpoint | **只有部分消息状态，不是真实完整快照** |
| Workspace restore | **按当前代码契约确认错误** |
| Round 2 fairness | **未被真实验证** |
| Compression | **可运行，但指标和保留策略不可信** |
| Diagnoser | **独立原型存在，主流程未接通** |
| Acquisition | **尚未实现** |
| 当前实验结果 | **不可用于论文** |
