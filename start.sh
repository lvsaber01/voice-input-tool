#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "=================================================="
echo "  语音输入工具 - VoiceInputTool"
echo "=================================================="
echo

# 检查 uv
if ! command -v uv &>/dev/null; then
    echo "[1/4] 安装 uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if ! command -v uv &>/dev/null; then
        echo "[错误] uv 安装失败，请手动安装: https://docs.astral.sh/uv/getting-started/installation/"
        exit 1
    fi
else
    echo "[1/4] uv 已就绪"
fi

# 创建 venv
if [ ! -d ".venv" ]; then
    echo "[2/4] 创建虚拟环境 (Python 3.11)..."
    uv venv --python 3.11
else
    echo "[2/4] 虚拟环境已存在"
fi

# 安装依赖
echo "[3/4] 检查依赖..."
uv pip install -r requirements.txt -q
uv pip install -r requirements_macos.txt -q 2>/dev/null || true

# FunASR 按需安装
if [ -f "config.yaml" ] && grep -qi "funasr" config.yaml; then
    if ! .venv/bin/python -c "import funasr" 2>/dev/null; then
        echo "[提示] 检测到 FunASR 引擎配置，正在安装..."
        uv pip install funasr modelscope || echo "[警告] FunASR 安装失败，将回退到 faster-whisper"
    fi
fi

# 启动
echo "[4/4] 启动语音输入工具..."
echo
.venv/bin/python main.py
