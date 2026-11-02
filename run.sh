#!/usr/bin/env bash
set -e

# FastTube Studio - Replit & Cloud Auto-Runner
# 1. Create .venv and install dependencies once (skip if already exists)
if [ ! -f ".venv/bin/activate" ] && [ ! -f ".venv/Scripts/activate" ]; then
  echo "[1/2] Creating virtual environment (.venv)..."
  python3 -m venv .venv 2>/dev/null || python -m venv .venv
  echo "[2/2] Installing dependencies..."
  if [ -f ".venv/bin/pip" ]; then
    .venv/bin/pip install -q --no-cache-dir -r requirements.txt
  elif [ -f ".venv/Scripts/pip" ]; then
    .venv/Scripts/pip install -q --no-cache-dir -r requirements.txt
  fi
fi

# 2. Activate virtual environment
if [ -f ".venv/bin/activate" ]; then
  source .venv/bin/activate
elif [ -f ".venv/Scripts/activate" ]; then
  source .venv/Scripts/activate
fi

# 3. Launch application immediately without delaying boot
echo "[*] Launching FastTube Studio..."
exec python main.py
