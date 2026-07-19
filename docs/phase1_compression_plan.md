# Phase 1 详细执行计划

## 目标

在当前唯一的 canary 实例（astropy-13398）上跑通 **R1 → Compression → R2 → SWE-bench eval** 全流程。

## 衡量标准

| 指标 | 压缩前（现状） | 压缩后（目标） |
|------|--------------|--------------|
| R2 是否跑完 | ❌ repeated_format_error | ✅ submitted |
| Structured Output Success Rate | ~69%（40/58） | >90% |
| 上下文 token 量 | 爆炸（100+ msg） | 可控（<50 msg） |
| SWE-bench R2 评测 | ❌ 未执行 | ✅ 有结果 |

---

## 第一步：理解当前消息结构

先搞清楚 v4 的消息格式，才能设计压缩策略。

```
当前 trajectory 结构:
  system (1):    系统提示词
  user (1):      issue 描述
  assistant (N): 模型回复（含 tool_calls/bash）
  tool (N):      工具执行结果（bash stdout）
  user (1+):     failure witness + diagnosis（R2 注入）
```

需要回答的问题：
- 每条 assistant 消息的格式（tool_calls 字段结构）
- 每条 tool 消息的格式（output 字段大小）
- 哪些消息是"冗余"的，占了多少 token

**动作：** 分析 canary 上一次跑的 trajectory.json。

## 第二步：实现 Compression 策略

### 2a: Observation Masking（低风险）

只改 tool 消息（bash 输出），不改 assistant 消息。

```python
# 压缩前
{"role": "tool", "content": "Cloning into 'astropy'...\nremote: Enumerating objects...\n... 2000 lines ..."}

# 压缩后
{"role": "tool", "content": "<output truncated: 25312 chars, exit code 0>\n[command: git clone ...]"}
```

**实现位置：** `condiag/compression/strategy.py`

### 2b: Window Truncation（中等风险）

保留最近 N 轮交互，丢弃早期探索轮次。

```
保留：system + issue + [最近 K 轮 assistant+tool] + FW + diagnosis
丢弃：早期探索、试错的命令
```

### 2c: 选择性保留（诊断感知，Phase 2 再做）

根据诊断结果保留相关的消息。Phase 1 不做。

## 第三步：修改数据流

当前流程：

```
R1 → checkpoint → build_branch_messages(R1 msgs + FW + diag) → R2
```

修改为：

```
R1 → checkpoint → compress(R1 msgs) → build_branch_messages(compressed msgs + FW + diag) → R2
```

**修改位置：**
- `experiment.py`: 在 `run_branch` 调用前压缩 `r1.messages`
- 或者 `branch_builder.py`: 在注入前压缩 `checkpoint_messages`

建议改 `experiment.py`，不污染现有的 `branch_builder`。

## 第四步：验证

跑同一个 canary 实例（astropy-13398），对比压缩前后：

| 项目 | 压缩前 | 压缩后 |
|------|--------|--------|
| R1 结果 | repeated_format_error | 同上（压缩不干预 R1） |
| R2 消息数 | ~58 条 | ? 条 |
| R2 格式错误 | 15+ 连续 | ? |
| R2 终止原因 | — | submitted / ? |
| R2 SWE-bench | — | RESOLVED / UNRESOLVED |

---

## 代码结构

```
condiag/
├── compression/                 # 🆕
│   ├── __init__.py
│   ├── strategy.py              # 压缩策略实现
│   │   ├── mask_observations()  # 裁剪 bash 输出
│   │   ├── truncate_window()    # 截断早期轮次
│   │   └── compress_messages()  # 组合策略
│   └── budget.py                # token 预算估算
│
├── experiment.py                # 🔧 修改
│   └── run_experiment():
│       # 在 R1 之后、R2 之前插入:
│       compressed = compress(r1.messages)
│       sf = run_branch(checkpoint_messages=compressed, ...)
│       cd = run_branch(checkpoint_messages=compressed, ...)
```

---

## 执行顺序

| Step | 内容 | 预计时间 |
|------|------|---------|
| 1 | 分析 trajectory 消息结构 | 30 min |
| 2 | 实现 observation masking | 1 hr |
| 3 | 集成到 experiment.py | 30 min |
| 4 | 跑 canary 验证 | 30 min（LLM 调用时间） |
| 5 | 根据结果调优 | 1 hr |
