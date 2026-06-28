#!/usr/bin/env bash
# Reusable environment snapshot for ConDiag M0.
# Writes to /mnt/d/condiag-artifacts/environment/.
set -u

CB="${HOME}/condiag/ContextBench"
OUT="/mnt/d/condiag-artifacts/environment"
mkdir -p "$OUT"

# Activate venv if present so pip/python resolve correctly.
if [ -f "${CB}/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "${CB}/.venv/bin/activate"
fi

{
  echo "===== DATE ====="
  date -Iseconds
  echo
  echo "===== OS ====="
  cat /etc/os-release
  echo
  echo "===== KERNEL ====="
  uname -a
  echo
  echo "===== WSL (Windows side) ====="
  wsl.exe --version 2>/dev/null || echo "(wsl.exe not callable from inside WSL)"
  echo
  echo "===== PYTHON ====="
  python --version 2>&1
  command -v python
  echo
  echo "===== UV ====="
  uv --version 2>&1 || echo "(no uv)"
  echo
  echo "===== DOCKER ====="
  docker version 2>&1 || echo "(docker unavailable)"
  echo
  echo "===== CONTEXTBENCH ====="
  echo "path: ${CB}"
  echo "commit: $(git -C "${CB}" rev-parse HEAD 2>/dev/null)"
  echo "branch: $(git -C "${CB}" branch --show-current 2>/dev/null)"
  git -C "${CB}" log -1 --format="last commit: %ci  %an  %s" 2>/dev/null
  echo
  echo "===== PIP FREEZE ====="
  pip freeze 2>&1
} > "${OUT}/environment_snapshot.txt" 2>&1

pip freeze > "${OUT}/contextbench_pip_freeze.txt" 2>&1

echo "wrote ${OUT}/environment_snapshot.txt"
echo "wrote ${OUT}/contextbench_pip_freeze.txt"
