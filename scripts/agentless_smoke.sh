#!/usr/bin/env bash
# ConDiag Agentless smoke test on scikit-learn PR 25232 (IterativeImputer fill_value).
# Same instance as M0 mini-SWE-Agent — lets us compare Attempt-1 trajectories.
set -uo pipefail

source ~/condiag/scripts/env.sh

AGENTLESS_ROOT=~/condiag/ContextBench/agent-frameworks/agentless
cd "${AGENTLESS_ROOT}" || exit 1
source script/api_key.sh
export PYTHONPATH="$(pwd)"

TARGET_ID="SWE-Bench-Verified__python__maintenance__bugfix__27320d49"
SWEBENCH_LANG="python"
FOLDER_NAME="Verified/0_${TARGET_ID}"
BENCH_NAME="Verified"
DATA_ROOT="$(pwd)/results/${FOLDER_NAME}/input_data"

# Build input_data/one.jsonl with the minimal record Agentless expects.
mkdir -p "${DATA_ROOT}/${SWEBENCH_LANG}"
python - <<PY
import json, os
rec = {
    "org": "scikit-learn",
    "repo": "scikit-learn",
    "instance_id": os.environ["TARGET_ID"],
    "base": {"sha": "f7eea978097085a6781a0e92fc14ba7712a52d75"},
    "resolved_issues": [{
        "title": "IterativeImputer has no parameter fill_value",
        "body": ""  # body intentionally empty — Agentless only uses title for smoke
    }],
}
out = os.path.join(os.environ["DATA_ROOT"], os.environ["SWEBENCH_LANG"], "one.jsonl")
with open(out, "w") as f:
    f.write(json.dumps(rec) + "\n")
print("wrote", out)
PY

export TARGET_ID SWEBENCH_LANG FOLDER_NAME BENCH_NAME DATA_ROOT

LOG_DIR=/mnt/d/condiag-artifacts/runs/m1_agentless_smoke_27320d49
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/run.$(date +%Y%m%d_%H%M%S).log"
echo "logging to ${LOG_FILE}"

# Clean previous partial run dirs but keep input_data we just wrote.
find "results/${FOLDER_NAME}" -mindepth 1 -maxdepth 1 ! -name "input_data" -exec rm -rf {} +

./script/run_single_instance.sh 2>&1 | tee "${LOG_FILE}"
