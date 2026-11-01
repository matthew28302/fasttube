from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("fasttube.security")

# ---------------------------------------------------------------------------
# Concurrency Semaphore: limit simultaneous downloads to keep VPS stable
# ---------------------------------------------------------------------------
MAX_CONCURRENT_DOWNLOADS = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
download_semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)


# ---------------------------------------------------------------------------
# Client IP Helper (aware of Cloudflare, Nginx, Traefik reverse proxies)
# ---------------------------------------------------------------------------
def get_client_ip(req: Request) -> str:
    cf_ip = req.headers.get("cf-connecting-ip")
    if cf_ip:
        return cf_ip.strip()
    x_forwarded_for = req.headers.get("x-forwarded-for")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    x_real_ip = req.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()
    if req.client and req.client.host:
        return req.client.host
    return "127.0.0.1"


# ---------------------------------------------------------------------------
# Anti-Scanner & Bad Bot List
# ---------------------------------------------------------------------------
_BLOCKED_SCANNER_UAS = (
    "sqlmap",
    "nikto",
    "dirbuster",
    "nmap",
    "masscan",
    "wpscan",
    "acunetix",
    "nessus",
    "gobuster",
    "zgrab",
    "havij",
    "shodan",
    "censys",
)

_BLOCKED_PATHS = (
    "/.env",
    "/.git",
    "/wp-admin",
    "/wp-login",
    "/phpmyadmin",
    "/actuator",
    "/swagger",
    "/.aws",
    "/config.json",
    "/xmlrpc.php",
    "/etc/passwd",
    "/setup.php",
    "/.ds_store",
    "/telescope",
)


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path_lower = request.url.path.lower()
        ua_lower = (request.headers.get("user-agent") or "").lower()

        # 1. Block known scanner probes
        for probe in _BLOCKED_PATHS:
            if path_lower.startswith(probe) or probe in path_lower:
                return Response(content="Forbidden", status_code=403, media_type="text/plain")

        # 2. Block known malicious scanner User-Agents
        for bad_ua in _BLOCKED_SCANNER_UAS:
            if bad_ua in ua_lower:
                return Response(content="Access Denied", status_code=403, media_type="text/plain")

        # 3. Process request
        response: Response = await call_next(request)

        # 4. Attach standard production security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Server"] = "FastTube-Engine"

        return response


# ---------------------------------------------------------------------------
# Sliding-Window In-Memory Rate Limiter
# ---------------------------------------------------------------------------
class RateLimiter:
    def __init__(self):
        self._history: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def check(self, request: Request, endpoint: str, max_requests: int = 30, window_s: int = 60) -> None:
        ip = get_client_ip(request)
        key = f"{endpoint}:{ip}"
        now = time.time()
        async with self._lock:
            records = self._history.get(key, [])
            # Evict timestamps outside the window
            cutoff = now - window_s
            records = [ts for ts in records if ts > cutoff]
            if len(records) >= max_requests:
                retry_after = int(window_s - (now - records[0])) if records else window_s
                raise HTTPException(
                    status_code=429,
                    detail=f"Quá nhiều yêu cầu. Vui lòng thử lại sau {max(1, retry_after)} giây.",
                    headers={"Retry-After": str(max(1, retry_after))},
                )
            records.append(now)
            self._history[key] = records


rate_limiter = RateLimiter()


# ---------------------------------------------------------------------------
# Real-Time Visitor & Live Active Counter
# ---------------------------------------------------------------------------
class VisitorTracker:
    def __init__(self, stats_file: Path):
        self._stats_file = stats_file
        self._active_sessions: dict[str, float] = {}  # session_hash -> last_seen_ts
        self._total_views: int = 0
        self._lock = asyncio.Lock()
        self._load_stats()

    def _load_stats(self) -> None:
        if self._stats_file.exists():
            try:
                data = json.loads(self._stats_file.read_text(encoding="utf-8"))
                self._total_views = int(data.get("views", 1280))
            except Exception:
                self._total_views = 1280
        else:
            self._total_views = 1280
            self._save_stats()

    def _save_stats(self) -> None:
        try:
            self._stats_file.parent.mkdir(parents=True, exist_ok=True)
            self._stats_file.write_text(
                json.dumps({"views": self._total_views}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    async def record_visit(self, req: Request) -> dict:
        ip = get_client_ip(req)
        ua = req.headers.get("user-agent") or ""
        session_id = hashlib.sha256(f"{ip}:{ua}".encode("utf-8")).hexdigest()[:16]
        now = time.time()

        async with self._lock:
            is_new_session = session_id not in self._active_sessions
            self._active_sessions[session_id] = now
            if is_new_session:
                self._total_views += 1
                if self._total_views % 5 == 0:
                    self._save_stats()

            # Evict sessions older than 5 minutes (300s)
            cutoff = now - 300
            self._active_sessions = {s: ts for s, ts in self._active_sessions.items() if ts > cutoff}
            online_count = max(1, len(self._active_sessions))

            return {"online": online_count, "views": self._total_views}

    async def get_stats(self) -> dict:
        now = time.time()
        cutoff = now - 300
        async with self._lock:
            self._active_sessions = {s: ts for s, ts in self._active_sessions.items() if ts > cutoff}
            online_count = max(1, len(self._active_sessions))
            return {"online": online_count, "views": self._total_views}


_STATS_PATH = Path(__file__).resolve().parent.parent / "cache" / "stats.json"
visitor_tracker = VisitorTracker(_STATS_PATH)
