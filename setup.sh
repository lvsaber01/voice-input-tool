#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "=================================================="
echo "  VoiceInputTool - Environment Setup"
echo "=================================================="
echo

mkdir -p logs

# Step 1: Check uv
echo "[1/4] Checking uv..."
if ! command -v uv &>/dev/null; then
    echo "       Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "[ERROR] uv install failed: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
else
    echo "       uv OK"
fi

# Step 2: Create venv
echo "[2/4] Creating virtual environment (Python 3.11)..."
if [ -d ".venv" ]; then
    echo "       venv exists, reusing"
else
    uv venv --python 3.11
    echo "       venv created"
fi

# Step 3: Install dependencies
echo "[3/4] Installing dependencies..."
uv pip install -r requirements/base.txt -q 2>&1 | tee -a logs/setup.log
uv pip install -r requirements/macos.txt -q 2>&1 | tee -a logs/setup.log || true

# Optional: FunASR
if [ -f "config.yaml" ] && grep -qi "funasr" config.yaml; then
    if ! .venv/bin/python -c "import funasr" 2>/dev/null; then
        echo "       Installing FunASR (optional engine)..."
        uv pip install funasr modelscope 2>&1 | tee -a logs/setup.log || echo "       [WARN] FunASR install failed, will fallback to faster-whisper"
    fi
fi

# Step 4: Verify
echo "[4/4] Verifying core dependencies..."
VERIFY_FAIL=0
for mod in yaml faster_whisper sounddevice scipy pypinyin; do
    if .venv/bin/python -c "import $mod" 2>/dev/null; then
        echo "   [OK] $mod"
    else
        echo "   [FAIL] $mod"
        VERIFY_FAIL=1
    fi
done

echo "Setup complete at $(date)" >> logs/setup.log

if [ "$VERIFY_FAIL" -eq 1 ]; then
    echo
    echo "[WARN] Some verifications failed! Check logs/setup.log"
    exit 1
fi

echo
echo "=================================================="
echo "  Setup complete!"
echo "  Next: ./run.sh to start"
echo "  Log:  logs/setup.log"
echo "=================================================="
