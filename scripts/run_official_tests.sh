#!/usr/bin/env bash
# SWE-bench-style official test for our M0 instance:
#   Phase A (baseline, no model_patch): f2p should FAIL, p2p should PASS
#   Phase B (with model_patch): f2p should PASS, p2p should PASS (no regressions)

set -uo pipefail

IMAGE="swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25232:latest"
OUT_DIR="/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/official_tests"
mkdir -p "$OUT_DIR"

CONTAINER_ID=$(docker run --rm -d \
  -v "$OUT_DIR:/root/out" \
  --platform linux/amd64 \
  "$IMAGE" sleep 7200)
echo "container: $CONTAINER_ID"
trap "docker rm -f $CONTAINER_ID >/dev/null 2>&1 || true" EXIT

run_tests() {
  local phase="$1"      # baseline | patched
  local extra_apply="$2" # "" or "/root/out/model_patch.diff"
  local out_prefix="$OUT_DIR/${phase}"

  echo ""
  echo "============================================================"
  echo "PHASE: $phase  (extra_apply=${extra_apply:-none})"
  echo "============================================================"

  # Reset repo to base_commit, then apply test_patch + (optional) model_patch
  docker exec "$CONTAINER_ID" bash -lc "
    cd /testbed && \
    git checkout -- . && \
    git apply /root/out/test_patch.diff && \
    echo 'test_patch applied' && \
    if [ -n '$extra_apply' ]; then \
      git apply $extra_apply && echo 'model_patch applied'; \
    fi
  " 2>&1 | tee "$out_prefix.apply.log"

  # Run the f2p test
  echo "--- f2p test ---"
  docker exec "$CONTAINER_ID" bash -lc "
    source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && \
    cd /testbed && \
    python -m pytest \
      'sklearn/impute/tests/test_impute.py::test_iterative_imputer_constant_fill_value' \
      -v --no-header --tb=short -p no:cacheprovider 2>&1 | tail -50
  " | tee "$out_prefix.f2p.log"

  # Run p2p tests (the full 214, parametrized)
  echo "--- p2p tests (214 total) ---"
  docker exec "$CONTAINER_ID" bash -lc "
    source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && \
    cd /testbed && \
    python -m pytest \
      \$(python -c \"import json; print(' '.join(json.load(open('/root/out/p2p.json'))))\") \
      -v --no-header --tb=line -p no:cacheprovider -q 2>&1 | tail -60
  " | tee "$out_prefix.p2p.log"
}

# Phase A: baseline (test_patch only, no model_patch)
run_tests "baseline" ""

# Phase B: test_patch + model_patch
run_tests "patched" "/root/out/model_patch.diff"

echo ""
echo "============================================================"
echo "SUMMARY"
echo "============================================================"
for phase in baseline patched; do
  echo "--- $phase ---"
  echo "f2p:"
  grep -E "PASSED|FAILED|ERROR" "$OUT_DIR/${phase}.f2p.log" | head -3 || true
  echo "p2p tail:"
  tail -3 "$OUT_DIR/${phase}.p2p.log" || true
done
