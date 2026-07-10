# ConDiag 端到端分析报告：django__django-12125

> 生成日期：2026-07-06
> 管道状态：✅ 三个 Bug 已修复，pipeline 可运行

---

## 一、原始问题描述（Problem Statement）

> 来源：SWE-bench Verified

**makemigrations 为内部类生成不正确的路径**

当你定义一个继承自 `django.db.models.Field` 的子类作为某个其他类的内部类，并在 `django.db.models.Model` 类中使用这个字段时，运行 `manage.py makemigrations` 生成的迁移文件会将内部类引用为模块的顶层类。

复现步骤：
```python
class Outer(object):
    class Inner(models.CharField):
        pass

class A(models.Model):
    field = Outer.Inner(max_length=20)
```

生成的迁移文件包含：
```python
migrations.CreateModel(
    name='A',
    fields=[
        ('id', ...),
        ('field', test1.models.Inner(max_length=20)),  # 错误！应该是 test1.models.Outer.Inner
    ],
),
```

真实场景涉及 django-enumfields 的 EnumField，定义为 Model 的内部类：
```python
class Thing(models.Model):
    @enum.unique
    class State(Enum):
        on = 'on'
        off = 'off'
    state = EnumField(enum=State)
```

生成：
```python
('state', enumfields.fields.EnumField(enum=test1.models.State, max_length=10)),
# 错误！应该是 test1.models.Thing.State
```

**根本原因**：`TypeSerializer.serialize()` 使用 `self.value.__name__`（仅类名），而内部类需要用 `self.value.__qualname__`（包含外部类的完整限定名）。

---

## 二、Agent 第一次修复（attempt_1）

### Agent 生成的 patch

```diff
--- a/django/db/migrations/serializer.py
+++ b/django/db/migrations/serializer.py
@@ -1,3 +1,4 @@
+import importlib
 import builtins
 import collections.abc
 import datetime
@@ -90,15 +91,24 @@ class DeconstructableSerializer(BaseSerializer):
 
     @staticmethod
     def _serialize_path(path):
-        module, name = path.rsplit(".", 1)
+        module = path
+        # Find the importable module based on the path.
+        parts = path.split(".")
+        for i in range(len(parts), 0, -1):
+            candidate = ".".join(parts[:i])
+            try:
+                if importlib.util.find_spec(candidate) is not None:
+                    module = candidate
+                    break
+            except (ImportError, ValueError):
+                continue
         if module == "django.db.models":
             imports = {"from django.db import models"}
-            name = "models.%s" % name
+            name = "models.%s" % path.rsplit(".", 1)[1]
         else:
             imports = {"import %s" % module}
             name = path
         return name, imports
-
     def serialize(self):
         return self.serialize_deconstructed(*self.value.deconstruct())
 
@@ -143,7 +153,7 @@ class FunctionTypeSerializer(BaseSerializer):
         if getattr(self.value, "__self__", None) and isinstance(self.value.__self__, type):
             klass = self.value.__self__
             module = klass.__module__
-            return "%s.%s.%s" % (module, klass.__name__, self.value.__name__), {"import %s" % module}
+            return "%s.%s.%s" % (module, klass.__qualname__, self.value.__name__), {"import %s" % module}
 
@@ -267,9 +277,12 @@ class TypeSerializer(BaseSerializer):
         if hasattr(self.value, "__module__"):
             module = self.value.__module__
             if module == builtins.__name__:
-                return self.value.__name__, set()
+                return self.value.__qualname__, set()
             else:
-                return "%s.%s" % (module, self.value.__name__), {"import %s" % module}
+                qualname = self.value.__qualname__
+                if '<locals>' in qualname:
+                    qualname = self.value.__name__
+                return "%s.%s" % (module, qualname), {"import %s" % module}
```

### 与 Gold Patch 对比

**Gold patch**（正确修复，仅改 1 行）：
```diff
- return "%s.%s" % (module, self.value.__name__), {"import %s" % module}
+ return "%s.%s" % (module, self.value.__qualname__), {"import %s" % module}
```

**attempt_1 问题**：
1. ✅ TypeSerializer 的 `__qualname__` 改对了（但加了多余的 `<locals>` guard）
2. ❌ 不必要地重写了 `DeconstructableSerializer._serialize_path()`（改用 importlib）
3. ❌ 添加了 `import importlib`（未被使用）
4. ❌ 修改了 `FunctionTypeSerializer`（与 bug 无关）

→ **过度修复（over-edit）导致 F2P 测试失败**

---

## 三、验证评估结果（attempt_1）

| 维度 | 结果 |
|---|---|
| exit_status | Submitted |
| resolved | ❌ 未通过 |
| 测试结果 | F2P 失败（过度修复导致现有测试失败） |
| 失败类型 | RuntimeError |
| 失败原因 | 过度修改 `_serialize_path()` 导致 migrations writer 测试失败 |

---

## 四、Failure Witness（失败见证）

```json
{
  "instance_id": "django__django-12125",
  "has_failure_witness": true,
  "failure_type": "RuntimeError",
  "error_message": "RuntimeError: Model class migrations.models.ModelWithCustomBase doesn't declare an explicit app_label and isn't in an application in INSTALLED_APPS.",
  "mode": "post_validation_failure",
  "source": "post_validation_output",
  "oracle_labels_hidden": true
}
```

失败堆栈最终帧：`django/db/models/base.py:113` — 测试加载时 app_label 未配置。

---

## 五、RetryTrigger（重试触发诊断）

```json
{
  "trigger_type": "EVIDENCE_EDIT_MISMATCH",
  "should_retry": true,
  "trigger_reason": [
    "9 viewed file(s) dropped from final PATCH_CONTEXT (>= 3 medium-strength mismatch)"
  ],
  "runtime_gap_status": "SEEN_BUT_DROPPED",
  "trigger_confidence": "medium"
}
```

**触发逻辑**：Agent attempt_1 查看了 9 个文件，但最终 PATCH_CONTEXT 只保留了 4 处 span。大量已查看但未使用的代码区域 → `EVIDENCE_EDIT_MISMATCH`。

---

## 六、ConDiag 诊断结果

```json
{
  "pathology": "EXPLORE_OK_EDIT_MISALIGNED",
  "action_family": "RECOVERY",
  "primary_5r_action": "REHYDRATE",
  "retry_intent": "REHYDRATE_SEEN_EVIDENCE",
  "confidence": 0.55
}
```

**诊断含义**：Agent 探索（explore）正确，看到了正确的代码，但编辑（edit）时偏离了证据 → 需要把之前看到但没用的证据重新注入给 Host Agent。

---

## 七、ConDiag 检索执行结果

| 检索操作 | 状态 | 候选数 |
|---|---|---|
| FIND_FAILED_TEST | ✅ done | 0 |
| REHYDRATE_SEEN_EVIDENCE | ✅ done | 4 |
| FIND_SYMBOL_DEFINITION | ✅ done | 3 |

**找到的证据**（selected_evidence，selected_count=5）：

| # | 类型 | 文件 | 行 | 评分 | 说明 |
|---|---|---|---|---|---|
| E1 | REHYDRATE | serializer.py | 200-300 | 0.95 | ModelManagerSerializer 区域 |
| E2 | REHYDRATE | serializer.py | 1-100 | 0.95 | BaseSerializer/imports 区域 |
| E4 | REHYDRATE | fields/__init__.py | 450-520 | 0.95 | Field.__init__ 参数区域 |
| E7 | SYMBOL_DEF | gdal/field.py | 19-35 | 0.92 | **Field.\_\_init\_\_ 解析到错误位置** ⚠️ |
| E9 | SYMBOL_DEF | writer.py | 270-271 | 0.92 | MigrationWriter.serialize 定义 |

---

## 八、Context Packet（完整内容，7374 chars）

见下方文件 `context_packet_full.md`

---

## 九、Packet 质量问题分析

### 问题 1：Primary Edit Target 定位错误

Packet 指定 `ModelManagerSerializer`（lines 200-300）为主要编辑目标。但真正需要修改的是 `TypeSerializer`（lines 265-275）。

原因：REHYDRATE 证据选了 serializer.py:200-300，而 SYMBOL_DEF 解析 `Field.__init__` 时指向了 `django/contrib/gis/gdal/field.py`（一个不相关的 GDAL 字段实现），没有解析到 `django/db/models/fields/__init__.py`。

### 问题 2：关键区域未突出

真正的 bug 行（TypeSerializer line 272：`self.value.__name__` → 应改为 `__qualname__`）在 serializer.py:267-272 区间。这个区间存在于 final_patch_context.json 中（patch 确实改过），但在 rehydrate 证据中没有作为独立项出现。

### 问题 3：Retry Instruction 模板化

最后一段指令包含 `add_subparsers` 等不相关的内容，是模板占位符未被替换。

### 问题 4：证据评分区分度低

5 条证据中 3 条 0.95、2 条 0.92，没有足够的区分度让 Host Agent 判断优先级。

---

## 十、总结：从问题到失败再到诊断

```
原始 Issue: 内部类序列化路径错误 (__name__ → __qualname__)
    │
    ▼
Agent attempt_1: 过度修复
    ├── 改了对的地方（__qualname__ in TypeSerializer）
    ├── 改了不对的地方（_serialize_path 重写 + importlib）
    └── 改多了（FunctionTypeSerializer 无关系改）
    │
    ▼
验证评估: F2P 失败（RuntimeError）
    │
    ▼
Failure Witness: 捕获 post-validation RuntimeError
    │
    ▼
RetryTrigger: EVIDENCE_EDIT_MISMATCH
    ├── 9 viewed files dropped from PATCH_CONTEXT
    └── 看到了正确的代码但没用上
    │
    ▼
ConDiag Diagnosis: EXPLORE_OK_EDIT_MISALIGNED
    ├── 5R: REHYDRATE
    └── 置信度: 0.55
    │
    ▼
ConDiag Packet: 7374 chars, 5 evidence items
    ├── ✅ pipeline 正常跑通
    ├── ✅ 3 个检索操作全部 done
    ├── ⚠️ Primary Edit Target 指向 ModelManagerSerializer（错误）
    ├── ⚠️ Field.__init__ 解析到 gdal/field.py（错误文件）
    ├── ⚠️ 证据评分区分度不足
    └── ⚠️ Retry Instruction 含模板残留
    │
    ▼
结论: Pipeline 基础链路已通，但 evidence 质量和定位精度需改进
```
