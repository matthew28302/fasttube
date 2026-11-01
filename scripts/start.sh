#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -d .venv ]]; then
  echo "Virtual environment .venv not found. Running build.sh first..."
  bash scripts/build.sh
fi

# Activate venv (Linux/macOS: bin/activate, Windows git bash: Scripts/activate)
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
elif [[ -f .venv/Scripts/activate ]]; then
  source .venv/Scripts/activate
else
  echo "ERROR: venv activate script not found"
  exit 1
fi

# After activation, `python` resolves to the venv interpreter on both platforms.
exec python -m app.main