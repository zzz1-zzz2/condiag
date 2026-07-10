#!/bin/bash
set -e

INSTANCES=(
    "Multi-SWE-Bench__c__maintenance__bugfix__a47dfbbf"
    "Multi-SWE-Bench__cpp__maintenance__bugfix__1ec2c84a"
    "Multi-SWE-Bench__cpp__maintenance__bugfix__4a37a167"
    "Multi-SWE-Bench__go__maintenance__bugfix__6e022940"
    "Multi-SWE-Bench__java__maintenance__bugfix__14da06bc"
    "Multi-SWE-Bench__javascript__maintenance__bugfix__98bbaed2"
    "Multi-SWE-Bench__c__maintenance__bugfix__49f4a0f4"
    "Multi-SWE-Bench__typescript__maintenance__bugfix__2fb50735"
)

INSTANCES_STR=$(IFS=,; echo "${INSTANCES[*]}")

STAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_BASE="/mnt/d/condiag-artifacts/runs/condiag_batch5b_multi_8_remaining_${STAMP}"
mkdir -p "$OUTPUT_BASE"

echo "=== Condiag Batch5b Remaining: 8 Multi-SWE-Bench ==="
echo "Start: $(date)"
echo "Output: $OUTPUT_BASE"
echo "Instances: ${#INSTANCES[@]}"
echo ""

export PYTHONPATH=/home/swelite/condiag/ContextBench:$PYTHONPATH
source ~/condiag/scripts/env.sh 2>/dev/null

cd /home/swelite/condiag/ContextBench

python3 -m contextbench.run \
    --agent miniswe \
    --bench Multi \
    --instances "$INSTANCES_STR" \
    --output "$OUTPUT_BASE" \
    --timeout 3600 \
    2>&1 | tee "$OUTPUT_BASE/run.log"

echo ""
echo "Done: $(date)"
echo "Output: $OUTPUT_BASE"
