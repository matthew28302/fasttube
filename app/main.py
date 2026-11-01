from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import Optional

import psutil
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

# 512KB chunks for high-speed video seeking and streaming
FileResponse.chunk_size = 512 * 1024
from pydantic import BaseModel, Field

from app.config import load_settings
from app.downloader import download_from_youtube, cancel_download
from app.fastsearch import fast_search_youtube, fast_search_youtube_list, get_youtube_video_info
from app.library import (
    find_by_file_id,
    find_by_query,
    iter_audio_files,
    _extract_id_from_filename,
    _title_from_filename,
    enforce_library_limit,
)
from app.logging_setup import setup_logging
from app.search_cache import SearchCache
from app.telegram_notify import send_telegram_message_async
from app.security import (
    SecurityMiddleware,
    rate_limiter,
    download_semaphore,
    visitor_tracker,
    get_client_ip,
)
import requests as _requests

logger = logging.getLogger("music_api")

_FILE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")


def _is_allowed_youtube_url(url: str) -> bool:
    u = url.strip().lower()
    return (
        u.startswith("https://www.youtube.com/")
        or u.startswith("http://www.youtube.com/")
        or u.startswith("https://youtube.com/")
        or u.startswith("http://youtube.com/")
        or u.startswith("https://youtu.be/")
        or u.startswith("http://youtu.be/")
    )
 

def _extract_youtube_id(url: str) -> Optional[str]:
    # Very small helper for common youtube URL formats.
    u = url.strip()
    if "v=" in u:
        part = u.split("v=", 1)[1]
        vid = part.split("&", 1)[0]
        return vid or None
    if "youtu.be/" in u:
        part = u.split("youtu.be/", 1)[1]
        vid = part.split("?", 1)[0].split("&", 1)[0]
        return vid or None
    return None


class MusicRequest(BaseModel):
    query: Optional[str] = Field(default=None, description="Search query (song name).")
    url: Optional[str] = Field(default=None, description="YouTube URL. If set, will download directly.")


class MusicResponse(BaseModel):
    found: bool
    downloaded: bool
    title: str
    file_id: str
    stream_url: str
    path: str
    source_url: Optional[str] = None


settings = load_settings()
setup_logging(log_level=settings.log_level, log_file=settings.log_file)
search_cache = SearchCache(settings.project_root / "cache" / "ytsearch.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.music_dir.mkdir(parents=True, exist_ok=True)
    # Startup metrics
    cpu_count = psutil.cpu_count(logical=True) or 0
    cpu_model = platform.processor() or "unknown"
    load = ""
    try:
        la = os.getloadavg()
        load = f" | loadavg={la[0]:.2f},{la[1]:.2f},{la[2]:.2f}"
    except Exception:
        load = ""
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(str(settings.music_dir))
    msg = (
        "Music API started\n"
        f"- Run: {settings.run_host}:{settings.run_port}\n"
        f"- PID: {os.getpid()}\n"
        f"- CPU: {cpu_count} vCPU ({cpu_model}){load}\n"
        f"- Memory: {vm.used/1024/1024/1024:.2f}GB / {vm.total/1024/1024/1024:.2f}GB ({vm.percent}%)\n"
        f"- Disk(music_dir): {du.used/1024/1024/1024:.2f}GB / {du.total/1024/1024/1024:.2f}GB ({du.used/du.total*100:.1f}%)\n"
        f"- music_dir: {settings.music_dir}"
    )
    logger.info("Music API started | run=%s:%s | music_dir=%s", settings.run_host, settings.run_port, settings.music_dir)
    send_telegram_message_async(settings.telegram_bot_token, settings.telegram_chat_id, msg)
    # Enforce transient storage limit on startup
    enforce_library_limit(settings.music_dir)
    yield


app = FastAPI(
    title="FastTube Studio Audio & Video Engine",
    version="2.0.0",
    lifespan=lifespan,
)

# Production security & anti-scanner middleware
app.add_middleware(SecurityMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index_page():
    index_file = _STATIC_DIR / "index.html"
    if index_file.is_file():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>FastTube Studio API</h1><p>Visit /health</p>")


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt():
    """Block all crawlers and search engine indexing."""
    return "User-agent: *\nDisallow: /\n"


@app.get("/api/stats")
async def api_stats(cid: Optional[str] = None):
    """Real-time visitor and view counter for public navbar display."""
    return await visitor_tracker.heartbeat(cid)


def _base_url(req: Request) -> str:
    """Robust base URL resolution behind reverse proxies (Nginx, Cloudflare, Traefik)."""
    if settings.base_url:
        return settings.base_url.rstrip("/")
    proto = req.headers.get("x-forwarded-proto") or req.url.scheme or "http"
    host = req.headers.get("x-forwarded-host") or req.headers.get("host") or req.url.netloc
    if host:
        return f"{proto}://{host}".rstrip("/")
    return str(req.base_url).rstrip("/")


def _media_type_for_path(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".mp3":
        return "audio/mpeg"
    if ext == ".m4a":
        return "audio/mp4"
    if ext == ".opus":
        return "audio/opus"
    if ext == ".ogg":
        return "audio/ogg"
    if ext == ".webm":
        return "audio/webm"
    if ext == ".mp4":
        return "video/mp4"
    if ext == ".wav":
        return "audio/wav"
    if ext == ".flac":
        return "audio/flac"
    return "application/octet-stream"


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict:
    return {"ok": True}


_SEARCH_CACHE: dict[str, tuple[float, list[dict]]] = {}


@app.get("/api/search")
async def api_search(q: str, request: Request, limit: int = 30):
    await rate_limiter.check(request, "search", max_requests=45, window_s=60)
    q = (q or "").strip()
    if not q:
        return []
    limit = max(1, min(int(limit or 30), 50))
    now = time.time()
    cache_key = f"{q.lower()}::{limit}"
    if cache_key in _SEARCH_CACHE:
        ts, cached_items = _SEARCH_CACHE[cache_key]
        if now - ts < 300:  # 5 minutes in-memory cache
            base_url = _base_url(request)
            for it in cached_items:
                vid = it.get("id")
                hit = find_by_file_id(settings.music_dir, vid) if vid else None
                it["is_downloaded"] = bool(hit)
                if hit:
                    it["stream_url"] = f"{base_url}/stream/{vid}"
            return cached_items

    items = await asyncio.to_thread(fast_search_youtube_list, q, limit=limit)
    base_url = _base_url(request)
    for it in items:
        vid = it.get("id")
        hit = find_by_file_id(settings.music_dir, vid) if vid else None
        it["is_downloaded"] = bool(hit)
        if hit:
            it["stream_url"] = f"{base_url}/stream/{vid}"

    _SEARCH_CACHE[cache_key] = (now, items)
    return items


class DownloadPayload(BaseModel):
    id: Optional[str] = Field(default=None, description="YouTube Video ID")
    url: Optional[str] = Field(default=None, description="YouTube URL")
    query: Optional[str] = Field(default=None, description="Search query or video name")
    format: str = Field(default="m4a", description="Format: m4a, mp3_320, mp3_128, wav, flac, opus, mp4_1080, mp4_720, mp4_480, mp4_360")


def _resolve_format(fmt: str) -> tuple[str, str, bool, str]:
    fmt = (fmt or "m4a").strip().lower()
    if fmt in ("mp3_320", "mp3"):
        return "mp3", "320k", False, "mp3"
    if fmt == "mp3_128":
        return "mp3", "128k", True, "mp3"
    if fmt == "wav":
        return "wav", "320k", False, "wav"
    if fmt == "flac":
        return "flac", "320k", False, "flac"
    if fmt in ("mp4_1080", "1080p"):
        return "mp4_1080", "128k", False, "mp4"
    if fmt in ("mp4", "mp4_720", "720p", "video"):
        return "mp4_720", "128k", False, "mp4"
    if fmt in ("mp4_480", "480p"):
        return "mp4_480", "128k", False, "mp4"
    if fmt in ("mp4_360", "360p"):
        return "mp4_360", "128k", False, "mp4"
    if fmt == "opus":
        return "opus", "160k", False, "webm"
    return "m4a", "128k", False, "m4a"


@app.post("/api/download")
async def api_download(payload: DownloadPayload, request: Request):
    await rate_limiter.check(request, "download", max_requests=15, window_s=60)
    t0 = time.perf_counter()
    target_id = (payload.id or "").strip()
    target_url = (payload.url or "").strip()
    target_query = (payload.query or "").strip()

    if not target_id and not target_url and not target_query:
        raise HTTPException(status_code=400, detail="Must provide 'id', 'url', or 'query'")

    if target_url and not target_id:
        target_id = _extract_youtube_id(target_url) or ""

    if target_id:
        target = f"https://www.youtube.com/watch?v={target_id}"
    elif target_url:
        target = target_url
    else:
        target = target_query

    fmt = (payload.format or "m4a").strip().lower()
    audio_mode, audio_bitrate, prefer_abr_128, target_ext = _resolve_format(fmt)

    # NOTE: Do not short-circuit on an existing file here. Reusing a cached file
    # regardless of its resolution made the quality selector appear to do nothing
    # (e.g. a previously downloaded 480p file kept being served for 1080p).
    # Always download so the requested quality is honored; yt-dlp overwrites the
    # same id-based filename.

    # Remove any previously cached file of the same id/ext so a lower/higher
    # quality switch cannot accidentally reuse the old bytes (and so the browser
    # does not serve a stale cached response).
    if target_id:
        try:
            old = find_by_file_id(settings.music_dir, target_id, ext=target_ext)
            if old and old.path.exists():
                old.path.unlink()
        except Exception:
            pass

    try:
        async with download_semaphore:
            res = await asyncio.to_thread(
                download_from_youtube,
                music_dir=settings.music_dir,
                cookies_dir=settings.cookies_dir,
                query_or_url=target,
                concurrent_fragments=settings.ytdlp_concurrent_fragments,
                max_duration_s=settings.max_video_duration_s,
                search_mode="first",
                audio_bitrate=audio_bitrate,
                ytdlp_verbose=settings.ytdlp_verbose,
                audio_mode=audio_mode,
                prefer_abr_128=prefer_abr_128,
                ytdlp_player_client=settings.ytdlp_player_client,
                fast_mode=settings.fast_mode,
                max_abr_kbps=320,
            )
    except asyncio.CancelledError:
        if target_id:
            cancel_download(target_id)
        logger.info("api_download: cancelled by client for %s", target)
        raise
    except Exception as e:
        logger.error("Download failed for %s: %s", target, e)
        raise HTTPException(status_code=500, detail=str(e)[:500])

    enforce_library_limit(settings.music_dir, exclude_id=res.file_id)
    base = _base_url(request)
    size_b = res.path.stat().st_size if res.path.exists() else 0
    elapsed_s = time.perf_counter() - t0

    actual_ext = res.path.suffix.lstrip(".")
    return {
        "success": True,
        "file_id": res.file_id,
        "title": res.title,
        "format": actual_ext,
        "size_mb": round(size_b / 1024 / 1024, 2),
        "duration_s": res.duration_s,
        "elapsed_s": round(elapsed_s, 2),
        "stream_url": f"{base}/stream/{res.file_id}?ext={actual_ext}",
        "download_url": f"{base}/stream/{res.file_id}?download=1&ext={actual_ext}",
        "path": str(res.path),
    }


@app.post("/api/download/cancel")
async def api_download_cancel(id: str = ""):
    """Instantly cancel an in-flight yt-dlp download process."""
    cid = (id or "").strip()
    cancelled = False
    if cid:
        cancelled = cancel_download(cid)
    return {"ok": True, "cancelled": cancelled, "file_id": cid}


@app.get("/api/library")
async def api_library():
    # Public server library disabled for copyright privacy & security
    return []


@app.delete("/api/library/{file_id}")
def api_delete_file(file_id: str):
    raise HTTPException(status_code=404, detail="Library disabled")


@app.post("/api/music", response_model=MusicResponse)
def get_or_download_music(payload: MusicRequest, request: Request) -> MusicResponse:
    return _handle_music_request(query=payload.query, url=payload.url, request=request)


@app.get("/api/music", response_model=MusicResponse)
def get_or_download_music_get(request: Request, query: Optional[str] = None, url: Optional[str] = None) -> MusicResponse:
    return _handle_music_request(query=query, url=url, request=request)


def _handle_music_request(*, query: Optional[str], url: Optional[str], request: Request) -> MusicResponse:
    t0 = time.perf_counter()
    logger.info("request /api/music | query=%r url=%r", query, url)
    if not query and not url:
        raise HTTPException(status_code=400, detail="Provide 'query' or 'url'")

    # 1) If url provided: download (but still check if already exists by id if possible)
    if url:
        if len(url) > 800:
            raise HTTPException(status_code=400, detail="URL too long")
        if not _is_allowed_youtube_url(url):
            raise HTTPException(status_code=400, detail="Only YouTube URLs are allowed")
        vid = _extract_youtube_id(url)
        if vid:
            existing = find_by_file_id(settings.music_dir, vid)
            if existing:
                logger.info("hit local by url id=%s | path=%s", existing.file_id, existing.path)
                stream = f"{_base_url(request)}/stream/{existing.file_id}"
                return MusicResponse(
                    found=True,
                    downloaded=False,
                    title=existing.title,
                    file_id=existing.file_id,
                    stream_url=stream,
                    path=str(existing.path),
                    source_url=url,
                )
        send_telegram_message_async(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            f"Download requested: {url}",
        )
        res = download_from_youtube(
            music_dir=settings.music_dir,
            cookies_dir=settings.cookies_dir,
            query_or_url=url,
            concurrent_fragments=settings.ytdlp_concurrent_fragments,
            max_duration_s=settings.max_video_duration_s,
            search_mode=settings.youtube_search_mode,
            audio_bitrate=settings.audio_bitrate,
            ytdlp_verbose=settings.ytdlp_verbose,
            audio_mode=settings.audio_mode,
            prefer_abr_128=settings.prefer_abr_128,
            ytdlp_js_runtimes=settings.ytdlp_js_runtimes,
            ytdlp_js_runtimes_path=settings.ytdlp_js_runtimes_path,
            fast_mode=settings.fast_mode,
            prefer_itag_140=settings.prefer_itag_140,
            ytdlp_player_client=settings.ytdlp_player_client,
            ytdlp_debug_list_formats=settings.ytdlp_debug_list_formats,
            max_abr_kbps=settings.max_abr_kbps,
        )
        stream = f"{_base_url(request)}/stream/{res.file_id}"
        size_b = res.path.stat().st_size if res.path.exists() else 0
        elapsed_s = time.perf_counter() - t0
        dur_txt = f"{res.duration_s}s" if res.duration_s is not None else "unknown"
        logger.info(
            "download completed | id=%s title=%r size_mb=%.2f duration=%s elapsed=%.2fs path=%s",
            res.file_id,
            res.title,
            size_b / 1024 / 1024,
            dur_txt,
            elapsed_s,
            res.path,
        )
        send_telegram_message_async(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            "Download completed\n"
            f"- Title: {res.title}\n"
            f"- Size: {size_b/1024/1024:.2f} MB\n"
            f"- Duration: {dur_txt}\n"
            f"- Elapsed: {elapsed_s:.2f} s\n"
            f"- Stream: {stream}",
        )
        return MusicResponse(
            found=False,
            downloaded=True,
            title=res.title,
            file_id=res.file_id,
            stream_url=stream,
            path=str(res.path),
            source_url=res.source_url,
        )

    assert query is not None
    q = query.strip()
    if not q:
        raise HTTPException(status_code=400, detail="Empty query")
    if len(q) > 200:
        raise HTTPException(status_code=400, detail="Query too long")

    # 2) Check local library first.
    hit = find_by_query(settings.music_dir, q)
    
    if hit:
        logger.info("hit local by query | id=%s title=%r path=%s", hit.file_id, hit.title, hit.path)
        stream = f"{_base_url(request)}/stream/{hit.file_id}"
        return MusicResponse(
            found=True,
            downloaded=False,
            title=hit.title,
            file_id=hit.file_id,
            stream_url=stream,
            path=str(hit.path),
            source_url=None,
        )

    # 3) Not found -> download from YouTube search.
    send_telegram_message_async(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        f"Not found locally. Downloading from YouTube: {q}",
    )
    try:
        cached_url = search_cache.get(q)
        dl_source = cached_url or q
        res = download_from_youtube(
            music_dir=settings.music_dir,
            cookies_dir=settings.cookies_dir,
            query_or_url=dl_source,
            concurrent_fragments=settings.ytdlp_concurrent_fragments,
            max_duration_s=settings.max_video_duration_s,
            search_mode=settings.youtube_search_mode,
            audio_bitrate=settings.audio_bitrate,
            ytdlp_verbose=settings.ytdlp_verbose,
            audio_mode=settings.audio_mode,
            prefer_abr_128=settings.prefer_abr_128,
            ytdlp_js_runtimes=settings.ytdlp_js_runtimes,
            ytdlp_js_runtimes_path=settings.ytdlp_js_runtimes_path,
            fast_mode=settings.fast_mode,
            prefer_itag_140=settings.prefer_itag_140,
            ytdlp_player_client=settings.ytdlp_player_client,
            ytdlp_debug_list_formats=settings.ytdlp_debug_list_formats,
            max_abr_kbps=settings.max_abr_kbps,
        )
        if not cached_url and res.source_url and "://" in res.source_url:
            search_cache.set(q, res.source_url)
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)[:800])
    stream = f"{_base_url(request)}/stream/{res.file_id}"
    size_b = res.path.stat().st_size if res.path.exists() else 0
    elapsed_s = time.perf_counter() - t0
    dur_txt = f"{res.duration_s}s" if res.duration_s is not None else "unknown"
    logger.info(
        "download completed | id=%s title=%r size_mb=%.2f duration=%s elapsed=%.2fs path=%s",
        res.file_id,
        res.title,
        size_b / 1024 / 1024,
        dur_txt,
        elapsed_s,
        res.path,
    )
    send_telegram_message_async(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        "Download completed\n"
        f"- Title: {res.title}\n"
        f"- Size: {size_b/1024/1024:.2f} MB\n"
        f"- Duration: {dur_txt}\n"
        f"- Elapsed: {elapsed_s:.2f} s\n"
        f"- Stream: {stream}",
    )
    return MusicResponse(
        found=False,
        downloaded=True,
        title=res.title,
        file_id=res.file_id,
        stream_url=stream,
        path=str(res.path),
        source_url=res.source_url,
    )


@app.api_route("/stream/{file_id}", methods=["GET", "HEAD"])
def stream(file_id: str, download: bool = False, ext: Optional[str] = None) -> FileResponse:
    if not _FILE_ID_RE.match(file_id):
        raise HTTPException(status_code=404, detail="File not found")
    if ext and not re.match(r"^[a-zA-Z0-9]{2,5}$", ext):
        raise HTTPException(status_code=400, detail="Invalid extension")
    hit = find_by_file_id(settings.music_dir, file_id, ext=ext)
    if not hit or not hit.path.exists():
        # Auto on-demand download so missing/purged files or direct links never return a broken JSON download
        try:
            target = f"https://www.youtube.com/watch?v={file_id}"
            audio_mode, audio_bitrate, prefer_abr_128, target_ext = _resolve_format(ext or "m4a")
            res = download_from_youtube(
                music_dir=settings.music_dir,
                cookies_dir=settings.cookies_dir,
                query_or_url=target,
                concurrent_fragments=settings.ytdlp_concurrent_fragments,
                max_duration_s=settings.max_video_duration_s,
                search_mode="first",
                audio_bitrate=audio_bitrate,
                ytdlp_verbose=settings.ytdlp_verbose,
                audio_mode=audio_mode,
                prefer_abr_128=prefer_abr_128,
                ytdlp_player_client=settings.ytdlp_player_client,
                fast_mode=settings.fast_mode,
                max_abr_kbps=320,
            )
            enforce_library_limit(settings.music_dir, exclude_id=res.file_id)
            hit = find_by_file_id(settings.music_dir, file_id, ext=res.path.suffix.lstrip("."))
        except Exception as e:
            logger.warning("On-demand download for stream %s failed: %s", file_id, e)

    if not hit or not hit.path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    # FileResponse supports conditional/range with proper headers via Starlette.
    # When download=False: no filename -> browsers stream inline
    # When download=True: filename set -> browsers download as attachment
    filename = f"{hit.title}{hit.path.suffix}" if download else None
    stream_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "public, max-age=3600",
    }
    return FileResponse(
        path=str(hit.path),
        media_type=_media_type_for_path(hit.path),
        filename=filename,
        headers=stream_headers,
    )


# ---------------------------------------------------------------------------
# Settings + AI Playlist
# ---------------------------------------------------------------------------

def _read_env_file() -> dict:
    env_path = settings.project_root / ".env"
    out = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_env_key(key: str, value: str) -> None:
    env_path = settings.project_root / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
    found = False
    for i, ln in enumerate(lines):
        if ln.strip().startswith(key + "=") or ln.strip().startswith(key + " ="):
            lines[i] = f"{key}={value}"
            found = True
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@app.get("/api/settings")
def api_get_settings():
    env = _read_env_file()
    # Prefer real env var, then .env file.
    key = os.getenv("GROQ_API_KEY") or env.get("GROQ_API_KEY") or ""
    return {"groq_api_key": key}


class SettingsPayload(BaseModel):
    groq_api_key: Optional[str] = None


@app.post("/api/settings")
def api_set_settings(payload: SettingsPayload):
    if payload.groq_api_key is not None:
        _write_env_key("GROQ_API_KEY", payload.groq_api_key.strip())
        os.environ["GROQ_API_KEY"] = payload.groq_api_key.strip()
    return {"ok": True}


def _parse_songs_json(text: str) -> list[str]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    songs = None
    import json as _json
    try:
        songs = _json.loads(text)
    except Exception:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            try:
                songs = _json.loads(text[start:end + 1])
            except Exception:
                songs = None
    if not isinstance(songs, list):
        return []
    clean = []
    seen = set()
    for s in songs:
        st = str(s).strip()
        if st and st.lower() not in seen:
            seen.add(st.lower())
            clean.append(st)
    return clean


class PlaylistPayload(BaseModel):
    prompt: str
    limit: int = 10
    format: str = "m4a"
    model: str = "groq/compound"
    web_search: bool = True


@app.post("/api/playlist/generate")
async def api_playlist_generate(payload: PlaylistPayload, request: Request):
    await rate_limiter.check(request, "playlist_gen", max_requests=10, window_s=60)
    key = os.getenv("GROQ_API_KEY") or _read_env_file().get("GROQ_API_KEY") or ""
    if not key:
        raise HTTPException(status_code=400, detail="Chưa set Groq API key trong Cài đặt")
    prompt = (payload.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="Nhập yêu cầu playlist")
    limit = max(1, min(int(payload.limit or 10), 25))

    req_model = (payload.model or "groq/compound").strip()
    use_web_search = bool(payload.web_search)

    # Determine candidate models to try
    if req_model == "groq/compound":
        use_web_search = True
        model_candidates = ["groq/compound", "openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    elif req_model == "openai/gpt-oss-20b":
        model_candidates = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    else:
        model_candidates = [req_model, "openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]

    # Ground the LLM with real search catalog hints to prevent hallucinations
    catalog_hints = ""
    try:
        hints = await asyncio.to_thread(fast_search_youtube_list, prompt, limit=20)
        valid_hint_titles = [h["title"] for h in hints if h.get("title")][:15]
        if valid_hint_titles:
            catalog_hints = "\n\nVERIFIED TRACK HINTS FROM MUSIC CATALOG:\n" + "\n".join(f"- {t}" for t in valid_hint_titles)
    except Exception:
        catalog_hints = ""

    # Ask Groq for a strictly verified JSON list of genuine song titles.
    system_msg = (
        "You are an expert music curator and discography specialist. "
        "The user will describe a genre, mood, era, artist, or theme. "
        "CRITICAL ACCURACY GUIDELINES:\n"
        "1. You MUST ONLY suggest 100% REAL, GENUINE, OFFICIALLY RELEASED songs that actually exist in reality. NEVER hallucinate, guess, or make up fake song titles.\n"
        "2. If an artist is specified (e.g. Thanh Hưng, Sơn Tùng M-TP, Vũ, Mỹ Tâm, Taylor Swift, etc.), you MUST ONLY list genuine hits actually performed or composed by that EXACT artist. NEVER assign songs by other artists to them.\n"
        "3. Provide the clean, exact title: remove 'Official MV', 'Lyrics Video', '4K', etc.\n"
        "4. Return STRICTLY a valid JSON array of strings in format: [\"Song Title - Artist Name\", ...].\n"
        f"5. Return EXACTLY {limit} items. No markdown backticks, no commentary, just the raw JSON array."
        + catalog_hints
    )
    last_err = None
    songs = []
    for model_name in model_candidates:
        req_body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": f"{prompt}. Return exactly {limit} songs as a JSON array."},
            ],
            "temperature": 0.15,
            "max_tokens": 1024,
        }
        if use_web_search and model_name in ("groq/compound", "openai/gpt-oss-20b", "openai/gpt-oss-120b"):
            req_body["tools"] = [{"type": "browser_search"}]

        try:
            r = _requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=req_body,
                timeout=45 if use_web_search else 30,
            )
            # If 404 (model not found on groq/compound), skip to next candidate
            if r.status_code == 404:
                last_err = f"Groq model {model_name} not available: {r.text[:200]}"
                continue
            # If 400 with tool error, retry without tools
            if r.status_code == 400 and "tools" in req_body:
                del req_body["tools"]
                r = _requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=req_body,
                    timeout=30,
                )
            if r.status_code != 200:
                last_err = f"Groq error ({model_name}) {r.status_code}: {r.text[:250]}"
                continue
            data = r.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = _parse_songs_json(content)
            if parsed and len(parsed) >= min(3, limit):
                songs = parsed[:limit]
                logger.info("Playlist generated via model=%s web_search=%s count=%d", model_name, use_web_search, len(songs))
                break
            else:
                last_err = f"Model {model_name} returned unparseable content: {content[:150]}"
        except Exception as e:
            last_err = f"Groq request failed ({model_name}): {e}"
            continue

    if not songs:
        raise HTTPException(status_code=502, detail=last_err or "Không thể tạo danh sách bài hát từ AI")

    audio_mode, audio_bitrate, prefer_abr_128, target_ext = _resolve_format(payload.format or "m4a")

    # Download each song with title scoring validation and deduplication
    results = []
    seen_ids: set[str] = set()
    for song in songs:
        try:
            async with download_semaphore:
                res = await asyncio.to_thread(
                    download_from_youtube,
                    music_dir=settings.music_dir,
                    cookies_dir=settings.cookies_dir,
                    query_or_url=song,
                    concurrent_fragments=settings.ytdlp_concurrent_fragments,
                    max_duration_s=min(settings.max_video_duration_s, 600),
                    search_mode="score",
                    audio_bitrate=audio_bitrate,
                    ytdlp_verbose=settings.ytdlp_verbose,
                    audio_mode=audio_mode,
                    prefer_abr_128=prefer_abr_128,
                    ytdlp_js_runtimes=settings.ytdlp_js_runtimes,
                    ytdlp_js_runtimes_path=settings.ytdlp_js_runtimes_path,
                    fast_mode=settings.fast_mode,
                    prefer_itag_140=settings.prefer_itag_140,
                    ytdlp_player_client=settings.ytdlp_player_client,
                    ytdlp_debug_list_formats=settings.ytdlp_debug_list_formats,
                    max_abr_kbps=settings.max_abr_kbps,
                    exclude_ids=seen_ids,
                )
            if res.file_id:
                seen_ids.add(res.file_id)
            enforce_library_limit(settings.music_dir, exclude_id=res.file_id)
            results.append({
                "title": res.title,
                "file_id": res.file_id,
                "source_query": song,
                "stream_url": f"/stream/{res.file_id}?ext={target_ext}",
            })
        except Exception as e:
            results.append({"title": song, "file_id": None, "error": str(e)[:200]})
    return {"songs": songs, "results": results}


# ---------------------------------------------------------------------------
# Saved playlists + download a list
# ---------------------------------------------------------------------------

_PLAYLISTS_FILE = settings.project_root / "playlists.json"


def _load_playlists() -> list[dict]:
    if not _PLAYLISTS_FILE.exists():
        return []
    try:
        return json.loads(_PLAYLISTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_playlists(pls: list[dict]) -> None:
    _PLAYLISTS_FILE.write_text(json.dumps(pls, ensure_ascii=False, indent=2), encoding="utf-8")


@app.get("/api/playlists")
def api_list_playlists():
    return {"playlists": _load_playlists()}


class SavePlaylistPayload(BaseModel):
    name: str
    songs: list[str] = []
    items: list[dict] = []


@app.post("/api/playlists")
def api_save_playlist(payload: SavePlaylistPayload):
    name = (payload.name or "").strip() or "Playlist"
    songs = [s.strip() for s in (payload.songs or []) if str(s).strip()]
    items = [it for it in (payload.items or []) if isinstance(it, dict)]
    if not songs and not items:
        raise HTTPException(status_code=400, detail="Danh sách rỗng")
    import time as _t
    pls = _load_playlists()
    entry = {"id": str(int(_t.time() * 1000)), "name": name, "songs": songs, "items": items, "created_at": _t.time()}
    pls.insert(0, entry)
    _save_playlists(pls)
    return {"ok": True, "playlist": entry}


@app.delete("/api/playlists/{pid}")
def api_delete_playlist(pid: str):
    pls = _load_playlists()
    pls = [p for p in pls if str(p.get("id")) != str(pid)]
    _save_playlists(pls)
    return {"ok": True}


class DownloadListPayload(BaseModel):
    songs: list[str]
    format: str = "m4a"
    limit: int = 50


@app.post("/api/playlist/download")
async def api_playlist_download(payload: DownloadListPayload, request: Request):
    await rate_limiter.check(request, "playlist_dl", max_requests=10, window_s=60)
    fmt = payload.format or "m4a"
    audio_mode, audio_bitrate, prefer_abr_128, target_ext = _resolve_format(fmt)
    songs = [s.strip() for s in (payload.songs or []) if str(s).strip()]
    results = []
    seen_ids: set[str] = set()
    for song in songs:
        try:
            async with download_semaphore:
                res = await asyncio.to_thread(
                    download_from_youtube,
                    music_dir=settings.music_dir,
                    cookies_dir=settings.cookies_dir,
                    query_or_url=song,
                    concurrent_fragments=settings.ytdlp_concurrent_fragments,
                    max_duration_s=min(settings.max_video_duration_s, 600),
                    search_mode="score",
                    audio_bitrate=audio_bitrate,
                    ytdlp_verbose=settings.ytdlp_verbose,
                    audio_mode=audio_mode,
                    prefer_abr_128=prefer_abr_128,
                    ytdlp_js_runtimes=settings.ytdlp_js_runtimes,
                    ytdlp_js_runtimes_path=settings.ytdlp_js_runtimes_path,
                    fast_mode=settings.fast_mode,
                    prefer_itag_140=settings.prefer_itag_140,
                    ytdlp_player_client=settings.ytdlp_player_client,
                    ytdlp_debug_list_formats=settings.ytdlp_debug_list_formats,
                    max_abr_kbps=settings.max_abr_kbps,
                    exclude_ids=seen_ids,
                )
            if res.file_id:
                seen_ids.add(res.file_id)
            enforce_library_limit(settings.music_dir, exclude_id=res.file_id)
            results.append({
                "title": res.title,
                "file_id": res.file_id,
                "source_query": song,
                "stream_url": f"/stream/{res.file_id}?ext={target_ext}",
            })
        except Exception as e:
            results.append({"title": song, "file_id": None, "error": str(e)[:200]})
    return {"results": results}


if __name__ == "__main__":
    import uvicorn

    # uvloop is not available on Windows; fall back to asyncio default loop there.
    try:
        import uvloop  # noqa: F401
        _loop = "uvloop"
    except Exception:
        _loop = "asyncio"

    # httptools may also be unavailable on some platforms; let uvicorn pick a safe default.
    try:
        import httptools  # noqa: F401
        _http = "httptools"
    except Exception:
        _http = "auto"

    _workers = 1 if os.name == "nt" else 2
    uvicorn.run(
        "app.main:app",
        host=settings.run_host,
        port=settings.run_port,
        loop=_loop,
        http=_http,
        workers=_workers,
        reload=False,
    )


