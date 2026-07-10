#!/bin/bash
# Batch3: Run 20 mini-SWE instances via ContextBench
# Output: /mnt/d/condiag-artifacts/runs/condiag_batch3_$(date +%Y%m%d_%H%M%S)/
set -e

INSTANCES=(
    "astropy__astropy-13398"
    "django__django-11400"
    "sympy__sympy-13877"
    "sympy__sympy-16597"
    "django__django-13449"
    "django__django-15863"
    "django__django-12858"
    "django__django-12193"
    "django__django-11163"
    "django__django-11555"
    "django__django-13012"
    "django__django-13109"
    "sympy__sympy-13551"
    "sympy__sympy-12489"
    "sympy__sympy-13372"
    "sympy__sympy-15017"
    "sympy__sympy-13615"
    "sympy__sympy-17318"
    "sympy__sympy-22714"
    "sympy__sympy-14976"
)

INSTANCES_STR=$(IFS=,; echo "${INSTANCES[*]}")

OUTPUT_BASE="/mnt/d/condiag-artifacts/runs/condiag_batch3_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTPUT_BASE"

echo "=== Condiag Batch3 ==="
echo "Start: $(date)"
echo "Output: $OUTPUT_BASE"
echo "Instances: ${#INSTANCES[@]}"
echo ""

export PYTHONPATH=/home/swelite/condiag/ContextBench:$PYTHONPATH
export DEEPSEEK_API_KEY="$(grep DEEPSEEK_API_KEY ~/.config/mini-swe-agent/.env | cut -d= -f2)"

cd /home/swelite/condiag/ContextBench

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
