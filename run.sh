#!/usr/bin/env bash
set -e

# FastTube Studio - Replit & Cloud Auto-Runner
echo "=============================================="
echo "  FastTube Studio - Initializing Environment  "
echo "=============================================="

# 1. Create .venv if it doesn't exist
if [ ! -d ".venv" ]; then
  echo "[1/3] Creating virtual environment (.venv)..."
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
fi

# 2. Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
  source .venv/Scripts/activate
fi

# 3. Ensure dependencies are installed inside virtual environment
echo "[2/3] Checking & installing packages from requirements.txt..."
pip install -q --disable-pip-version-check -r requirements.txt

# 4. Launch application
echo "[3/3] Launching FastTube Studio on port ${PORT:-8080}..."
exec python main.py
