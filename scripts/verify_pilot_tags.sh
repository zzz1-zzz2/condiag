#!/usr/bin/env bash
# Verify pilot image tags exist (manifest inspect — no layer download).
# Use this BEFORE pull to catch tag typos in seconds, not minutes.
set -uo pipefail
export DOCKER_CONFIG=/dev/null

TAGS=(
  # Verified (5)
  "docker.io/swebench/sweb.eval.x86_64.astropy_1776_astropy-13398:latest"
  "docker.io/swebench/sweb.eval.x86_64.django_1776_django-11400:latest"
  "docker.io/swebench/sweb.eval.x86_64.django_1776_django-13195:latest"
  "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-13877:latest"
  "docker.io/swebench/sweb.eval.x86_64.sympy_1776_sympy-16597:latest"
  # Poly (4)
  "ghcr.io/timesler/swe-polybench.eval.x86_64.huggingface__transformers-13989:latest"
  "ghcr.io/timesler/swe-polybench.eval.x86_64.huggingface__transformers-20136:latest"
  "ghcr.io/timesler/swe-polybench.eval.x86_64.huggingface__transformers-21768:latest"
  "ghcr.io/timesler/swe-polybench.eval.x86_64.huggingface__transformers-26839:latest"
  # Pro (6) — correct tags per resolve_docker_image()
  "jefzda/sweap-images:ansible.ansible-ansible__ansible-1b70260d5aa2f6c9782fd2b848e8d16566e50d85-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5"
  "jefzda/sweap-images:ansible.ansible-ansible__ansible-5640093f1ca63fd6af231cc8a7fb7d40e1907b8c-vba6da65a0f3baefda7a058ebbd0a8dcafb8512f5"
  "jefzda/sweap-images:ansible.ansible-ansible__ansible-622a493ae03bd5e5cf517d336fc426e9d12208c7-v906c969b551b346ef54a2c0b41e04f632b7b73c2"
  "jefzda/sweap-images:ansible.ansible-ansible__ansible-b5e0293645570f3f404ad1dbbe5f006956ada0df-v0f01c69f1e2528b935359cfe578530722bca2c59"
  "jefzda/sweap-images:internetarchive.openlibrary-internetarchive__openlibrary-bb152d23c004f3d68986877143bb0f83531fe401-ve8c8d62a2b60610a3c4631f5f23ed"
  "jefzda/sweap-images:internetarchive.openlibrary-internetarchive__openlibrary-d109cc7e6e161170391f98f9a6fa1d02534c18e4-ve8c8d62a2b60610a3c4631f5f23ed"
)

ok=0; missing=0; missing_list=()
for img in "${TAGS[@]}"; do
  if timeout 30 docker manifest inspect "$img" >/dev/null 2>&1; then
    echo "  OK    $img"
    ok=$((ok+1))
  else
    echo "  MISS  $img"
    missing=$((missing+1))
    missing_list+=("$img")
  fi
done

echo
echo "=== summary: $ok OK, $missing missing ==="
[ ${#missing_list[@]} -gt 0 ] && printf '  - %s\n' "${missing_list[@]}"
