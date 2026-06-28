#!/usr/bin/env bash
# Mini-SWE-Agent smoke test on scikit-learn PR 25232 with DeepSeek V4-pro.
# Same instance as M0 (GLM) and M1 Agentless (DeepSeek) for cross-comparison.
set -uo pipefail

source ~/condiag/scripts/env.sh

OUT=/mnt/d/condiag-artifacts/runs/m1_miniswe_smoke_27320d49
mkdir -p "$OUT"

cd ~/condiag/ContextBench || exit 1

echo "=== docker sanity ==="
docker info >/dev/null 2>&1 && echo "docker: OK" || { echo "docker: DOWN"; exit 1; }
docker images | grep "scikit-learn_1776_scikit-learn-25232" | head -1

echo "=== mini-SWE config model_name ==="
grep "model_name" agent-frameworks/mini-swe-agent/multi-poly-pro-verified/configs/swebench_following_context.yaml

echo "=== starting mini-SWE on 27320d49 (DeepSeek V4-pro) ==="
python -m contextbench.run \
  --agent miniswe \
  --bench Verified \
  --instances "scikit-learn__scikit-learn-25232" \
  --output "$OUT" \
  --rerun \
  --timeout 1800 2>&1 | tee "$OUT/run.log"
