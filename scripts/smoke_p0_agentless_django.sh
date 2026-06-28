#!/usr/bin/env bash
# P0-2 smoke: run Agentless on django-13195 to verify git proxy + HTTPS_PROXY
# let Agentless's `git clone https://github.com/...` succeed from inside the
# Ubuntu distro. Single instance only — fast fail/success signal.
set -uo pipefail

source ~/condiag/scripts/env.sh

STAMP=$(date +%Y%m%d_%H%M%S)
OUT=/mnt/d/condiag-artifacts/runs/p0_agentless_django_${STAMP}
mkdir -p "$OUT"

echo "=== smoke start: $(date) ===" > "$OUT/smoke.log"
echo "OUTDIR=$OUT" >> "$OUT/smoke.log"
echo "instance=django__django-13195" >> "$OUT/smoke.log"
echo "git http.proxy: $(git config --global --get http.proxy)" >> "$OUT/smoke.log"
echo "HTTPS_PROXY: ${HTTPS_PROXY:-}" >> "$OUT/smoke.log"

INST="django__django-13195"
INST_CB=$(python - <<'PY'
import pyarrow.dataset as ds
rows = ds.dataset("/home/swelite/condiag/ContextBench/data/contextbench_verified.parquet", format="parquet").to_table().to_pylist()
m = next((r for r in rows if r["original_inst_id"] == "django__django-13195"), None)
print(m["instance_id"] if m else "")
PY
)
if [ -z "$INST_CB" ]; then
  echo "ERROR: django__django-13195 not found in parquet" >> "$OUT/smoke.log"
  exit 1
fi
echo "CB id: $INST_CB" >> "$OUT/smoke.log"

cd ~/condiag/ContextBench/agent-frameworks/agentless || exit 1
source script/api_key.sh
export PYTHONPATH="$(pwd)"

export TARGET_ID="$INST_CB"
export SWEBENCH_LANG="python"
export FOLDER_NAME="Verified/p0_django_13195"
export BENCH_NAME="Verified"
export DATA_ROOT="$(pwd)/results/${FOLDER_NAME}/input_data"
mkdir -p "${DATA_ROOT}/${SWEBENCH_LANG}"

python - <<PY >> "$OUT/smoke.log" 2>&1
import json, os
rec = {
    "org": "django",
    "repo": "django",
    "instance_id": os.environ["TARGET_ID"],
    "base": {"sha": ""},
    "resolved_issues": [{"title": "(P0 smoke — title only)", "body": ""}],
}
out = os.path.join(os.environ["DATA_ROOT"], os.environ["SWEBENCH_LANG"], "one.jsonl")
with open(out, "w") as f:
    f.write(json.dumps(rec) + "\n")
print("wrote", out)
PY

find "results/${FOLDER_NAME}" -mindepth 1 -maxdepth 1 ! -name "input_data" -exec rm -rf {} + 2>/dev/null
./script/run_single_instance.sh >> "$OUT/smoke.log" 2>&1
RC=$?

if [ -d "results/${FOLDER_NAME}" ]; then
  cp -r "results/${FOLDER_NAME}" "$OUT/django_13195_results" 2>/dev/null || true
fi

echo >> "$OUT/smoke.log"
echo "=== smoke end: $(date) rc=$RC ===" >> "$OUT/smoke.log"
echo "OUTDIR=$OUT" > /tmp/p0_agentless_last_outdir
