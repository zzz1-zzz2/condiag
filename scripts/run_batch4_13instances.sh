#!/bin/bash
# Run remaining 13 Pilot50 instances with DeepSeek V4-pro
set -e

INSTANCES=(
    "django__django-11433"
    "django__django-12262"
    "django__django-12663"
    "django__django-14140"
    "django__django-14351"
    "django__django-14787"
    "django__django-14792"
    "huggingface__transformers-20136"
    "huggingface__transformers-21768"
    "sympy__sympy-12096"
    "sympy__sympy-12419"
    "sympy__sympy-13852"
    "sympy__sympy-23824"
)

INSTANCES_STR=$(IFS=,; echo "${INSTANCES[*]}")

STAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_BASE="/mnt/d/condiag-artifacts/runs/pilot50_batch4_${STAMP}"
mkdir -p "$OUTPUT_BASE"

echo "=== Pilot50 Batch4 (remaining 13 instances) ==="
echo "Start: $(date)"
echo "Output: $OUTPUT_BASE"
echo "Instances: ${#INSTANCES[@]}"
echo ""

source ~/condiag/scripts/env.sh

cd ~/condiag/ContextBench

python3 -m contextbench.run \
    --agent miniswe \
    --bench Verified \
    --instances "$INSTANCES_STR" \
    --output "$OUTPUT_BASE" \
    --timeout 3600 \
    2>&1 | tee "$OUTPUT_BASE/run.log"

echo ""
echo "Done: $(date)"
echo "Output: $OUTPUT_BASE"
