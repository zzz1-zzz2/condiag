#!/bin/bash
# Batch5a: Run 11 remaining Verified instances for Attempt-1 traj
set -e

INSTANCES=(
    "django__django-11433"
    "django__django-12262"
    "django__django-12663"
    "django__django-14140"
    "django__django-14351"
    "django__django-14787"
    "django__django-14792"
    "sympy__sympy-12096"
    "sympy__sympy-12419"
    "sympy__sympy-13852"
    "sympy__sympy-23824"
)

INSTANCES_STR=$(IFS=,; echo "${INSTANCES[*]}")

STAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_BASE="/mnt/d/condiag-artifacts/runs/condiag_batch5a_verified_11_${STAMP}"
mkdir -p "$OUTPUT_BASE"

echo "=== Condiag Batch5a: 11 Verified (missing traj) ==="
echo "Start: $(date)"
echo "Output: $OUTPUT_BASE"
echo "Instances: ${#INSTANCES[@]}"
echo ""

export PYTHONPATH=/home/swelite/condiag/ContextBench:$PYTHONPATH
source ~/condiag/scripts/env.sh

cd /home/swelite/condiag/ContextBench

python3 -m contextbench.run     --agent miniswe     --bench Verified     --instances "$INSTANCES_STR"     --output "$OUTPUT_BASE"     --timeout 3600     2>&1 | tee "$OUTPUT_BASE/run.log"

echo ""
echo "Done: $(date)"
echo "Output: $OUTPUT_BASE"
