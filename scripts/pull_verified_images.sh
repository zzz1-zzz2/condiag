#!/usr/bin/env bash
# Pull 6 ContextBench Verified-source instance images (naming rule verified).
# Run in background; logs to /mnt/d/condiag-artifacts/environment/pull_verified.log
set -uo pipefail
export DOCKER_CONFIG=/dev/null   # bypass credsStore=desktop.exe bug

LOG=/mnt/d/condiag-artifacts/environment/pull_verified.log
mkdir -p "$(dirname "$LOG")"

IMAGES=(
  "swebench/sweb.eval.x86_64.astropy_1776_astropy-13398:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-11400:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-13195:latest"
  "swebench/sweb.eval.x86_64.sphinx-doc_1776_sphinx-doc-9461:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-13877:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-16597:latest"
)

{
  echo "=== pull start: $(date) ==="
  echo "target: ${#IMAGES[@]} images"
} >> "$LOG"

ok=0
fail=0
failed_list=()
for img in "${IMAGES[@]}"; do
  echo "--- [$(date +%H:%M:%S)] pulling $img ---" >> "$LOG"
  if docker pull "$img" >> "$LOG" 2>&1; then
    echo "  OK: $img" >> "$LOG"
    ok=$((ok+1))
  else
    echo "  FAIL: $img" >> "$LOG"
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
