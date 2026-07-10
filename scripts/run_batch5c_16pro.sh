#!/bin/bash
# Batch5c: Run 16 SWE-Bench-Pro instances for Attempt-1 traj
set -e

INSTANCES=(
    "SWE-Bench-Pro__go__maintenance__bugfix__4df06349"
    "SWE-Bench-Pro__go__maintenance__bugfix__720b4d92"
    "SWE-Bench-Pro__go__maintenance__bugfix__997c7afd"
    "SWE-Bench-Pro__javascript__maintenance__bugfix__5c001746"
    "SWE-Bench-Pro__javascript__maintenance__bugfix__7b6185af"
    "SWE-Bench-Pro__python__maintenance__bugfix__0c4f8d13"
    "SWE-Bench-Pro__python__maintenance__bugfix__1cae51cc"
    "SWE-Bench-Pro__python__maintenance__bugfix__20f502e0"
    "SWE-Bench-Pro__python__maintenance__bugfix__31f13b61"
    "SWE-Bench-Pro__python__maintenance__bugfix__462b957d"
    "SWE-Bench-Pro__python__maintenance__bugfix__5b2cf9bb"
    "SWE-Bench-Pro__python__maintenance__bugfix__64469377"
    "SWE-Bench-Pro__python__maintenance__bugfix__6ebb54dc"
    "SWE-Bench-Pro__python__maintenance__bugfix__942d0b14"
    "SWE-Bench-Pro__python__maintenance__bugfix__95b4abc1"
    "SWE-Bench-Pro__python__maintenance__bugfix__b9f9961f"
)

INSTANCES_STR=$(IFS=,; echo "${INSTANCES[*]}")

STAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_BASE="/mnt/d/condiag-artifacts/runs/condiag_batch5c_pro_16_${STAMP}"
mkdir -p "$OUTPUT_BASE"

echo "=== Condiag Batch5c: 16 SWE-Bench-Pro ==="
echo "Start: $(date)"
echo "Output: $OUTPUT_BASE"
echo "Instances: ${#INSTANCES[@]}"
echo ""

export PYTHONPATH=/home/swelite/condiag/ContextBench:$PYTHONPATH
source ~/condiag/scripts/env.sh 2>/dev/null

cd /home/swelite/condiag/ContextBench

python3 -m contextbench.run     --agent miniswe     --bench Pro     --instances "$INSTANCES_STR"     --output "$OUTPUT_BASE"     --timeout 3600     2>&1 | tee "$OUTPUT_BASE/run.log"

echo ""
echo "Done: $(date)"
echo "Output: $OUTPUT_BASE"
