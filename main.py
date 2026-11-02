"""
FastTube Studio - Root Entrypoint
Compatible with Replit, Render, Heroku, Docker and local development.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import uvicorn
from app.config import load_settings


def main():
    settings = load_settings()
    
    is_replit = bool(
        os.environ.get("REPL_ID")
        or os.environ.get("REPLIT_ENVIRONMENT")
        or os.environ.get("REPL_SLUG")
        or os.environ.get("REPL_OWNER")
        or os.environ.get("REPLIT_DEPLOYMENT")
    )

    # Priority: PORT (Replit/Cloud) > 8080 (if Replit) > RUN_PORT (.env) > settings.run_port
    if os.environ.get("PORT"):
        port = int(os.environ["PORT"])
    elif is_replit:
        port = 8080
    elif os.environ.get("RUN_PORT"):
        port = int(os.environ["RUN_PORT"])
    else:
        port = settings.run_port or 9000

    host = os.environ.get("RUN_HOST") or "0.0.0.0"
    
    # In low-memory containers (Replit free tier = 512MB RAM), keep workers=1 to prevent OOM kills
    workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    
    # Select safe loop
    try:
        import uvloop  # noqa: F401
        _loop = "uvloop"
    except Exception:
        _loop = "asyncio"

    try:
        import httptools  # noqa: F401
        _http = "httptools"
    except Exception:
        _http = "auto"

    print(f"[*] Starting FastTube on http://{host}:{port} (workers={workers})...")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        loop=_loop,
        http=_http,
        workers=workers,
        reload=False,
    )


if __name__ == "__main__":
    main()
