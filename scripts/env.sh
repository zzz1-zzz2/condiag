#!/usr/bin/env bash
# ConDiag project environment. Source this before any ContextBench / mini-SWE-Agent run.
# Do NOT execute directly. Contains no secrets — those live in ~/.config/mini-swe-agent/.env.

# HF mirror for users in regions where direct huggingface.co is blocked.
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"

# Make HF Hub talkative when debugging.
export HF_HUB_VERBOSITY="${HF_HUB_VERBOSITY:-info}"

# === Caches MUST live on D drive, not on the WSL2 vhdx in C:\ ===
# See feedback_cache_disk_location.md for the reason.
export CONDIAG_CACHE_ROOT="/mnt/d/condiag-artifacts/cache"
mkdir -p "${CONDIAG_CACHE_ROOT}"/{hf,llama_index,sentence_transformers,torch,uv,pip}

export HF_HOME="${HF_HOME:-${CONDIAG_CACHE_ROOT}/hf}"
export LLAMA_INDEX_CACHE_DIR="${LLAMA_INDEX_CACHE_DIR:-${CONDIAG_CACHE_ROOT}/llama_index}"
export SENTENCE_TRANSFORMERS_HOME="${SENTENCE_TRANSFORMERS_HOME:-${CONDIAG_CACHE_ROOT}/sentence_transformers}"
export TORCH_HOME="${TORCH_HOME:-${CONDIAG_CACHE_ROOT}/torch}"
export UV_CACHE_DIR="${UV_CACHE_DIR:-${CONDIAG_CACHE_ROOT}/uv}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-${CONDIAG_CACHE_ROOT}/pip}"

# Python venv.
CB_ROOT="${HOME}/condiag/ContextBench"
if [ -f "${CB_ROOT}/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "${CB_ROOT}/.venv/bin/activate"
fi

# Repos and artifacts.
export CONTEXTBENCH_ROOT="${CB_ROOT}"
export CONDIAG_ARTIFACTS="/mnt/d/condiag-artifacts"

# === LLM provider ===
# Primary: DeepSeek V4. Both OpenAI SDK (Agentless) and Anthropic SDK (Claude
# Code, mini-SWE-Agent) talk to DeepSeek's compatible endpoints.
# ConDiag: switched from ZAI anthropic proxy on 2026-06-26. ZAI's /api/anthropic
# is Anthropic-protocol-only, which broke Agentless's openai backend
# (ret.choices was None). DeepSeek exposes both protocols natively.
if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -f "${HOME}/.config/mini-swe-agent/.env" ]; then
  _ds_key=$(grep -E "^DEEPSEEK_API_KEY=" "${HOME}/.config/mini-swe-agent/.env" | head -1 | cut -d= -f2-)
  if [ -n "${_ds_key}" ]; then
    export DEEPSEEK_API_KEY="${_ds_key}"
  fi
  unset _ds_key
fi

if [ -n "${DEEPSEEK_API_KEY:-}" ]; then
  # OpenAI SDK clients (Agentless, litellm openai provider)
  export OPENAI_API_KEY="${DEEPSEEK_API_KEY}"
  export OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://api.deepseek.com}"
  export OPENAI_MODEL="${OPENAI_MODEL:-deepseek-v4-pro}"
  # Anthropic SDK clients (Claude Code itself, litellm anthropic provider)
  export ANTHROPIC_API_KEY="${DEEPSEEK_API_KEY}"
  export ANTHROPIC_API_BASE="${ANTHROPIC_API_BASE:-https://api.deepseek.com/anthropic}"
fi

# mini-swe-agent's cost tracking doesn't know how to price deepseek-v4-pro.
# Tell it to ignore cost-tracking errors instead of crashing.
export MSWEA_COST_TRACKING="${MSWEA_COST_TRACKING:-ignore_errors}"

# === Proxy: WSL2 → Windows host → upstream ===
# ConDiag: Agentless runs inside the Ubuntu distro. Clash TUN mode only routes
# Windows-side traffic transparently; WSL2 NAT outbound (e.g. `git clone
# https://github.com/...`) gets GFW-blocked. Point HTTP clients at the Windows
# host's Clash HTTP proxy instead. See project_condiag_pilot_ready_result.md
# (Failure A-2) for the symptom.
#
# 2026-06-27晚: api.deepseek.com 在国内有 CDN 直连（DNS 走 223.5.5.5 即可），
# 但 Clash HTTP 代理对长流式 reasoning 响应有 ~300s 静默断流（close_wait 卡死）。
# 所以 DeepSeek 流量必须绕过 Clash 直连。
export HTTP_PROXY="${HTTP_PROXY:-http://172.27.128.1:7890}"
export HTTPS_PROXY="${HTTPS_PROXY:-http://172.27.128.1:7890}"
export http_proxy="${http_proxy:-http://172.27.128.1:7890}"
export https_proxy="${https_proxy:-http://172.27.128.1:7890}"
export no_proxy="${no_proxy:-localhost,127.0.0.1,::1,172.27.0.0/16,api.deepseek.com,deepseek.com}"
export NO_PROXY="${NO_PROXY:-localhost,127.0.0.1,::1,172.27.0.0/16,api.deepseek.com,deepseek.com}"

# Git does NOT read HTTP_PROXY/HTTPS_PROXY env vars. Configure it globally so
# that Agentless's `git clone` (run from arbitrary cwd via subprocess) inherits
# the proxy. Idempotent — skips if already set, so manual overrides survive.
if command -v git >/dev/null 2>&1; then
  if [ -z "$(git config --global --get http.proxy 2>/dev/null)" ]; then
    git config --global http.proxy "http://172.27.128.1:7890"
  fi
  if [ -z "$(git config --global --get https.proxy 2>/dev/null)" ]; then
    git config --global https.proxy "http://172.27.128.1:7890"
  fi
fi
