#!/usr/bin/env bash
# SWE-bench-style official test (v3 — simpler: run whole test_impute.py module).
set -uo pipefail

IMAGE="swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25232:latest"
OUT_DIR="/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/official_tests"

CONTAINER_ID=$(docker run --rm -d \
  -v "$OUT_DIR:/root/out" \
  --platform linux/amd64 \
  "$IMAGE" sleep 7200)
echo "container: $CONTAINER_ID"
trap "docker rm -f $CONTAINER_ID >/dev/null 2>&1 || true" EXIT

run_phase() {
  local phase="$1"
  local apply_model="$2"

  echo ""
  echo "============================================================"
  echo "PHASE: $phase   (apply_model=$apply_model)"
  echo "============================================================"

  docker exec "$CONTAINER_ID" bash -lc "
    cd /testbed && \
    git checkout -- . 2>&1 | tail -3 ; \
    git apply /root/out/test_patch.diff && echo 'test_patch OK' ; \
    if [ '$apply_model' = 'yes' ]; then \
      git apply /root/out/model_patch.diff && echo 'model_patch OK' ; \
    fi
  " 2>&1 | tee "$OUT_DIR/${phase}.apply.log"

  # f2p test alone
  echo "--- f2p ---"
  docker exec "$CONTAINER_ID" bash -lc '
    source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && \
    cd /testbed && \
    python -m pytest \
      "sklearn/impute/tests/test_impute.py::test_iterative_imputer_constant_fill_value" \
      -v --no-header --tb=short -p no:cacheprovider 2>&1 | tail -25
  ' | tee "$OUT_DIR/${phase}.f2p.log"

  # p2p: run the full test_impute.py module (covers all 214 + more)
  echo "--- test_impute.py module (covers p2p) ---"
  docker exec "$CONTAINER_ID" bash -lc '
    source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && \
    cd /testbed && \
    python -m pytest \
      sklearn/impute/tests/test_impute.py \
      -p no:cacheprovider --tb=line -q 2>&1 | tail -20
  ' | tee "$OUT_DIR/${phase}.p2p.log"
}

run_phase "baseline" "no"
run_phase "patched"  "yes"

echo ""
echo "============================================================"
echo "FINAL SUMMARY"
echo "============================================================"
for phase in baseline patched; do
  f2p=$(grep -oE "(PASSED|FAILED|ERROR)" "$OUT_DIR/${phase}.f2p.log" | head -1)
  p2p=$(grep -E "[0-9]+ (passed|failed)" "$OUT_DIR/${phase}.p2p.log" | tail -1)
  printf "%-10s  f2p=%-10s  p2p=%s\n" "$phase" "$f2p" "$p2p"
done
