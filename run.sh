#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "[ERROR] .venv not found."
    echo "        Please run ./setup.sh first to install dependencies."
    exit 1
fi

if [ ! -f ".venv/bin/python" ]; then
    echo "[ERROR] .venv/bin/python not found."
    echo "        The virtual environment may be corrupted."
    echo "        Delete .venv and re-run ./setup.sh."
    exit 1
fi

if [ ! -f "main.py" ]; then
    echo "[ERROR] main.py not found."
    echo "        Please check the installation directory."
    exit 1
fi

mkdir -p logs

echo "Starting VoiceInputTool..."
echo "Log: logs/run.log"
echo

.venv/bin/python main.py >> logs/run.log 2>&1

echo "Program exited."
echo "Program exited at $(date)" >> logs/run.log
