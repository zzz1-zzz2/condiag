# ConDiag Agent Adapter Layer

**Status:** v0.2 — `MinisweAdapter` 已实现；其余 3 个 adapter 是 planned skeleton
**Date:** 2026-06-28
**Companion docs:** `baseline_runner_design_v0.2.md` §4.5 + `artifact_schema.md`

---

## 1. Design Principle

ConDiag is designed as an **agent-agnostic** post-failure recovery middleware.
To avoid coupling the method to a specific repair agent, we introduce an
**Agent Adapter Layer**. The input-side adapter normalizes heterogeneous
agent outputs—such as mini-SWE trajectories, Agentless localization stages,
or OpenHands event streams—into a unified `runtime_signals.json` and
`case_bundle`. The output-side adapter translates ConDiag's
`context_packet.md` into an agent-specific retry input.

In the current v0 implementation, we fully support **mini-SWE-Agent** via
`build_case_bundle.py`, which extracts viewed spans, searched queries, patch
diffs, local test outputs, and final `<PATCH_CONTEXT>`. Other agents are
treated as future adapters. This design allows **ConDiag Core**—trigger,
taxonomy, 5R planner, retrieval executor, scope guard, and ContextPacket
builder—to remain independent of agent-specific logging formats.

中文：ConDiag 被设计成 agent-agnostic 的失败后恢复中间件。为了避免方法绑定某个具体 repair agent，我们引入 Agent Adapter Layer。输入侧 Adapter 将不同 agent 的运行输出，如 mini-SWE trajectory、Agentless localization stages、OpenHands event stream，统一转成 `runtime_signals.json` 和 case_bundle；输出侧 Adapter 则把 ConDiag 生成的 `context_packet.md` 转成对应 agent 可使用的 retry input。

当前 v0 完整实现 mini-SWE Adapter，即 `build_case_bundle.py`；它负责抽取 viewed spans、searched queries、patch diff、local test outputs 和 final `<PATCH_CONTEXT>`。其他 agent 暂作为后续 adapter 扩展。这样 ConDiag Core 的 trigger、taxonomy、5R planner、retrieval executor、scope guard 和 ContextPacket builder 都可以保持 agent-independent。

---

## 2. Architecture

```
                  Different Agents
        mini-SWE / Agentless / OpenHands / SWE-agent
                         ↓
                 Agent Output Adapter
                         ↓
                  runtime_signals.json
                         ↓
                    ConDiag Core
       trigger / taxonomy / 5R / retrieval / guard / packet
                         ↓
                  context_packet.md
                         ↓
                Retry Injection Adapter
                         ↓
                    原 Agent 再修
```

**边界**：Adapter 负责格式转换；ConDiag Core 负责诊断和恢复。ConDiag Core 不 import 任何 agent-specific 模块。

---

## 3. Adapter Contract

每个 adapter 必须实现 `condiag.adapters.base.AgentAdapter` 抽象类：

```python
class AgentAdapter(ABC):
    name: str               # "miniswe" | "agentless" | ...
    display_name: str
    status: str             # "implemented" | "planned"

    # input side: raw agent run -> unified case_bundle
    def build_case_bundle(self, raw_run_dir, instance_id, out_dir) -> dict: ...
    def extract_runtime_signals(self, raw_run_dir) -> dict: ...
    def extract_patch(self, raw_run_dir) -> str: ...
    def extract_final_patch_context(self, raw_run_dir) -> dict: ...

    # output side: ConDiag context_packet -> agent retry input
    def build_retry_input(self, context_packet_path, task_metadata) -> dict: ...
```

**输出契约**（`build_case_bundle` 写到 `out_dir/`）：
```
raw_trajectory.json
runtime_signals.json
patch.diff
local_test_outputs.md
final_patch_context.json
build_report.json
```

---

## 4. Adapter-Specific Notes

### 4.1 mini-SWE Adapter (implemented)

来源：`traj.json` 中的 assistant/user messages、bash commands、`<EXPLORE_CONTEXT>`、`<PATCH_CONTEXT>`、git diff、test 输出。

输出：完整 case_bundle（见 §3）。

Retry input shape：
```json
{
  "agent": "miniswe",
  "retry_input_kind": "user_message",
  "user_message": "## ConDiag Recovery Context ...\n<context_packet.md content>",
  "context_packet_path": "..."
}
```

### 4.2 Agentless Adapter (planned)

Agentless 是 pipeline（file localization → element localization → edit localization → repair），不是开放式 trajectory。Adapter 把各阶段映射到 runtime_signals：

| runtime_signals 字段    | Agentless 来源                                          |
| --------------------- | ----------------------------------------------------- |
| `searched_queries`    | localization prompt / issue keyword / retrieval query |
| `viewed_files`        | file localization candidates                          |
| `viewed_spans`        | element localization / related locations              |
| `edited_files`        | generated patch diff                                  |
| `patch.diff`          | repair output                                         |
| `local_test_outputs`  | validation logs                                       |
| `final_patch_context` | repair prompt context / selected locations            |

Agentless 特别适合 RELOCALIZE —— file localization 是命名的失败点（如 ContextBench 案例 `django__django-11630`：agent 被 "db table collision" 表面词带偏，没追踪错误码 `E028`）。

Retry input shape（planned）：
```json
{
  "agent": "agentless",
  "retry_input_kind": "localized_candidates",
  "files": [...],
  "spans": [...]
}
```

### 4.3 OpenHands / SWE-agent Adapter (planned)

主要差别是 tool-call event stream / workspace state / submit protocol / prompt format。ContextBench 已展示这些 agent 可通过 agent-specific prompt/workflow adaptation 统一产出 `<PATCH_CONTEXT>`。

Retry input shape（planned）：
- OpenHands: `{"agent": "openhands", "retry_input_kind": "event_stream_injection", ...}`
- SWE-agent: `{"agent": "swe_agent", "retry_input_kind": "instruction_injection", ...}`

---

## 5. Code Layout

```
condiag/adapters/
├── __init__.py        # exports + registry initialization
├── base.py            # AgentAdapter ABC + register_adapter/get_adapter/list_adapters
├── miniswe.py         # implemented; thin wrapper around tools/build_case_bundle.py
├── agentless.py       # skeleton, NotImplementedError
├── openhands.py       # skeleton, NotImplementedError
└── swe_agent.py       # skeleton, NotImplementedError
```

注册机制：每个 adapter 用 `@register_adapter` 装饰；`__init__.py` import 所有 adapter 触发注册；调用方用 `get_adapter(name)` 实例化。

```python
from condiag.adapters import get_adapter, list_adapters

print(list_adapters())
# {'miniswe': {'display_name': 'mini-SWE-Agent', 'status': 'implemented'},
#  'agentless': {'display_name': 'Agentless', 'status': 'planned'},
#  'openhands': {'display_name': 'OpenHands', 'status': 'planned'},
#  'swe_agent': {'display_name': 'SWE-agent', 'status': 'planned'}}

adapter = get_adapter("miniswe")
report = adapter.build_case_bundle(traj_path, instance_id, out_dir)
```

---

## 6. Why It Matters for Robustness

Adapter 是 ConDiag 鲁棒性的工程支柱之一：

1. **三轴 taxonomy 跟 agent 解耦**：Context Evidence Type / Runtime Gap Status / Recovery Intent 不依赖某个 agent 的日志格式
2. **5R 恢复流可复用**：trigger / retrieval / guard / packet 只要消费统一 case_bundle 就能跨 agent 工作
3. **跨 agent 实验**：v1 接入 Agentless / OpenHands 后，可以验证 ConDiag 是否真的 agent-agnostic
4. **公平性**：Feedback Retry / Broad Expansion / ConDiag 四个 baseline 都通过 adapter 注入，注入方式（user_message / localized_candidates / event_injection）的可比性可控

---

## 7. What v0 Does NOT Do

明确不在 v0 范围（学长"方法可以简单"原则）：

- ❌ Agentless / OpenHands / SWE-agent 的实际接入（只有 skeleton）
- ❌ 跨 agent 大规模对比实验
- ❌ 不同 agent 输出 `<PATCH_CONTEXT>` 质量的对比

这些留给 v1+。v0 重点：mini-SWE 走通 + Adapter 接口留好 + 目录树加 `<agent>/` 维度。

---

## 8. Reference

- ConDiag Core 入口：`condiag/cli.py`、`condiag/seed_regression.py`、`condiag/manual_recovery.py`
- mini-SWE Adapter 实际包的 tool：`condiag/tools/build_case_bundle.py`
- Adapter framework：`condiag/adapters/{base,miniswe,agentless,openhands,swe_agent}.py`
- 实验目录结构：`runs/pilot50/<agent>/<baseline>/<instance>/`（见 `artifact_schema.md`）
