#!/usr/bin/env bash
# Pull Pilot50 Batch 1 (10 images) — 8 Django + 2 sympy.
# Strategy: sequential pull (same-repo images share layers, so 2nd Django
# onwards only downloads the instance layer — much smaller).
# Per memory: TUN mode + system proxy OFF + DOCKER_CONFIG=/dev/null.
set -uo pipefail
export DOCKER_CONFIG=/dev/null   # bypass credsStore=desktop.exe bug (坑 A)

LOG=/mnt/d/condiag-artifacts/environment/pull_pilot50_batch1.log
mkdir -p "$(dirname "$LOG")"

# Batch 1 (from pilot50_batch1.txt — order: Django EC strong first, then Django other, then sympy)
# Order matters: Django block first to maximise layer sharing within repo.
IMAGES=(
  # 5 Django EC (strong RELOCALIZE candidates, models.E### etc.)
  "swebench/sweb.eval.x86_64.django_1776_django-12858:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-13925:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-11820:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-13023:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-13109:latest"
  # 3 Django other (config/database/backend)
  "swebench/sweb.eval.x86_64.django_1776_django-13449:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-15863:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-16454:latest"
  # 2 sympy
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-13372:latest"
  "swebench/sweb.eval.x86_64.sympy_1776_sympy-17318:latest"
)

{
  echo "=== pull start: $(date) ==="
  echo "target: ${#IMAGES[@]} images (8 Django + 2 sympy)"
  echo "strategy: sequential, Django block first for layer sharing"
  echo "proxy: TUN mode (Clash system proxy OFF, ProxyEnable=0)"
} > "$LOG"

ok=0
fail=0
failed_list=()
for img in "${IMAGES[@]}"; do
  echo "--- [$(date +%H:%M:%S)] pulling $img ---" >> "$LOG"
  # Per-image timeout 20 min (TUN slow case ~5 min/image, this is generous)
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
