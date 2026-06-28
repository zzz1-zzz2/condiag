#!/usr/bin/env bash
# SWE-bench-style official test for our M0 instance (v2 — Python-driven pytest).
set -uo pipefail

IMAGE="swebench/sweb.eval.x86_64.scikit-learn_1776_scikit-learn-25232:latest"
OUT_DIR="/mnt/d/condiag-artifacts/runs/m0_miniswe_27320d49/official_tests"

CONTAINER_ID=$(docker run --rm -d \
  -v "$OUT_DIR:/root/out" \
  --platform linux/amd64 \
  "$IMAGE" sleep 7200)
echo "container: $CONTAINER_ID"
trap "docker rm -f $CONTAINER_ID >/dev/null 2>&1 || true" EXIT

# Helper: copy a pytest runner script into the container
docker exec "$CONTAINER_ID" bash -lc 'mkdir -p /root/work'
docker cp "$OUT_DIR/../../_runner.py" "$CONTAINER_ID:/root/work/runner.py" 2>/dev/null \
  || true

run_phase() {
  local phase="$1"
  local apply_model="$2"  # "yes" or "no"

  echo ""
  echo "============================================================"
  echo "PHASE: $phase   (apply_model=$apply_model)"
  echo "============================================================"

  # Reset + apply test_patch (+ optional model_patch)
  docker exec "$CONTAINER_ID" bash -lc "
    cd /testbed && \
    git checkout -- . 2>&1 | tail -3 ; \
    git apply /root/out/test_patch.diff && echo 'test_patch OK' ; \
    if [ '$apply_model' = 'yes' ]; then \
      git apply /root/out/model_patch.diff && echo 'model_patch OK' ; \
    fi
  " 2>&1 | tee "$OUT_DIR/${phase}.apply.log"

  # f2p
  echo "--- f2p ---"
  docker exec "$CONTAINER_ID" bash -lc '
    source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && \
    cd /testbed && \
    python -m pytest \
      "sklearn/impute/tests/test_impute.py::test_iterative_imputer_constant_fill_value" \
      -v --no-header --tb=short -p no:cacheprovider 2>&1 | tail -25
  ' | tee "$OUT_DIR/${phase}.f2p.log"

  # p2p — use Python subprocess to avoid shell quoting issues
  echo "--- p2p (214 ids) ---"
  docker exec "$CONTAINER_ID" bash -lc '
    source /opt/miniconda3/etc/profile.d/conda.sh && conda activate testbed && \
    cd /testbed && \
    python - <<PYEOF
import json, subprocess, sys
ids = json.load(open("/root/out/p2p.json"))
print(f"running {len(ids)} p2p tests")
r = subprocess.run(
    ["python", "-m", "pytest"] + ids + ["-p", "no:cacheprovider", "--tb=line", "-q"],
    capture_output=True, text=True, timeout=900,
)
print("STDOUT TAIL:")
print(r.stdout[-3500:])
print("STDERR TAIL:")
print(r.stderr[-500:])
# Print summary line (e.g., "5 passed, 3 failed in 12.3s")
for line in r.stdout.splitlines()[-1:]:
    print("SUMMARY_LINE:", line)
PYEOF
  ' | tee "$OUT_DIR/${phase}.p2p.log"
}

run_phase "baseline" "no"
run_phase "patched"  "yes"

echo ""
echo "============================================================"
echo "FINAL SUMMARY"
echo "============================================================"
for phase in baseline patched; do
  f2p_line=$(grep -E "PASSED|FAILED|ERROR" "$OUT_DIR/${phase}.f2p.log" | head -1 || echo "n/a")
  p2p_summary=$(grep -E "passed|failed|error" "$OUT_DIR/${phase}.p2p.log" | tail -1 || echo "n/a")
  printf "%-10s f2p=%s  p2p=%s\n" "$phase" "$f2p_line" "$p2p_summary"
done
