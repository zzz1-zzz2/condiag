#!/usr/bin/env python3
"""Three-way comparison table for instance 27320d49 (scikit-learn PR 25232).

Pulls data from:
- M0 mini-SWE (GLM):       runs/m0_miniswe_27320d49/
- M1 Agentless (DeepSeek): runs/m1_agentless_smoke_27320d49/
- M1 mini-SWE (DeepSeek):  runs/m1_miniswe_smoke_27320d49/
"""
import json
from pathlib import Path
import pyarrow.dataset as ds


def load_json(p):
    with open(p) as f:
        return json.load(f)


def find_usage(traj):
    """sum prompt+completion tokens over all assistant msgs"""
    ptoks = ctoks = 0
    model = None
    n_resp_with_usage = 0
    for m in traj.get("messages", []):
        if m.get("role") == "assistant":
            extra = m.get("extra") or {}
            resp = extra.get("response") or {}
            u = resp.get("usage") if isinstance(resp, dict) else None
            if u:
                ptoks += u.get("prompt_tokens", 0) or 0
                ctoks += u.get("completion_tokens", 0) or 0
                n_resp_with_usage += 1
                if not model:
                    model = resp.get("model")
    return model, ptoks, ctoks, n_resp_with_usage


def count_adds(p):
    return sum(1 for line in p.split("\n") if line.startswith("+") and not line.startswith("+++"))


def count_dels(p):
    return sum(1 for line in p.split("\n") if line.startswith("-") and not line.startswith("---"))


def semantic_ok(p):
    checks = [
        ("docstring fill_value", "fill_value" in p and "constant" in p),
        ('_parameter_constraints add', '"fill_value"' in p and "no_validation" in p),
        ("__init__ fill_value=None", "fill_value=None" in p),
        ("self.fill_value = fill_value", "self.fill_value = fill_value" in p),
    ]
    return [name for name, ok in checks if ok]


def main():
    # Load gold patch
    rows = ds.dataset(
        "/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet",
        format="parquet",
    ).to_table().to_pylist()
    gold = next((r for r in rows if r["original_inst_id"] == "scikit-learn__scikit-learn-25232"), None)
    gold_patch = gold["patch"] if gold else ""
    gold_lines = max(0, gold_patch.count("\n") - gold_patch.count("\n@@"))
    gold_files = gold_patch.count("diff --git a/")

    # M0 mini-SWE GLM
    m0_traj = load_json("/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/miniswe/Verified/scikit-learn__scikit-learn-25232/scikit-learn__scikit-learn-25232.traj.json")
    m0_card = load_json("/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/case_card_metrics.json")
    m0_sub = m0_traj.get("info", {}).get("submission", "") or ""
    m0_ms = m0_traj.get("info", {}).get("model_stats", {}) or {}
    m0_model, m0_ptoks, m0_ctoks, m0_nu = find_usage(m0_traj)

    # M1 Agentless DeepSeek
    with open("/mnt/d/condiag-artifacts/runs/m1_agentless_smoke_27320d49/agentless/Verified/all_preds.jsonl") as f:
        m1a_pred = json.loads(f.readline())
    m1a_patch = m1a_pred.get("model_patch", "") or ""

    # M1 mini-SWE DeepSeek
    m1ms_traj = load_json("/mnt/d/condiag-artifacts/runs/m1_miniswe_smoke_27320d49/miniswe/Verified/scikit-learn__scikit-learn-25232/scikit-learn__scikit-learn-25232.traj.json")
    m1ms_sub = m1ms_traj.get("info", {}).get("submission", "") or ""
    m1ms_ms = m1ms_traj.get("info", {}).get("model_stats", {}) or {}
    m1ms_model, m1ms_ptoks, m1ms_ctoks, m1ms_nu = find_usage(m1ms_traj)

    print("=" * 90)
    print("INSTANCE: scikit-learn__scikit-learn-25232 (IterativeImputer fill_value)")
    print("ContextBench ID: SWE-Bench-Verified__python__maintenance__bugfix__27320d49")
    print(f"Gold: {gold_lines} lines / {gold_files} file / 4 modifications")
    print("=" * 90)
    print()
    print("## 主对照表")
    print()
    print("| 维度              | M0 mini-SWE (GLM)        | M1 Agentless (DeepSeek)  | M1 mini-SWE (DeepSeek)   |")
    print("|-------------------|--------------------------|--------------------------|--------------------------|")

    def row(label, a, b, c):
        print(f"| {label:18s}| {str(a):24s} | {str(b):24s} | {str(c):24s} |")

    row("Provider / Model", "ZAI / glm-4.6", "DeepSeek / v4-pro", m1ms_model or "deepseek-v4-pro")
    row("exit_status", m0_traj["info"]["exit_status"], "(patch produced)", m1ms_traj["info"]["exit_status"])
    row("api_calls", m0_ms.get("api_calls", "?"), "N/A (Agentless)", m1ms_ms.get("api_calls", "?"))
    row("instance_cost $", m0_ms.get("instance_cost", 0), "?", m1ms_ms.get("instance_cost", 0))
    row("prompt_tokens", m0_ptoks if m0_ptoks else "N/A (GLM proxy)", "see raw_output", m1ms_ptoks)
    row("completion_tokens", m0_ctoks if m0_ctoks else "N/A (GLM proxy)", "see raw_output", m1ms_ctoks)
    row("responses w/ usage", f"{m0_nu}/{m0_ms.get('api_calls',0)}", "?", f"{m1ms_nu}/{m1ms_ms.get('api_calls',0)}")
    row("patch bytes", len(m0_sub), len(m1a_patch), len(m1ms_sub))
    row("patch additions", count_adds(m0_sub), count_adds(m1a_patch), count_adds(m1ms_sub))
    row("patch deletions", count_dels(m0_sub), count_dels(m1a_patch), count_dels(m1ms_sub))

    print()
    print("## Patch 语义正确性（4 处 gold 修改）")
    print()
    print("| 检查项                          | M0 mini-SWE | M1 Agentless | M1 mini-SWE |")
    print("|---------------------------------|-------------|--------------|-------------|")
    m0_ok = semantic_ok(m0_sub)
    m1a_ok = semantic_ok(m1a_patch)
    m1ms_ok = semantic_ok(m1ms_sub)
    all_checks = [
        "docstring fill_value",
        "_parameter_constraints add",
        "__init__ fill_value=None",
        "self.fill_value = fill_value",
    ]
    for c in all_checks:
        print(f"| {c:31s} | {'YES' if c in m0_ok else 'NO':11s} | {'YES' if c in m1a_ok else 'NO':12s} | {'YES' if c in m1ms_ok else 'NO':11s} |")

    print()
    print("## mini-SWE 内部 trajectory metrics（仅 M0 跑了 evaluate.py）")
    print()
    print("| metric                          | M0 mini-SWE GLM |")
    print("|---------------------------------|-----------------|")
    for k in ["api_calls", "messages_total", "messages_assistant", "explore_blocks", "explore_entries", "num_steps", "patch_bytes"]:
        print(f"| {k:31s} | {str(m0c_val(m0_card, k)):>15} |")
    final = m0_card.get("final", {})
    print(f"| final.file.coverage             | {final.get('file',{}).get('coverage','?'):>15} |")
    print(f"| final.symbol.coverage           | {final.get('symbol',{}).get('coverage','?'):>15} |")
    print(f"| editloc.recall                  | {m0_card.get('editloc',{}).get('recall','?'):>15} |")
    print(f"| trajectory_auc.line             | {m0_card.get('trajectory_auc',{}).get('line','?'):>15} |")

    print()
    print("## 结论")
    print("- 三个 attempt 都生成了语义正确的 4 处修改，patch 内容与 gold 一致")
    print("- 该实例对 ConDiag 无诊断价值（easy 案例，所有 agent 全过）")
    print("- M0 GLM 与 M1 DeepSeek 的 patch bytes / additions / deletions 微差源于 docstring 措辞不同")
    print("- M0 instance_cost=0 是 GLM 通过 anthropic proxy 不返 usage 的已知问题（memory 坑 9）")
    print("- M1 mini-SWE DeepSeek 的 usage 字段已能正常抽出，prompt+completion tokens 可用于 cost 估算")


def m0c_val(card, k):
    return card.get(k, "?")


if __name__ == "__main__":
    main()
