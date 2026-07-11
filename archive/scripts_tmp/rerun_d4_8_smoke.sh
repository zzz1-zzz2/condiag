#!/bin/bash
set -e
cd /home/swelite/condiag
rm -rf /mnt/d/condiag-artifacts/condiag/v0/d4_8_smoke/runs
INST=/mnt/d/condiag-artifacts/condiag/v0/d4_8_smoke/instances.txt
OUT=/mnt/d/condiag-artifacts/condiag/v0/d4_8_smoke/runs
MAN=/mnt/d/condiag-artifacts/condiag/v0/d4_8_smoke/manifest.csv

for bl in base_miniswe feedback_retry broad_expansion condiag_packet_only; do
    echo "=== $bl ==="
    python3 -m experiments.baseline_runner --agent miniswe --baseline "$bl" \
        --instances "$INST" --out "$OUT" --mode smoke --manifest "$MAN" 2>&1 | tail -3
done
