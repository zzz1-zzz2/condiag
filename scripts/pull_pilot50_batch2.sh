#!/usr/bin/env bash
# Pull Pilot50 Batch 2 (17 Django images) — 1 EC remaining + 11 other + 5 NOOP.
# Layer sharing maximised: all Django repo, base+env layers already cached locally.
# Per memory: TUN mode + Docker Desktop ProxyMode=manual + OverrideProxy=172.27.128.1:7890.
set -uo pipefail
export DOCKER_CONFIG=/dev/null   # bypass credsStore=desktop.exe bug (坑 A)

LOG=/mnt/d/condiag-artifacts/environment/pull_pilot50_batch2.log
mkdir -p "$(dirname "$LOG")"

IMAGES=(
  # 1 Django EC remaining
  "swebench/sweb.eval.x86_64.django_1776_django-14349:latest"
  # 11 Django other
  "swebench/sweb.eval.x86_64.django_1776_django-13513:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-15104:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-15973:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-10880:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-11603:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-12663:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-13012:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-14140:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-14351:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-14787:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-14792:latest"
  # 5 NOOP control (Django repo)
  "swebench/sweb.eval.x86_64.django_1776_django-11163:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-11433:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-11555:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-12193:latest"
  "swebench/sweb.eval.x86_64.django_1776_django-12262:latest"
)

{
  echo "=== pull start: $(date) ==="
  echo "target: ${#IMAGES[@]} images (all Django — 1 EC + 11 other + 5 NOOP)"
  echo "strategy: sequential, layer sharing with existing 14 Django local"
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
