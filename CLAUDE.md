# ConDiag 项目

## 首先阅读

@docs/CONDIAG_HANDOFF.md

## 硬性约束（违反 = 回滚）

- **不要修改 mini-SWE-agent 源码。** 所有 ConDiag 代码在 `condiag/` 下。
- **必须使用官方 `swebench.harness.run_instance()`**（`namespace="swebench"`），不允许自定义 `docker exec` 评测逻辑。
- **SF 和 ConDiag 必须从同一个 Round 1 snapshot 启动**（共用 `build_branch_messages`）。
- **两个分支收到相同的 FailureWitness。** 只有 ConDiag 收到 Diagnosis Instruction。
- **`step_limit=0`** — 无人工截断。只有 `submitted` 是有效的终止。
- **FormatError 终止不能算作有效提交。** 只有 `termination_reason == "submitted"` 才算。
- **`comparison.json` 必须始终在 `try/finally` 中写出**，即使部分失败。
- **每次修改代码后执行 `git diff` 确认变更范围。**

## 项目定位

ConDiag v4: **Failure-Guided Context Diagnosis via Persistent Repair Episodes**。
研究代码位于 `/home/swelite/condiag/`。产物位于 `/mnt/d/condiag-artifacts/condiag/`。

**核心假设：** ConDiag 不是通过检索更多上下文来改进修复，而是在同一轮 repair episode 中让 agent 保持存活，利用中间验证的失败信号（通过软诊断 prompt）有针对性地重新探索。

**v4 核心变化（2026-07-15）：**
- 两次独立 Attempt → 1 个 persistent episode
- Markdown ContextPacket 注入 → 保留原生 message history + working tree
- 外部 CDType 规则 → 宿主同一模型在 DIAGNOSE phase 显式推理
- 外部 retry runner → `ConDiagIntegratedAgent(DefaultAgent)` 嵌入原生 loop

## 实验设定（不可协商）

Post-Validation / Interactive Feedback Repair。不是标准 hidden-test SWE-bench pass@1。

**已验证（V2c）：**
```
R1 自然提交（step_limit=0, 等待 Submitted）
→ 拦截 → 补 tool response → 提取 canonical patch（含 untracked）
→ 官方 run_instance() 评测
→ 深复制 checkpoint（messages, workspace, n_calls, cost, elapsed）
→ 如果是 UNRESOLVED:
    同一 checkpoint fork SF（FW only）/ CD（FW + Diagnosis）
    恢复工作区 + 计数器 → 继续 step loop → 提交 → 最终评测
→ comparison.json（try/finally 保证写出）
```

## 架构图

```
experiment.py（薄编排）
  ├── round1_runner.py    R1 自然提交（step_limit=0）
  ├── branch_runner.py    R2 分支（SF/CD 共用, mode 参数区分）
  ├── branch_builder.py   ⚠️ 唯一注入入口（gate + runner 共用）
  ├── checkpoint.py       快照保存/恢复
  └── official_harness.py  官方 run_instance() 薄层
```

所有注入逻辑只存在于 `build_branch_messages()`。门禁测试和正式实验调用同一份代码。

## 数据集

| 来源 | 实例数 | Docker 镜像 |
|------|--------|-------------|
| Verified/python（99 中） | 52 | ✅ 已拉取 |
| Multi/Poly/Pro（99 中） | 47 | ❌ 需要适配 |
| SWE-bench Lite | 12 | 已在 52 中 |
| SWE-bench Verified | 54/500 有镜像 | 其余需拉取 |

**99 实例 manifest：** `/mnt/d/condiag-artifacts/condiag/manifests/instances_v2.jsonl`

## 已知问题

1. **DeepSeek 长上下文 JSON 不稳定** — 150K+ 字符下约 7% 坏 JSON。FormatError 计数器已验证正确（每一步 clean step 后归零）。末尾 8+ 连 FE 是模型进入错误状态，非计数 bug。
2. **Django gold calibration** — 已通过 `django-12125`（empty→UNRESOLVED, gold→RESOLVED）。官方 `run_instance()` 配 `namespace="swebench"` 正常工作。
3. **ContextBench 离线评测未接通** — 需要 git clone（网络不稳定）或本地 repo cache。

## 关键文件

| 文件 | 用途 | 状态 |
|------|------|------|
| `condiag/round1_runner.py` | R1 自然提交循环 | ✅ |
| `condiag/branch_runner.py` | R2 分支循环 | ✅ |
| `condiag/experiment.py` | 实验编排 | ✅ |
| `condiag/branch_builder.py` | ⚠️ 唯一注入函数 | ✅ |
| `condiag/checkpoint.py` | 快照保存/恢复 | ✅ |
| `condiag/instance_registry.py` | 数据加载 | ✅ |
| `condiag/evaluators/official_harness.py` | 官方评测薄层 | ✅ |
| `experiments/v2c_entry.py` | CLI 入口 | ✅ |
| `docs/CONDIAG_HANDOFF.md` | 完整项目知识 | ✅ |

## 冻结模块（v1-v3，不修改）

| 模块 | 处置 |
|------|------|
| `condiag/trajectory_signals.py` | 冻结 |
| `condiag/context_deficiency_diagnoser.py` | 冻结 |
| `condiag/search_contract_builder.py` | 冻结 |
| `condiag/contract_renderer.py` | 冻结 |
| `condiag/paired_runner_legacy.py` | 冻结 |
| `condiag/paired_runner_prototype_v1.py` | 冻结 |
| `condiag/integrated_agent.py` | 冻结（参考用） |
| `condiag/evaluators/docker_swebench.py` | 冻结 |
