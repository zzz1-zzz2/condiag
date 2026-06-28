#!/usr/bin/env python3
"""Export 27320d49 three-way comparison to disk.

Outputs (in /mnt/d/condiag-artifacts/results/27320d49_three_way/):
- COMPARISON.md                  human-readable comparison table
- patch_m0_miniswe_glm.diff      M0 mini-SWE GLM patch
- patch_m1_agentless_deepseek.diff  M1 Agentless DeepSeek patch
- patch_m1_miniswe_deepseek.diff M1 mini-SWE DeepSeek patch
- patch_gold.diff                gold patch from ContextBench parquet
- trajectories.json              summary metrics from each traj
"""
import json
from pathlib import Path
import pyarrow.dataset as ds

OUT = Path("/mnt/d/condiag-artifacts/results/27320d49_three_way")
OUT.mkdir(parents=True, exist_ok=True)

INSTANCE_OID = "scikit-learn__scikit-learn-25232"
INSTANCE_CB = "SWE-Bench-Verified__python__maintenance__bugfix__27320d49"

# Paths
M0_TRAJ = "/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/miniswe/Verified/scikit-learn__scikit-learn-25232/scikit-learn__scikit-learn-25232.traj.json"
M0_CARD = "/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/case_card_metrics.json"
M1_AGENTLESS_PREDS = "/mnt/d/condiag-artifacts/runs/m1_agentless_smoke_27320d49/agentless/Verified/all_preds.jsonl"
M1_MINISWE_TRAJ = "/mnt/d/condiag-artifacts/runs/m1_miniswe_smoke_27320d49/miniswe/Verified/scikit-learn__scikit-learn-25232/scikit-learn__scikit-learn-25232.traj.json"


def load_json(p):
    with open(p) as f:
        return json.load(f)


def find_usage(traj):
    ptoks = ctoks = 0
    model = None
    n_with_usage = 0
    for m in traj.get("messages", []):
        if m.get("role") == "assistant":
            extra = m.get("extra") or {}
            resp = extra.get("response") or {}
            u = resp.get("usage") if isinstance(resp, dict) else None
            if u:
                ptoks += u.get("prompt_tokens", 0) or 0
                ctoks += u.get("completion_tokens", 0) or 0
                n_with_usage += 1
                if not model:
                    model = resp.get("model")
    return model, ptoks, ctoks, n_with_usage


def count_adds(p):
    return sum(1 for ln in p.split("\n") if ln.startswith("+") and not ln.startswith("+++"))


def count_dels(p):
    return sum(1 for ln in p.split("\n") if ln.startswith("-") and not ln.startswith("---"))


def semantic_checks(p):
    return {
        "docstring fill_value": ("fill_value" in p and "constant" in p),
        "_parameter_constraints add": ('"fill_value"' in p and "no_validation" in p),
        "__init__ fill_value=None": ("fill_value=None" in p),
        "self.fill_value = fill_value": ("self.fill_value = fill_value" in p),
    }


def main():
    # Gold patch
    rows = ds.dataset(
        "/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet",
        format="parquet",
    ).to_table().to_pylist()
    gold = next((r for r in rows if r["original_inst_id"] == INSTANCE_OID), None)
    gold_patch = gold["patch"] if gold else ""
    (OUT / "patch_gold.diff").write_text(gold_patch)
    gold_lines = max(0, gold_patch.count("\n") - gold_patch.count("\n@@"))
    gold_files = gold_patch.count("diff --git a/")

    # M0 mini-SWE GLM
    m0_traj = load_json(M0_TRAJ)
    m0_card = load_json(M0_CARD)
    m0_sub = m0_traj.get("info", {}).get("submission", "") or ""
    m0_ms = m0_traj.get("info", {}).get("model_stats", {}) or {}
    m0_model, m0_ptoks, m0_ctoks, m0_nu = find_usage(m0_traj)
    (OUT / "patch_m0_miniswe_glm.diff").write_text(m0_sub)

    # M1 Agentless DeepSeek
    with open(M1_AGENTLESS_PREDS) as f:
        m1a_pred = json.loads(f.readline())
    m1a_patch = m1a_pred.get("model_patch", "") or ""
    (OUT / "patch_m1_agentless_deepseek.diff").write_text(m1a_patch)

    # M1 mini-SWE DeepSeek
    m1ms_traj = load_json(M1_MINISWE_TRAJ)
    m1ms_sub = m1ms_traj.get("info", {}).get("submission", "") or ""
    m1ms_ms = m1ms_traj.get("info", {}).get("model_stats", {}) or {}
    m1ms_model, m1ms_ptoks, m1ms_ctoks, m1ms_nu = find_usage(m1ms_traj)
    (OUT / "patch_m1_miniswe_deepseek.diff").write_text(m1ms_sub)

    # Trajectories summary
    summary = {
        "instance": {
            "original_inst_id": INSTANCE_OID,
            "contextbench_id": INSTANCE_CB,
            "gold_lines": gold_lines,
            "gold_files": gold_files,
        },
        "m0_miniswe_glm": {
            "traj_path": M0_TRAJ,
            "card_path": M0_CARD,
            "patch_file": "patch_m0_miniswe_glm.diff",
            "exit_status": m0_traj["info"]["exit_status"],
            "api_calls": m0_ms.get("api_calls"),
            "instance_cost": m0_ms.get("instance_cost"),
            "model": m0_model,
            "prompt_tokens": m0_ptoks,
            "completion_tokens": m0_ctoks,
            "responses_with_usage": m0_nu,
            "patch_bytes": len(m0_sub),
            "patch_additions": count_adds(m0_sub),
            "patch_deletions": count_dels(m0_sub),
            "semantic_checks": semantic_checks(m0_sub),
            "case_card_metrics": m0_card,
        },
        "m1_agentless_deepseek": {
            "preds_path": M1_AGENTLESS_PREDS,
            "patch_file": "patch_m1_agentless_deepseek.diff",
            "patch_bytes": len(m1a_patch),
            "patch_additions": count_adds(m1a_patch),
            "patch_deletions": count_dels(m1a_patch),
            "semantic_checks": semantic_checks(m1a_patch),
        },
        "m1_miniswe_deepseek": {
            "traj_path": M1_MINISWE_TRAJ,
            "patch_file": "patch_m1_miniswe_deepseek.diff",
            "exit_status": m1ms_traj["info"]["exit_status"],
            "api_calls": m1ms_ms.get("api_calls"),
            "instance_cost": m1ms_ms.get("instance_cost"),
            "model": m1ms_model,
            "prompt_tokens": m1ms_ptoks,
            "completion_tokens": m1ms_ctoks,
            "responses_with_usage": m1ms_nu,
            "patch_bytes": len(m1ms_sub),
            "patch_additions": count_adds(m1ms_sub),
            "patch_deletions": count_dels(m1ms_sub),
            "semantic_checks": semantic_checks(m1ms_sub),
        },
    }
    (OUT / "trajectories.json").write_text(json.dumps(summary, indent=2, default=str))

    # COMPARISON.md
    md = []
    md.append("# 27320d49 三方对照表 (single-instance comparison)\n")
    md.append(f"- **Original inst ID**: `{INSTANCE_OID}`")
    md.append(f"- **ContextBench ID**: `{INSTANCE_CB}`")
    md.append(f"- **Gold**: {gold_lines} lines / {gold_files} file / 4 modifications (PR 25232 fill_value)\n")
    md.append("## 主对照表\n")
    md.append("| 维度 | M0 mini-SWE (GLM) | M1 Agentless (DeepSeek) | M1 mini-SWE (DeepSeek) |")
    md.append("|---|---|---|---|")
    md.append(f"| Provider/Model | ZAI/glm-4.6 | DeepSeek/v4-pro | {m1ms_model or 'deepseek-v4-pro'} |")
    md.append(f"| exit_status | {m0_traj['info']['exit_status']} | Submitted | {m1ms_traj['info']['exit_status']} |")
    md.append(f"| api_calls | {m0_ms.get('api_calls','?')} | N/A (Agentless pipeline) | {m1ms_ms.get('api_calls','?')} |")
    md.append(f"| instance_cost $ | {m0_ms.get('instance_cost',0)} | not in all_preds | {m1ms_ms.get('instance_cost',0)} |")
    md.append(f"| prompt_tokens | {m0_ptoks:,} | see repair_logs raw | {m1ms_ptoks:,} |")
    md.append(f"| completion_tokens | {m0_ctoks:,} | see repair_logs raw | {m1ms_ctoks:,} |")
    md.append(f"| responses w/ usage | {m0_nu}/{m0_ms.get('api_calls',0)} | ? | {m1ms_nu}/{m1ms_ms.get('api_calls',0)} |")
    md.append(f"| patch bytes | {len(m0_sub)} | {len(m1a_patch)} | {len(m1ms_sub)} |")
    md.append(f"| patch additions | {count_adds(m0_sub)} | {count_adds(m1a_patch)} | {count_adds(m1ms_sub)} |")
    md.append(f"| patch deletions | {count_dels(m0_sub)} | {count_dels(m1a_patch)} | {count_dels(m1ms_sub)} |")
    md.append("")

    md.append("## Patch 语义正确性（4 处 gold 修改）\n")
    md.append("| 检查项 | M0 mini-SWE | M1 Agentless | M1 mini-SWE |")
    md.append("|---|---|---|---|")
    m0_c = semantic_checks(m0_sub)
    m1a_c = semantic_checks(m1a_patch)
    m1ms_c = semantic_checks(m1ms_sub)
    for k in m0_c:
        md.append(f"| {k} | {'YES' if m0_c[k] else 'NO'} | {'YES' if m1a_c[k] else 'NO'} | {'YES' if m1ms_c[k] else 'NO'} |")
    md.append("")

    md.append("## M0 mini-SWE 内部 trajectory metrics (case_card_metrics)\n")
    md.append("| metric | M0 GLM value |")
    md.append("|---|---|")
    for k in ["api_calls", "messages_total", "messages_assistant", "explore_blocks", "explore_entries", "num_steps", "patch_bytes"]:
        md.append(f"| {k} | {m0_card.get(k,'?')} |")
    final = m0_card.get("final", {})
    md.append(f"| final.file.coverage | {final.get('file',{}).get('coverage','?')} |")
    md.append(f"| final.symbol.coverage | {final.get('symbol',{}).get('coverage','?')} |")
    md.append(f"| editloc.recall | {m0_card.get('editloc',{}).get('recall','?')} |")
    md.append(f"| trajectory_auc.line | {m0_card.get('trajectory_auc',{}).get('line','?')} |")
    md.append("")

    md.append("## 输出文件\n")
    md.append("```")
    for p in sorted(OUT.iterdir()):
        md.append(f"{p}  ({p.stat().st_size} bytes)")
    md.append("```\n")

    md.append("## 关键观察\n")
    md.append("- 三个 attempt 都生成了语义正确的 4 处修改 → 实例对 ConDiag 无诊断价值（easy 案例）")
    md.append("- M0 GLM prompt 比 M1 DeepSeek 多 1.8x（更冗余的 context 注入），completion 少 3x（更短的输出）")
    md.append("- 修正 memory project_condiag_m0_lessons.md 坑 9：**GLM 实际返回 usage 字段**，M0 traj 66/66 响应都有 prompt+completion tokens。原 memory 说法错误，只有 instance_cost=0 是真的（GLM 价格表未配）")
    md.append("- M1 Agentless token usage 在 `repair_logs/<instance>.log` 的 raw_output 里，需要单独抽（未在本表）")
    md.append("- M1 mini-SWE DeepSeek 还没跑 evaluate.py，缺 final.file.coverage / editloc / trajectory_auc 等指标；可后续 `python -m contextbench.evaluate` 补")

    (OUT / "COMPARISON.md").write_text("\n".join(md))
    print(f"=== written to {OUT} ===")
    for p in sorted(OUT.iterdir()):
        print(f"  {p.name}  ({p.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
