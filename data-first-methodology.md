---
name: data-first-methodology
description: 每一步必须先做数据摸底，再写代码——信号不可凭空设计
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d760be3b-d65b-4664-87fc-f08d686adef7
  modified: 2026-07-19T10:38:29.783Z
---

Phase 1→Phase 2 的过渡中暴露了一个根本问题：诊断模块的信号输入设计，跳过了"检查评测输出实际有什么数据"这一步，直接从架构图推导 schema。

**教训：** 代码架构不能替代数据理解。每个模块的输入必须有对应的真实数据源验证，不能从需求倒推字段。

**How to apply:**
1. 任何新模块的第一步：找出它的**所有输入数据源**，打开看真实内容
2. 列出现有可提取字段 vs 需要额外采集的字段
3. 只有在数据摸底完成后，才设计 schema 和接口
4. 输出的 schema 必须对应到具体的数据源路径（如 `test_log_path → extract_witness → failed_tests`）
5. 如果某个字段没有稳定数据源，标记为 `Optional` 或 deferred
