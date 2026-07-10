#!/bin/bash
# Batch5d: Run 16 SWE-PolyBench instances for Attempt-1 traj
# (#39 SWE-PolyBench__python__evolution__feature__8bb50331 skipped: no Docker image)
set -e

INSTANCES=(
    "SWE-PolyBench__javascript__maintenance__bugfix__2d7a3934"
    "SWE-PolyBench__javascript__maintenance__bugfix__4d090fc6"
    "SWE-PolyBench__javascript__maintenance__bugfix__63513ada"
    "SWE-PolyBench__python__evolution__feature__4a329645"
    "SWE-PolyBench__python__evolution__feature__66f97093"
    "SWE-PolyBench__python__evolution__feature__7fe6d907"
    "SWE-PolyBench__python__evolution__feature__9e2901b5"
    "SWE-PolyBench__python__evolution__feature__b4302840"
    "SWE-PolyBench__python__evolution__feature__ebb79d55"
    "SWE-PolyBench__python__evolution__refactor__57ad5598"
    "SWE-PolyBench__python__maintenance__bugfix__2b00c0d1"
    "SWE-PolyBench__python__maintenance__bugfix__37455515"
    "SWE-PolyBench__python__maintenance__bugfix__ed58622a"
    "SWE-PolyBench__typescript__evolution__feature__34826a6a"
    "SWE-PolyBench__typescript__evolution__feature__41cd3842"
    "SWE-PolyBench__typescript__maintenance__bugfix__708894b2"
)

INSTANCES_STR=$(IFS=,; echo "${INSTANCES[*]}")

STAMP=$(date +%Y%m%d_%H%M%S)
OUTPUT_BASE="/mnt/d/condiag-artifacts/runs/condiag_batch5d_poly_16_${STAMP}"
mkdir -p "$OUTPUT_BASE"

echo "=== Condiag Batch5d: 16 SWE-PolyBench ==="
echo "Start: $(date)"
echo "Output: $OUTPUT_BASE"
echo "Instances: ${#INSTANCES[@]}"
echo ""

export PYTHONPATH=/home/swelite/condiag/ContextBench:$PYTHONPATH
source ~/condiag/scripts/env.sh 2>/dev/null

cd /home/swelite/condiag/ContextBench

python3 -m contextbench.run     --agent miniswe     --bench Poly     --instances "$INSTANCES_STR"     --output "$OUTPUT_BASE"     --timeout 3600     2>&1 | tee "$OUTPUT_BASE/run.log"

echo ""
echo "Done: $(date)"
echo "Output: $OUTPUT_BASE"
