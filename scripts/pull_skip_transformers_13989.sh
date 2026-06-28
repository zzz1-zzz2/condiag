#!/usr/bin/env bash
# Pull remaining 8 pilot images, skipping transformers-13989 (suspected stuck on a big layer).
# Will retry transformers-13989 separately afterwards.
set -uo pipefail
export DOCKER_CONFIG=/dev/null

LOG=/mnt/d/condiag-artifacts/environment/pull_others.log
mkdir -p "$(dirname "$LOG")"

# Skip transformers-13989, pull others
IMAGES=(
  "ghcr.io/timesler/swe-polybench.eval.x86_64.huggingface__transformers-20136:latest"
  "ghcr.io/timesler/swe-polybench.eval.x86_64.huggingface__transformers-21768:latest"
  "ghcr.io/timesler/swe-polybench.eval.x86_64.huggingface__transformers-26839:latest"
  "jefzda/sweap-images:ansible.ansible-1b70260d5aa2f6c9782fd2b848e8d16566e50d85-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5"
  "jefzda/sweap-images:ansible.ansible-5640093f1ca63fd6af231cc8a7fb7d40e1907b8c-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5"
  "jefzda/sweap-images:ansible.ansible-622a493ae03bd5e5cf517d336fc426e9d12208c7-v906c969b551b346ef54a2c0b41e04f632b7b73c2"
  "jefzda/sweap-images:ansible.ansible-b5e0293645570f3f404ad1dbbe5f006956ada0df-v0f01c69f1e2528b935359cfe578530722bca2c59"
  "jefzda/sweap-images:internetarchive.openlibrary-bb152d23c004f3d68986877143bb0f83531fe401-ve8c8d62a2b60610a3c4631f5f23ed866bada9818"
  "jefzda/sweap-images:internetarchive.openlibrary-d109cc7e6e161170391f98f9a6fa1d02534c18e4-ve8c8d62a2b60610a3c4631f5f23ed866bada9818"
)

{
  echo "=== pull-others start: $(date) ==="
  echo "target: ${#IMAGES[@]} images (skip transformers-13989)"
} > "$LOG"

ok=0; fail=0; failed_list=()
for img in "${IMAGES[@]}"; do
  echo "--- [$(date +%H:%M:%S)] pulling $img ---" >> "$LOG"
  if timeout 600 docker pull "$img" >> "$LOG" 2>&1; then
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
  echo "=== pull-others done: $(date) ==="
  echo "OK=$ok  FAIL=$fail"
  [ ${#failed_list[@]} -gt 0 ] && { echo "FAILED:"; printf '  %s\n' "${failed_list[@]}"; }
} >> "$LOG"
