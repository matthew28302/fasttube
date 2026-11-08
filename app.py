"""
FastTube Studio - Production Entrypoint for Cloud / Panel Hosting (Pterodactyl, HIDENCLOUD, Docker)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent

# 1. Bootstrap runtime environment (sys.path, PATH, Deno JS runtime, permissions, cookies)
import app.runtime
app.runtime.setup_runtime_environment()

# 2. Ensure Git tracking is initialized if AUTO_UPDATE is enabled and .git is missing
def _ensure_git_tracking(target_dir: Path) -> None:
    git_dir = target_dir / ".git"
    if git_dir.exists():
        return
    git_repo = (
        os.getenv("GIT_REPO_ADDRESS")
        or os.getenv("GIT_ADDRESS")
        or "https://github.com/matthew28302/fasttube"
    ).strip()
    branch = (os.getenv("GIT_BRANCH") or "main").strip()
    import shutil, subprocess
    if not shutil.which("git"):
        return
    try:
        print(f"[FastTube] Initializing Git repository for {git_repo} (branch: {branch})...")
        subprocess.run(["git", "init"], cwd=target_dir, capture_output=True, check=True)
        # Avoid error if remote origin already exists
        subprocess.run(["git", "remote", "remove", "origin"], cwd=target_dir, capture_output=True)
        subprocess.run(["git", "remote", "add", "origin", git_repo], cwd=target_dir, capture_output=True, check=True)
        subprocess.run(["git", "fetch", "origin", branch, "--depth=1"], cwd=target_dir, capture_output=True, check=True)
        subprocess.run(["git", "branch", "-M", branch], cwd=target_dir, capture_output=True, check=True)
        subprocess.run(["git", "reset", f"origin/{branch}"], cwd=target_dir, capture_output=True, check=True)
        print("[FastTube] Git tracking initialized! Future server startups will auto-pull with git pull.")
    except Exception as e:
        print(f"[FastTube] Notice: Git tracking auto-init skipped: {e}")

_ensure_git_tracking(project_root)

# 6. Ensure project root is on sys.path
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

    import yt_dlp
    print("==================================================================")
    print(" [FastTube Studio] Server Starting...")
    print(f" - Host & Port : http://{host}:{port}")
    print(f" - Python      : {sys.version.split()[0]} | yt-dlp: {getattr(yt_dlp, 'version', None) and yt_dlp.version.__version__}")
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
