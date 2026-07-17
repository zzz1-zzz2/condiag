#!/usr/bin/env bash
# ConDiag v4 — New machine setup script
# Usage: bash scripts/setup_new_machine.sh
set -euo pipefail

REQUIREMENTS="requirements.txt"
ENV_FILE="$HOME/.config/mini-swe-agent/.env"
PILOT_SCRIPT="scripts/pull_pilot_images.sh"

echo "================================================"
echo "  ConDiag v4 — Environment Setup"
echo "================================================"
echo ""

# 1. Python dependencies
echo "[1/5] Installing Python dependencies..."
pip install -r "$REQUIREMENTS" -q
echo "  ✅ Done"

# 2. Check Python version
echo "[2/5] Checking Python version..."
PY_VER=$(python3 --version 2>&1)
echo "  $PY_VER"
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")
if [ "$PY_MAJOR" -lt 3 ] || [ "$PY_MAJOR" -eq 3 -a "$PY_MINOR" -lt 10 ]; then
    echo "  ❌ Need Python >= 3.10"
    exit 1
fi
echo "  ✅ OK"

# 3. Docker check
echo "[3/5] Checking Docker..."
if command -v docker &>/dev/null; then
    docker info --format '{{.ServerVersion}}' &>/dev/null && echo "  ✅ Docker $(docker info --format '{{.ServerVersion}}')" || {
        echo "  ⚠️  Docker daemon not running (start Docker Desktop)"
    }
else
    echo "  ❌ Docker not found. Install Docker Desktop first."
    echo "     https://docs.docker.com/desktop/"
fi

# 4. Setup API key
echo "[4/5] API key..."
mkdir -p "$(dirname "$ENV_FILE")"
if [ -f "$ENV_FILE" ]; then
    if grep -q "DEEPSEEK_API_KEY" "$ENV_FILE" 2>/dev/null; then
        echo "  ✅ DEEPSEEK_API_KEY found in $ENV_FILE"
    else
        echo "  ⚠️  $ENV_FILE exists but missing DEEPSEEK_API_KEY"
        echo "     Add: DEEPSEEK_API_KEY=sk-..."
    fi
else
    echo "  ⚠️  No .env file found at $ENV_FILE"
    echo "     Creating template..."
    cat > "$ENV_FILE" << 'EOF'
# ConDiag v4 — LLM API Keys
# Uncomment and fill in your provider's key
DEEPSEEK_API_KEY=your_key_here
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-...
EOF
    chmod 600 "$ENV_FILE"
    echo "     Template created. Edit $ENV_FILE with your actual key."
fi

# 5. Data check
echo "[5/5] Checking data files..."
CB_PARQUET="ContextBench/data/full.parquet"
MANIFEST="../condiag-artifacts/condiag/manifests/instances_v2.jsonl"

if [ -f "$CB_PARQUET" ]; then
    echo "  ✅ ContextBench parquet ($(du -h "$CB_PARQUET" | cut -f1))"
else
    echo "  ⚠️  $CB_PARQUET not found (some registries won't work)"
fi

if [ -f "$MANIFEST" ]; then
    echo "  ✅ Manifest ($(wc -l < "$MANIFEST") instances)"
else
    echo "  ⚠️  $MANIFEST not found"
fi

echo ""
echo "================================================"
echo "  Setup complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo "  1. Edit API key:  $ENV_FILE"
echo "  2. Pull images:   bash $PILOT_SCRIPT"
echo "  3. Dry-run test:  cd /home/swelite/condiag && HF_DATASETS_OFFLINE=1 python3 -m experiments.v2c_entry --instance sympy__sympy-20428 --dry-run"
echo "  4. Full run:      HF_DATASETS_OFFLINE=1 python3 -m experiments.v2c_entry --instance sympy__sympy-20428"
echo ""
