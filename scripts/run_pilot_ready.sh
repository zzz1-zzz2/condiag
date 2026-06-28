#!/usr/bin/env bash
# Run mini-SWE + Agentless on the 2 Pilot instances whose images are ready.
# astropy-13398 (large) + django-13195 (medium) — both Verified source.
set -uo pipefail

source ~/condiag/scripts/env.sh

STAMP=$(date +%Y%m%d_%H%M%S)
ROOT=/mnt/d/condiag-artifacts/runs/pilot_ready_${STAMP}
mkdir -p "$ROOT"

INSTANCES="astropy__astropy-13398,django__django-13195"

cd ~/condiag/ContextBench || exit 1

echo "=== docker sanity ==="
docker info >/dev/null 2>&1 && echo "docker: OK" || { echo "docker: DOWN"; exit 1; }
for img in \
  "swebench/sweb.eval.x86_64.astropy_1776_astropy-13398" \
  "swebench/sweb.eval.x86_64.django_1776_django-13195"; do
  docker images --format '{{.Repository}}:{{.Tag}}' | grep -q "^${img}:" && echo "  ✓ $img" || echo "  ✗ $img MISSING"
done

echo
echo "=== mini-SWE config model_name ==="
grep "model_name" agent-frameworks/mini-swe-agent/multi-poly-pro-verified/configs/swebench_following_context.yaml

# =========== Stage A: mini-SWE (DeepSeek V4-pro) ===========
MINISWE_OUT="$ROOT/miniswe"
mkdir -p "$MINISWE_OUT"
echo
echo "=== [A] starting mini-SWE on astropy-13398 + django-13195 (DeepSeek V4-pro) ==="
python -m contextbench.run \
  --agent miniswe \
  --bench Verified \
  --instances "$INSTANCES" \
  --output "$MINISWE_OUT" \
  --rerun \
  --timeout 1800 2>&1 | tee "$MINISWE_OUT/run.log"

echo
echo "=== [A] done at $(date) ==="

# =========== Stage B: Agentless (DeepSeek V4-pro) ===========
AGENTLESS_OUT="$ROOT/agentless"
mkdir -p "$AGENTLESS_OUT"

cd ~/condiag/ContextBench/agent-frameworks/agentless || exit 1
source script/api_key.sh
export PYTHONPATH="$(pwd)"

echo
echo "=== [B] starting Agentless on astropy-13398 + django-13195 (DeepSeek V4-pro) ==="
echo "  (Agentless runs sequentially per instance via its own pipeline)"

for INST in astropy__astropy-13398 django__django-13195; do
  echo "--- agentless on $INST ---"
  # Agentless takes instance_id via TARGET_ID env, expects CB-formatted ID
  # We look it up from parquet to get the ContextBench canonical ID
  INST_CB=$(python - <<PY
import pyarrow.dataset as ds
rows = ds.dataset("/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet", format="parquet").to_table().to_pylist()
m = next((r for r in rows if r["original_inst_id"] == "$INST"), None)
print(m["instance_id"] if m else "")
PY
  )
  if [ -z "$INST_CB" ]; then
    echo "  ERROR: $INST not found in parquet"
    continue
  fi
  echo "  CB id: $INST_CB"

  export TARGET_ID="$INST_CB"
  export SWEBENCH_LANG="python"
  export FOLDER_NAME="Verified/pilot_${INST}"
  export BENCH_NAME="Verified"
  export DATA_ROOT="$(pwd)/results/${FOLDER_NAME}/input_data"
  mkdir -p "${DATA_ROOT}/${SWEBENCH_LANG}"

  # build one.jsonl
  python - <<PY
import json, os
rec = {
    "org": "${INST%%__*}",
    "repo": "${INST#*__}",
    "instance_id": os.environ["TARGET_ID"],
    "base": {"sha": ""},
    "resolved_issues": [{"title": "(Agentless smoke — title only)", "body": ""}],
}
out = os.path.join(os.environ["DATA_ROOT"], os.environ["SWEBENCH_LANG"], "one.jsonl")
with open(out, "w") as f:
    f.write(json.dumps(rec) + "\n")
print("wrote", out)
PY

  find "results/${FOLDER_NAME}" -mindepth 1 -maxdepth 1 ! -name "input_data" -exec rm -rf {} + 2>/dev/null
  ./script/run_single_instance.sh 2>&1 | tee "$AGENTLESS_OUT/${INST}.log"

  # move results into our artifact root
  if [ -d "results/${FOLDER_NAME}" ]; then
    cp -r "results/${FOLDER_NAME}" "$AGENTLESS_OUT/${INST}_results" 2>/dev/null || true
  fi
done

echo
echo "=== [B] done at $(date) ==="
echo
echo "=== summary ==="
ls "$ROOT/" 2>&1
echo "artifacts in: $ROOT"
