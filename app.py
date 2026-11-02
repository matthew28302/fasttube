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

# 2. Ensure .local/bin is prepended to PATH so binaries (yt-dlp, ffmpeg, deno) are immediately discoverable
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

# 4. Ensure a JavaScript runtime (deno/node) is available for YouTube n-challenge deciphering
def _ensure_js_runtime(target_dir: Path) -> None:
    import shutil
    if shutil.which("node") or shutil.which("deno") or shutil.which("bun") or shutil.which("quickjs"):
        return
    deno_bin = target_dir / "deno"
    if deno_bin.is_file():
        return
    if sys.platform.startswith("linux"):
        import platform
        arch = platform.machine().lower()
        deno_arch = "aarch64" if ("aarch64" in arch or "arm64" in arch) else "x86_64"
        url = f"https://github.com/denoland/deno/releases/latest/download/deno-{deno_arch}-unknown-linux-gnu.zip"
        try:
            import urllib.request, zipfile, io
            target_dir.mkdir(parents=True, exist_ok=True)
            print(f"[FastTube] Auto-installing standalone Deno JS runtime ({deno_arch}) for YouTube deciphering...")
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                z.extract("deno", target_dir)
            deno_bin.chmod(0o755)
            # Make sure it's in PATH immediately
            t_str = str(target_dir.resolve())
            if t_str not in os.environ.get("PATH", ""):
                os.environ["PATH"] = t_str + os.pathsep + os.environ.get("PATH", "")
            print("[FastTube] Deno JS runtime installed successfully into .local/bin/deno!")
        except Exception as e:
            print(f"[FastTube] Notice: could not auto-install Deno JS runtime: {e}")

_ensure_js_runtime(local_bin)

# 5. Ensure Git tracking is initialized if AUTO_UPDATE is enabled and .git is missing
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
