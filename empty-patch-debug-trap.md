---
name: empty-patch-debug-trap
description: agent 提交空 patch 不是因为流程 bug，而是 prompt 没要求改代码
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d760be3b-d65b-4664-87fc-f08d686adef7
  modified: 2026-07-19T15:43:45.966Z
---

**症状：** R1 termination_reason=submitted，但 patch_text 为空或只改了 pyproject.toml。官方评测 UNRESOLVED。循环排查压缩/bug/超时，浪费了多轮 canary 时间。

**根因：** 系统提示词太简陋。`run_canary.py` 里只写了一句 "You are a software engineer. You can run bash commands..."，没告诉 agent 需要改代码、创建 git diff、按规范提交。agent 以为读文件+写分析就算完成任务。

**教训：** 
1. agent 的行为由提示词驱动。提示词缺了"改代码"这个指令，agent 就不改代码。
2. 不要假设 agent 知道该做什么——显式写出工作流：分析→复现→编辑→验证→提交。
3. 提交格式必须显式约束：`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat patch.txt`

**How to apply:**
1. 每次改 agent 配置时，先检查 system_template + instance_template 是否包含完整的 5 步工作流
2. 跑 canary 后第一件事：看 submission patch 有没有实质内容（不是空或配置修改）
3. 提交前必须有 git diff 操作——没有 diff 的 COMPLETE_TASK 是无效的
4. `max_tokens` 至少 4096——1024 连完整 patch 都放不下
