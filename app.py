"""
FastTube Studio - Production Entrypoint for Cloud / Panel Hosting (Pterodactyl, HIDENCLOUD, Docker)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent

# 1. Ensure local package prefix (.local/lib/python*/site-packages) is on sys.path
# Pterodactyl/HIDENCLOUD runs: pip install -U --prefix .local -r requirements.txt
local_lib = project_root / ".local" / "lib"
if local_lib.exists():
    for sp in local_lib.glob("python*/site-packages"):
        if sp.is_dir() and str(sp) not in sys.path:
            sys.path.insert(0, str(sp))

# 2. Ensure .local/bin is prepended to PATH so binaries (yt-dlp, ffmpeg) are immediately discoverable
local_bin = project_root / ".local" / "bin"
if local_bin.exists():
    local_bin_str = str(local_bin.resolve())
    curr_path = os.environ.get("PATH", "")
    if local_bin_str not in curr_path:
        os.environ["PATH"] = local_bin_str + os.pathsep + curr_path

# 3. Ensure permissions on .local/bin/yt-dlp if present
ytdlp_bin = local_bin / "yt-dlp"
if ytdlp_bin.is_file() and os.name != "nt":
    try:
        os.chmod(ytdlp_bin, os.stat(ytdlp_bin).st_mode | 0o755)
    except Exception:
        pass

# 4. Ensure project root is on sys.path
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.config import load_settings
import uvicorn


def main():
    settings = load_settings()

    # HIDENCLOUD / Pterodactyl injects SERVER_PORT; other cloud hosts inject PORT
    env_port = os.getenv("SERVER_PORT") or os.getenv("PORT") or os.getenv("RUN_PORT")
    try:
        port = int(env_port) if env_port else settings.run_port
    except ValueError:
        port = settings.run_port

    host = os.getenv("HOST") or os.getenv("RUN_HOST") or "0.0.0.0"

    try:
        import uvloop
        _loop = "uvloop"
    except Exception:
        _loop = "asyncio"

    try:
        import httptools
        _http = "httptools"
    except Exception:
        _http = "auto"

    workers = 1

    print("==================================================================")
    print(" [FastTube Studio] Server Starting...")
    print(f" - Host & Port : http://{host}:{port}")
    print(f" - Python      : {sys.version.split()[0]}")
    print(f" - Event Loop  : {_loop} | HTTP Parser: {_http}")
    print("==================================================================")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        loop=_loop,
        http=_http,
        workers=workers,
        reload=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
