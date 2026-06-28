#!/usr/bin/env bash
# Pull Pilot50 Batch 3 (13 sympy images).
# sympy base layer different from Django but shared within sympy block.
# Per memory: TUN mode + Docker Desktop ProxyMode=manual + OverrideProxy=172.27.128.1:7890.
set -uo pipefail
export DOCKER_CONFIG=/dev/null

LOG=/mnt/d/condiag-artifacts/environment/pull_pilot50_batch3.log
mkdir -p "$(dirname "$LOG")"

IMAGES=(
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-19954:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-20428:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-20590:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-22714:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-23824:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-12096:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-12419:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-12489:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-13551:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-13615:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-13852:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-14976:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-15017:latest"
)

{
  echo "=== pull start: $(date) ==="
  echo "target: ${#IMAGES[@]} images (sympy)"
  echo "strategy: sequential, sympy base layer sharing"
  echo "proxy: manual mode OverrideProxy=172.27.128.1:7890 (Clash HK node)"
} > "$LOG"

ok=0
fail=0
failed_list=()
for img in "${IMAGES[@]}"; do
  echo "--- [$(date +%H:%M:%S)] pulling $img ---" >> "$LOG"
  if timeout 1200 docker pull "$img" >> "$LOG" 2>&1; then
    echo "  OK: $img  ($(date +%H:%M:%S))" >> "$LOG"
    ok=$((ok+1))
  else
    echo "  FAIL: $img  ($(date +%H:%M:%S))" >> "$LOG"
    fail=$((fail+1))
    failed_list+=("$img")
  fi
done

{
  echo
  echo "=== pull done: $(date) ==="
  echo "OK=$ok  FAIL=$fail"
  if [ ${#failed_list[@]} -gt 0 ]; then
    echo "FAILED:"
    printf '  %s\n' "${failed_list[@]}"
  fi
} >> "$LOG"
