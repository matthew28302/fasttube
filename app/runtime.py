"""
FastTube Studio - Runtime Environment Bootstrap & Provisioning
Ensures PATH, dependencies, JS runtime (Node/Deno), and file permissions are ready
across all execution contexts (uvicorn, python app.py, systemd, Pterodactyl container).
"""
from __future__ import annotations

import io
import logging
import os
import platform
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_INITIALIZED = False


def get_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def setup_runtime_environment() -> None:
    """
    Called at the earliest possible moment in FastTube execution.
    Idempotent and safe to call multiple times.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return
    _INITIALIZED = True

    root = get_project_root()

    # 1. sys.path for local package prefix (.local/lib/python*/site-packages)
    local_lib = root / ".local" / "lib"
    if local_lib.exists():
        for sp in local_lib.glob("python*/site-packages"):
            if sp.is_dir() and str(sp) not in sys.path:
                sys.path.insert(0, str(sp))

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    # 2. PATH prepends (.local/bin)
    local_bin = root / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    local_bin_str = str(local_bin.resolve())
    curr_path = os.environ.get("PATH", "")
    if local_bin_str not in curr_path:
        os.environ["PATH"] = local_bin_str + os.pathsep + curr_path

    # 3. Ensure executable permissions on .local/bin binaries (yt-dlp, deno, ffmpeg)
    if os.name != "nt":
        for fname in ["yt-dlp", "deno", "ffmpeg"]:
            fpath = local_bin / fname
            if fpath.is_file():
                try:
                    os.chmod(fpath, os.stat(fpath).st_mode | 0o755)
                except Exception:
                    pass

    # 4. Ensure a working JavaScript runtime exists for YouTube challenge deciphering
    ensure_js_runtime(local_bin)

    # 5. Ensure YouTube cookies exist and are backed up
    _ensure_cookie_persistence(root)


def ensure_js_runtime(target_dir: Path) -> Optional[str]:
    """
    Verify if a JavaScript runtime (node, deno, bun, quickjs) is available.
    If not, automatically download and install standalone Deno binary for YouTube BotGuard bypass.
    """
    # Check current system PATH
    for cand in ["node", "deno", "bun", "qjs", "quickjs"]:
        found = shutil.which(cand)
        if found:
            return cand

    deno_name = "deno.exe" if os.name == "nt" else "deno"
    deno_bin = target_dir / deno_name

    # If already downloaded in target_dir, ensure executable and in PATH
    if deno_bin.is_file():
        if os.name != "nt":
            try:
                os.chmod(deno_bin, os.stat(deno_bin).st_mode | 0o755)
            except Exception:
                pass
        t_str = str(target_dir.resolve())
        if t_str not in os.environ.get("PATH", ""):
            os.environ["PATH"] = t_str + os.pathsep + os.environ.get("PATH", "")
        return str(deno_bin.resolve())

    # Auto-provision Deno on Linux
    if sys.platform.startswith("linux"):
        machine = platform.machine().lower()
        deno_arch = "aarch64" if ("aarch64" in machine or "arm64" in machine) else "x86_64"
        download_urls = [
            f"https://dl.deno.land/release/v2.1.9/deno-{deno_arch}-unknown-linux-gnu.zip",
            f"https://github.com/denoland/deno/releases/download/v2.1.9/deno-{deno_arch}-unknown-linux-gnu.zip",
        ]
        target_dir.mkdir(parents=True, exist_ok=True)
        for url in download_urls:
            try:
                print(f"[FastTube] Auto-installing standalone Deno JS runtime ({deno_arch}) from {url}...")
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    z.extract("deno", target_dir)
                deno_bin.chmod(0o755)
                t_str = str(target_dir.resolve())
                if t_str not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = t_str + os.pathsep + os.environ.get("PATH", "")
                print(f"[FastTube] Deno JS runtime installed successfully into {deno_bin}!")
                return str(deno_bin.resolve())
            except Exception as e:
                print(f"[FastTube] Notice: could not auto-install Deno from {url}: {e}")

    return None


def _ensure_cookie_persistence(root: Path) -> None:
    """Ensure cookie file in cookies/ is safely backed up and restored across updates."""
    cookies_dir = root / "cookies"
    cookies_dir.mkdir(parents=True, exist_ok=True)

    target_file = cookies_dir / "www.youtube.com_cookies.txt"
    backup_file = cookies_dir / "user_youtube_cookies_backup.txt"
    root_cookie = root / "cookies.txt"

    # 1. Restore from backup if primary is missing
    if not target_file.is_file() and backup_file.is_file() and backup_file.stat().st_size > 50:
        try:
            shutil.copy2(backup_file, target_file)
        except Exception:
            pass

    # 2. Or copy root cookies.txt if present
    if not target_file.is_file() and root_cookie.is_file() and root_cookie.stat().st_size > 50:
        try:
            shutil.copy2(root_cookie, target_file)
        except Exception:
            pass

    # 3. Always refresh backup from primary if primary is valid
    if target_file.is_file() and target_file.stat().st_size > 50:
        try:
            shutil.copy2(target_file, backup_file)
        except Exception:
            pass
