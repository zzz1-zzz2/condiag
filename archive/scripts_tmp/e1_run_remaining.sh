#!/bin/bash
# Run 3 remaining baselines sequentially
set -e
cd ~/condiag
for bl in feedback_retry broad_expansion condiag_retry_v2_alpha; do
    echo "============ $bl ============"
    bash scripts_tmp/e1_run_eval.sh "$bl" 2>&1 | tail -25
    echo ""
done
