# django-16454: feedback (resolved) vs condiag (unresolved) 差分分析

## 1. retry_task.md 体量对比

| | feedback_retry | condiag_retry | 倍率 |
|---|---|---|---|
| retry_task.md chars | 6,851 | 17,820 | **2.6x** |
| patch.diff chars | 998 | 1,240 | 1.24x |

## 2. retry_task.md 结构对比

**feedback** (6,851 chars) — 紧凑、可执行：
```
Original Issue (34 lines)
Previous Attempt (7 lines)
Why Retry (6 lines)
Feedback Retry Packet:
  Previous Attempt Summary (3 lines)
  Runtime Feedback (test output excerpts)
  Previous Patch Summary (3 lines)
  Retry Instruction (8 lines, concrete)
```

**condiag** (17,820 chars) — 臃肿、学术化：
```
Original Issue (34 lines)
Previous Attempt (7 lines)
Why Retry + taxonomy terms (6 lines)
ConDiag Context Packet:
  Diagnosis (8 lines, taxonomy-heavy: "EXPLORE_OK_EDIT_MISALIGNED" etc.)
  Runtime Failure Evidence (2 lines, vague)
  E1 REHYDRATE — **255 lines of base.py source code** (lines 46-300)
  E5 Neighbor test — 4 lines (irrelevant: test_command_add_arguments_after_common_arguments)
  E6 Neighbor test — 7 lines (irrelevant: test_command_missing for dbshell)
  Repair Constraints (3 lines, vague)
  Retry Instruction (1 line: "Apply the REHYDRATE action plan")
  Retry Contract (boilerplate)
```

## 3. Patch 对比

两者都修改 `django/core/management/base.py`，都在 `CommandParser` 类里加了 `add_subparsers()` override。

**feedback patch (998 chars, RESOLVED)** — 优雅、最小化：
```python
from functools import partial

def add_subparsers(self, **kwargs):
    if "parser_class" not in kwargs:
        kwargs["parser_class"] = partial(
            type(self),
            missing_args_message=self.missing_args_message,
            called_from_command_line=self.called_from_command_line,
        )
    return super().add_subparsers(**kwargs)
```
- 使用 argparse 标准的 `parser_class` 机制
- `partial(type(self), ...)` 创建子类工厂
- **正确做法** — argparse 原生就接受 `parser_class` kwarg

**condiag patch (1240 chars, UNRESOLVED)** — 猴子补丁式：
```python
def add_subparsers(self, **kwargs):
    subparsers = super().add_subparsers(**kwargs)
    parent_missing_args_message = self.missing_args_message
    parent_called_from_command_line = self.called_from_command_line
    original_add_parser = subparsers.add_parser

    def wrapped_add_parser(name, **kwargs):
        kwargs.setdefault("missing_args_message", parent_missing_args_message)
        kwargs.setdefault("called_from_command_line", parent_called_from_command_line)
        return original_add_parser(name, **kwargs)

    subparsers.add_parser = wrapped_add_parser
    return subparsers
```
- 猴子补丁 `subparsers.add_parser`
- 绕过 argparse 标准扩展点
- 可能遗漏 argparse 内部通过 `parser_class` 创建的路径

## 4. 根因分析：ConDiag 为什么输

**4.1 Evidence 体量过大 (primary cause)**
E1 把 `base.py` 255 行源码（`CommandParser` + `handle_default_options` + `no_translations` + `DjangoHelpFormatter` + `OutputWrapper`）全部塞进 packet。agent 被淹没在无关代码中。

**4.2 Taxonomy 术语过载**
"EXPLORE_OK_EDIT_MISALIGNED", "REHYDRATE_SEEN_EVIDENCE", "5R action", "action family: RECOVERY" — 对 agent 无帮助，增加认知负担。

**4.3 邻居测试假阳性**
E5 (`test_command_add_arguments_after_common_arguments` in user_commands) 和 E6 (`test_command_missing` in dbshell) 跟 subparser issue 完全无关。keyword match 的假阳性，但 score=0.75 没被过滤。

**4.4 Retry Instruction 太模糊**
"Apply the REHYDRATE action plan" — 没说改哪里、怎么改。对比 feedback: "Address the specific failing tests/errors visible in the runtime feedback" — 至少指向了测试。

**4.5 核心机制差异**
- feedback agent: issue + 测试输出 → 自由推理 → 想到 `parser_class=partial(type(self))` → 正确
- condiag agent: 255 行源码 dump → 被引导到 `wrapped_add_parser` 思路 → 猴子补丁路线 → 不干净 → eval 不过

## 5. 结论

**ConDiag 在这个 case 上是负面帮助**：额外的 evidence（尤其是 E1 的 255 行源码 dump + E5/E6 假阳性邻居测试）把 agent 导向了比 feedback 更差的实现方向。

## 6. ConDiag packet 改进方向

1. **不要 dump 大段源码** — E1 255 行 → 应压缩为 "CommandParser.__init__ (lines 53-58): missing_args_message and called_from_command_line need propagation to subparsers"
2. **去掉 taxonomy 术语** — agent 不需要知道 pathology class name
3. **过滤假阳性邻居测试** — E5/E6 score=0.75 应被阈值排除（建议 >= 0.85）
4. **给 Primary Edit Target** — 直接说 "Override add_subparsers() in CommandParser"
5. **给 Failure Witness** — attempt_1 改了 +17 lines 但测试仍然失败的证据
6. **Retry Instruction 要具体** — 不是 "Apply REHYDRATE action plan"，而是 "Propagate missing_args_message and called_from_command_line to subparsers"
7. **总体原则：packet 应该 < 5000 chars**，比 feedback packet 略多但不要 2.6x
