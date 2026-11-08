#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Detect a working Python interpreter.
# On Linux/macOS it is usually `python3`; on Windows (git bash) `python3` may be a
# broken Microsoft-Store stub, so we actually run --version to verify.
find_python() {
  # Prefer interpreters with full wheel support for all dependencies (3.10-3.13).
  for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then
      ver="$("$cand" --version 2>&1)"
      if echo "$ver" | grep -qE 'Python 3\.(10|11|12|13)\.'; then
        echo "$cand"; return
      fi
    fi
  done
  # Windows common install locations (git bash).
  for d in /c/Python313 /c/Python312 /c/Python311 /c/Python310; do
    if [[ -x "$d/python.exe" ]]; then
      ver="$("$d/python.exe" --version 2>&1)"
      if echo "$ver" | grep -qE 'Python 3\.(10|11|12|13)\.'; then
        echo "$d/python.exe"; return
      fi
    fi
  done
  # Fallback: any working interpreter.
  for cand in python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" --version >/dev/null 2>&1; then
      echo "$cand"; return
    fi
  done
}

PY_CMD="$(find_python)"
if [[ -z "$PY_CMD" ]]; then
  echo "ERROR: no working python found"
  exit 1
fi
if [[ -z "$PY_CMD" ]]; then
  echo "ERROR: no working python/python3 found"
  exit 1
fi

echo "==> Project: ${ROOT_DIR}"

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_deps() {
  if [[ "${SKIP_SYSTEM_DEPS:-0}" == "1" ]]; then
    echo "==> SKIP_SYSTEM_DEPS=1, skipping system packages"
    return
  fi

  if need_cmd ffmpeg && (need_cmd python3 || need_cmd python); then
    echo "==> Required system tools (ffmpeg, python) already present. Skipping system package installation."
    return
  fi

  local sudo_cmd=""
  if need_cmd sudo; then
    sudo_cmd="sudo"
  fi

  if need_cmd apt-get; then
    echo "==> Installing system deps via apt-get"
    $sudo_cmd apt-get update -y
    $sudo_cmd apt-get install -y python3-venv ffmpeg build-essential python3-dev
    return
  fi

  if need_cmd dnf; then
    echo "==> Installing system deps via dnf"
    $sudo_cmd dnf install -y python3-virtualenv ffmpeg gcc python3-devel
    return
  fi

  if need_cmd yum; then
    echo "==> Installing system deps via yum"
    $sudo_cmd yum install -y python3-virtualenv ffmpeg gcc python3-devel
    return
  fi

  if need_cmd pacman; then
    echo "==> Installing system deps via pacman"
    $sudo_cmd pacman -Sy --noconfirm python ffmpeg base-devel
    return
  fi

  echo "==> No supported package manager found. Skipping system deps."
}

install_deps

cd "$ROOT_DIR"

if [[ ! -d .venv ]]; then
  echo "==> Creating venv"
  $PY_CMD -m venv .venv
fi

echo "==> Activating venv"
# shellcheck disable=SC1091
if [[ -f .venv/bin/activate ]]; then
  source .venv/bin/activate
elif [[ -f .venv/Scripts/activate ]]; then
  source .venv/Scripts/activate
else
  echo "ERROR: venv activate script not found"
  exit 1
fi

echo "==> Upgrading pip"
python -m pip install --upgrade pip

echo "==> Installing Python packages"
pip install -r requirements.txt

echo "==> Ensuring runtime folders"
mkdir -p music cache cookies logs .local/bin

if ! command -v node >/dev/null 2>&1 && ! command -v deno >/dev/null 2>&1 && ! [[ -x "${ROOT_DIR}/.local/bin/deno" ]]; then
  echo "==> Provisioning Deno standalone JS engine for YouTube deciphering..."
  DENO_ARCH="x86_64"
  if [[ "$(uname -m)" == *"aarch64"* ]] || [[ "$(uname -m)" == *"arm64"* ]]; then
    DENO_ARCH="aarch64"
  fi
  DENO_ZIP="${ROOT_DIR}/cache/deno.zip"
  curl -fsSL "https://dl.deno.land/release/v2.1.9/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o "$DENO_ZIP" || \
  curl -fsSL "https://github.com/denoland/deno/releases/download/v2.1.9/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o "$DENO_ZIP" || true
  if [[ -f "$DENO_ZIP" ]]; then
    python -m zipfile -e "$DENO_ZIP" "${ROOT_DIR}/.local/bin" || true
    chmod +x "${ROOT_DIR}/.local/bin/deno" 2>/dev/null || true
    rm -f "$DENO_ZIP"
    echo "==> Deno installed into .local/bin/deno successfully!"
  fi
fi

if [[ ! -f .env ]]; then
  if [[ -f .env.example ]]; then
    echo "==> Copying .env.example to .env (paths are relative to project root, no editing needed)"
    cp .env.example .env
  else
    echo "==> Creating .env (placeholder, paths relative to project root)"
    cat > .env << 'EOF'
MUSIC_DIR=./music
COOKIES_DIR=./cookies
LOG_FILE=./logs/app.log
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
EOF
  fi
fi

echo "==> Done. You can run:"
echo "    source .venv/bin/activate   (Linux/macOS)"
echo "    source .venv/Scripts/activate (Windows git bash)"
echo "    python -m app.main"
echo "    # or: uvicorn app.main:app --host 0.0.0.0 --port 8000"