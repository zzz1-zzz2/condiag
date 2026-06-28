#!/usr/bin/env bash
# Verify all 50 Pilot50 instance images are present locally.
# Uses docker images list + grep to avoid inspect quirks under wsl bash.
set -uo pipefail
export DOCKER_CONFIG=/dev/null

SELECTED=/mnt/d/condiag-artifacts/condiag/v0/pilot50/pilot50_selected.txt

# Get all current local images into a temp file
LIST=$(mktemp)
docker images --format '{{.Repository}}:{{.Tag}}' > "$LIST"

present=0
missing=0
missing_list=()

while IFS= read -r inst_id; do
  [ -z "$inst_id" ] && continue
  repo="${inst_id%%__*}"
  name="${inst_id#*__}"
  img="swebench/sweb.eval.x86_64.${repo}_1776_${name}:latest"
  if grep -Fxq "$img" "$LIST"; then
    present=$((present+1))
  else
    missing=$((missing+1))
    missing_list+=("$img")
  fi
done < "$SELECTED"

rm -f "$LIST"

echo "Present: $present / 50"
echo "Missing: $missing"
if [ "${#missing_list[@]}" -gt 0 ]; then
  echo "Missing images:"
  printf '  %s\n' "${missing_list[@]}"
fi
