from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_ACTIVE_CANCELLATION_EVENTS: dict[str, threading.Event] = {}


def cancel_download(file_id: str) -> bool:
    """Signal any active yt-dlp download for file_id to terminate immediately."""
    file_id = (file_id or "").strip()
    ev = _ACTIVE_CANCELLATION_EVENTS.get(file_id)
    if ev:
        ev.set()
        logger.info("cancel_download: signal set for file_id=%s", file_id)
        return True
    return False


def _extract_id_from_url_or_id(s: str) -> Optional[str]:
    s = (s or "").strip()
    if re.match(r"^[A-Za-z0-9_-]{11}$", s):
        return s
    if "v=" in s:
        part = s.split("v=", 1)[1]
        return part.split("&", 1)[0].split("#", 1)[0]
    if "youtu.be/" in s:
        return s.split("youtu.be/", 1)[1].split("?", 1)[0].split("#", 1)[0]
    return None


def _get_ytdlp_bin() -> str:
    # 0) Environment variable override
    override = os.getenv("YTDLP_BIN") or os.getenv("YTDLP_PATH")
    if override and Path(override).is_file():
        return str(Path(override).resolve())

    # 1) Search in .local/bin (Pterodactyl / HIDENCLOUD container pip --prefix .local)
    for root_dir in [Path.cwd(), Path(__file__).resolve().parent.parent]:
        for bin_name in ["yt-dlp", "yt-dlp.exe"]:
            local_bin = root_dir / ".local" / "bin" / bin_name
            if local_bin.is_file():
                if os.name != "nt":
                    try:
                        os.chmod(local_bin, os.stat(local_bin).st_mode | 0o755)
                    except Exception:
                        pass
                return str(local_bin.resolve())

    # 2) Python venv directory (e.g. venv/bin or venv/Scripts)
    base = Path(sys.executable).parent
    candidates = [base / "yt-dlp", base / "yt-dlp.exe"]
    for venv_bin in candidates:
        if venv_bin.is_file():
            return str(venv_bin)

    # 3) Current process PATH search
    found = shutil.which("yt-dlp")
    if found:
        return found

    # 4) Absolute bulletproof fallback: write a launcher script using python -m yt_dlp
    try:
        import yt_dlp
        launcher = Path.cwd() / "cache" / ("yt-dlp.bat" if os.name == "nt" else "yt-dlp")
        launcher.parent.mkdir(parents=True, exist_ok=True)
        if not launcher.is_file():
            if os.name == "nt":
                launcher.write_text(f'@"{sys.executable}" -m yt_dlp %*\n', encoding="utf-8")
            else:
                launcher.write_text(
                    f"#!{sys.executable}\n"
                    "import sys\n"
                    "from yt_dlp import main\n"
                    "if __name__ == '__main__':\n"
                    "    sys.exit(main())\n",
                    encoding="utf-8",
                )
                try:
                    os.chmod(launcher, 0o755)
                except Exception:
                    pass
        return str(launcher.resolve())
    except Exception:
        pass

    return "yt-dlp"


def _get_ffmpeg_path() -> Optional[str]:
    # 0) Explicit environment override or .env
    override = os.getenv("FFMPEG_PATH") or os.getenv("FFMPEG_BIN")
    if override and Path(override).is_file():
        return str(Path(override).resolve())

    # 1) Search in .local/bin (Pterodactyl / HIDENCLOUD container pip --prefix .local)
    for root_dir in [Path.cwd(), Path(__file__).resolve().parent.parent]:
        for bin_name in ["ffmpeg", "ffmpeg.exe"]:
            local_bin = root_dir / ".local" / "bin" / bin_name
            if local_bin.is_file():
                return str(local_bin.resolve())

    # 2) System ffmpeg on current process PATH.
    found = shutil.which("ffmpeg")
    if found:
        return found

    # 2) Check Windows Registry PATH (User & Machine) in case PATH was added after process started
    if os.name == "nt":
        try:
            import winreg
            for root_k in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    sub = r"Environment" if root_k == winreg.HKEY_CURRENT_USER else r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
                    with winreg.OpenKey(root_k, sub) as k:
                        val, _ = winreg.QueryValueEx(k, "Path")
                        for p_item in val.split(os.pathsep):
                            cand = Path(p_item.strip()) / "ffmpeg.exe"
                            if cand.is_file():
                                return str(cand.resolve())
                except Exception:
                    pass
        except Exception:
            pass

        # 3) Check user documents or typical install directories
        user_docs = Path.home() / "Documents"
        if user_docs.exists():
            for p in user_docs.glob("**/ffmpeg.exe"):
                if p.is_file():
                    return str(p.resolve())

    # 4) imageio-ffmpeg bundled static binary (cross-platform fallback).
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).is_file():
            return str(exe)
    except Exception:
        pass
    return None


# Make ffmpeg discoverable to yt-dlp (which scans PATH) as early as possible.
_FFMPEG_PATH = _get_ffmpeg_path()
if _FFMPEG_PATH:
    _ffmpeg_dir = str(Path(_FFMPEG_PATH).parent)
    if _ffmpeg_dir not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")
    logger.info("FFmpeg configured: %s", _FFMPEG_PATH)


# Hook YoutubeIE to disk-cache player JS across process runs (saves ~1.5s - 2s per request)
try:
    import yt_dlp
    from yt_dlp.extractor.youtube import YoutubeIE

    _orig_load_player = YoutubeIE._load_player

    def _cached_load_player(self, video_id, player_url, fatal=True):
        key = self._player_js_cache_key(player_url)
        cache_dir = Path(__file__).resolve().parent.parent / "cache" / "ytdlp"
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"player_{key}.js"
        if key not in self._code_cache:
            if cache_path.exists() and cache_path.stat().st_size > 10000:
                self._code_cache[key] = cache_path.read_text(encoding="utf-8")
                return self._code_cache[key]
            code = _orig_load_player(self, video_id, player_url, fatal=fatal)
            if code:
                self._code_cache[key] = code
                try:
                    cache_path.write_text(code, encoding="utf-8")
                except Exception:
                    pass
        return self._code_cache.get(key)

    YoutubeIE._load_player = _cached_load_player
except Exception as e:
    logger.warning("Failed to hook YoutubeIE player cache: %s", e)


def _detect_js_engine() -> Optional[str]:
    for cand in ["node", "deno", "bun", "quickjs"]:
        if shutil.which(cand):
            return cand
    return None


def _fast_inprocess_download(
    source_url: str,
    music_dir: Path,
    cookie_file: Optional[Path],
    format_spec: str,
    player_client: str,
    concurrent_fragments: int,
    ytdlp_cache_dir: Path,
    ytdlp_verbose: bool = False,
    force_mp3: bool = False,
    audio_bitrate: str = "128k",
    target_codec: Optional[str] = None,
    merge_output_format: Optional[str] = None,
) -> Optional[DownloadResult]:
    client = (player_client or "android").strip()
    # visionos/android_vr do not support cookies; passing cookies causes yt-dlp to skip them
    use_cookie = cookie_file if (cookie_file and client not in ("visionos", "android_vr", "android", "ios")) else None

    if not merge_output_format and "/18" not in format_spec and "/best" not in format_spec:
        format_spec = f"{format_spec}/18/best"

    js_eng = _detect_js_engine()
    opts = {
        "noplaylist": True,
        "format": format_spec,
        "concurrent_fragment_downloads": max(16, concurrent_fragments),
        "http_chunk_size": 10485760,  # 10MB chunking bypasses YouTube playback-speed throttling
        "buffersize": 4194304,        # 4MB buffer for high-throughput disk I/O
        "cachedir": str(ytdlp_cache_dir),
        "outtmpl": str(music_dir / "%(title).200B [%(id)s].%(ext)s"),
        "cookiefile": str(use_cookie) if use_cookie else None,
        "nocheckcertificate": True,
        "quiet": not ytdlp_verbose,
        "no_warnings": not ytdlp_verbose,
        "extractor_args": {
            "youtube": {
                "player_client": [client],
                "skip": ["hls", "web_embedded"],
            }
        },
    }
    if js_eng:
        opts["js_runtimes"] = {js_eng: {}}
    if merge_output_format:
        opts["merge_output_format"] = merge_output_format
    if _FFMPEG_PATH:
        opts["ffmpeg_location"] = _FFMPEG_PATH
    # Force re-download/overwrite so changing quality actually replaces the old file.
    opts["overwrites"] = True
    opts["continuedl"] = False
    opts["updatetime"] = False  # Keep current local download time, do not overwrite mtime with YouTube upload date

    codec = target_codec or ("mp3" if force_mp3 else None)
    if not codec and not merge_output_format:
        codec = "m4a"
    if codec in ("mp3", "m4a", "wav", "flac"):
        pp = {
            "key": "FFmpegExtractAudio",
            "preferredcodec": codec,
        }
        if codec == "mp3":
            pp["preferredquality"] = audio_bitrate.replace("k", "")
        opts["postprocessors"] = [pp]
    try:
        import yt_dlp
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source_url, download=True)
            if not info:
                return None
            vid = str(info.get("id") or "").strip()
            title = str(info.get("title") or vid).strip()
            webpage_url = str(info.get("webpage_url") or source_url).strip()
            dur = info.get("duration")
            dur_i = int(dur) if dur is not None else None

            filename = ydl.prepare_filename(info)
            p = Path(filename)
            if codec:
                p = p.with_suffix(f".{codec}")
            elif merge_output_format:
                p = p.with_suffix(f".{merge_output_format}")
            if not p.exists():
                candidates = list(music_dir.glob(f"*{vid}*"))
                if candidates:
                    matching = [c for c in candidates if (codec and c.suffix == f".{codec}") or (merge_output_format and c.suffix == f".{merge_output_format}")]
                    p = matching[0] if matching else candidates[0]
            if p.exists() and p.is_file():
                return DownloadResult(file_id=vid, title=title, path=p, source_url=webpage_url, duration_s=dur_i)
    except Exception as e:
        logger.warning("fast in-process download error, will fall back: %s", e)
        return None
    return None

@dataclass(frozen=True)
class DownloadResult:
    file_id: str
    title: str
    path: Path
    source_url: str
    duration_s: Optional[int] = None


def _pick_cookie_file(cookies_dir: Path) -> Optional[Path]:
    if not cookies_dir.exists():
        return None
    # Prefer *.txt cookie export (Netscape format) from browser.
    for p in sorted(cookies_dir.iterdir()):
        if p.is_file() and p.suffix.lower() == ".txt":
            return p
    return None


def _norm_title(s: str) -> str:
    s = s.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\[[^\]]+\]|\([^)]+\)", " ", s)  # remove bracketed noise
    s = re.sub(r"[\s\-_]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s.strip()


def _score_title(query: str, title: str) -> int:
    q = _norm_title(query)
    t = _norm_title(title)
    if not q or not t:
        return 0
    if q == t:
        return 10_000
    score = 0
    if q in t:
        score += 3000 + len(q)
    # token overlap
    q_tokens = set(q.split())
    t_tokens = set(t.split())
    score += 200 * len(q_tokens & t_tokens)
    # prefer "official audio"/"lyrics" a bit, but not required
    if "official" in t_tokens:
        score += 50
    if "audio" in t_tokens:
        score += 50
    return score


def _ytsearch_pick_best(
    *,
    cookies_dir: Path,
    query: str,
    max_duration_s: int,
    search_n: int = 10,
    exclude_ids: set[str] | list[str] | None = None,
) -> dict:
    cookie_file = _pick_cookie_file(cookies_dir)
    src = f"ytsearch{max(1, int(search_n))}:{query}"
    cmd = [_get_ytdlp_bin(), "--flat-playlist", "--dump-single-json", src]
    if cookie_file:
        cmd.insert(1, "--cookies")
        cmd.insert(2, str(cookie_file))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp search failed: {proc.stderr.strip()[:1200]}")
    data = json.loads(proc.stdout)
    entries = data.get("entries") or []
    best = None
    best_score = -1
    excluded = set(exclude_ids or [])
    for e in entries:
        vid = (e.get("id") or "").strip()
        title = (e.get("title") or "").strip()
        dur = e.get("duration")
        try:
            dur_i = int(dur) if dur is not None else None
        except (TypeError, ValueError):
            dur_i = None
        if not vid or not title or vid in excluded:
            continue
        if dur_i is not None and dur_i > int(max_duration_s):
            continue
        s = _score_title(query, title)
        if s > best_score:
            best_score = s
            best = {"id": vid, "title": title, "duration": dur_i}
    if not best:
        raise RuntimeError(f"No suitable YouTube result under {max_duration_s}s for query='{query}'")
    best["webpage_url"] = f"https://www.youtube.com/watch?v={best['id']}"
    return best


def _ytsearch_list(
    *,
    cookies_dir: Path,
    query: str,
    search_n: int = 10,
    exclude_ids: set[str] | list[str] | None = None,
) -> list[str]:
    cookie_file = _pick_cookie_file(cookies_dir)
    src = f"ytsearch{max(1, int(search_n))}:{query}"
    cmd = [_get_ytdlp_bin(), "--flat-playlist", "--dump-single-json", src]
    if cookie_file:
        cmd.insert(1, "--cookies")
        cmd.insert(2, str(cookie_file))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"yt-dlp search timeout after 15s for query={query}")
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp search failed: {proc.stderr.strip()[:1200]}")
    data = json.loads(proc.stdout)
    entries = data.get("entries") or []
    urls: list[str] = []
    excluded = set(exclude_ids or [])
    for e in entries:
        vid = (e.get("id") or "").strip()
        if vid and vid not in excluded:
            urls.append(f"https://www.youtube.com/watch?v={vid}")
    return urls

def download_from_youtube(
    *,
    music_dir: Path,
    cookies_dir: Path,
    query_or_url: str,
    concurrent_fragments: int = 4,
    max_duration_s: int = 600,
    search_mode: str = "first",
    audio_bitrate: str = "128k",
    ytdlp_verbose: bool = False,
    audio_mode: str | None = None,
    prefer_abr_128: bool = True,
    ytdlp_js_runtimes: str | None = None,
    ytdlp_js_runtimes_path: str | None = None,
    fast_mode: bool = False,
    prefer_itag_140: bool = True,
    ytdlp_player_client: str | None = None,
    ytdlp_debug_list_formats: bool = False,
    max_abr_kbps: int = 96,
    exclude_ids: set[str] | list[str] | None = None,
    cancel_event: Optional[threading.Event] = None,
) -> DownloadResult:
    music_dir.mkdir(parents=True, exist_ok=True)
    target_id = _extract_id_from_url_or_id(query_or_url)
    ev = cancel_event or threading.Event()
    if target_id:
        _ACTIVE_CANCELLATION_EVENTS[target_id] = ev
    
    # Use audio_mode from settings if not explicitly provided
    if audio_mode is None:
        from app.config import load_settings
        settings = load_settings()
        audio_mode = settings.audio_mode

    # Use ytsearch as fallback if user provides a plain query.
    source = query_or_url.strip()
    if not source:
        raise ValueError("Empty query/url")
    is_url = "://" in source
    if target_id and not is_url:
        source = f"https://www.youtube.com/watch?v={target_id}"
        is_url = True
    fallback_urls: list[str] | None = None
    if not is_url:
        mode = (search_mode or "first").strip().lower()
        if fast_mode:
            # Fast path: 1-call innertube search (~0.8-1.5s) instead of yt-dlp
            # ytsearch (~4s), then download the picked video directly.
            # On any fast-search failure, fall back to ytsearch1: inline.
            fast = None
            try:
                from app.fastsearch import fast_search_youtube
                fast = fast_search_youtube(
                    source,
                    max_duration_s=max_duration_s,
                    search_mode=mode,
                    exclude_ids=exclude_ids,
                )
            except Exception as e:
                logger.warning("fast search exception, falling back: %s", e)
            if fast:
                logger.info("fast search pick | id=%s title=%r", fast["id"], fast["title"])
                source = f"https://www.youtube.com/watch?v={fast['id']}"
            else:
                source = f"ytsearch1:{source}"
        elif mode == "score":
            picked = _ytsearch_pick_best(
                cookies_dir=cookies_dir,
                query=source,
                max_duration_s=max_duration_s,
                search_n=10,
                exclude_ids=exclude_ids,
            )
            source = picked["webpage_url"]
        else:
            # Prefer search result #1, but if it fails to download (SABR/EJS/PO-token),
            # fall back to next results to keep the API stable.
            fallback_urls = _ytsearch_list(cookies_dir=cookies_dir, query=source, search_n=10, exclude_ids=exclude_ids)
            if not fallback_urls:
                raise RuntimeError(f"No YouTube results for query='{source}'")
            source = fallback_urls[0]

    cookie_file = _pick_cookie_file(cookies_dir)
    # Use a local yt-dlp cache to reduce repeated extractor/player work across requests.
    ytdlp_cache_dir = (music_dir.parent / "cache" / "ytdlp").resolve()

    def _list_formats_for(url: str) -> str:
        # Best-effort debug helper. Logs can be long; we keep tail.
        cmd = [_get_ytdlp_bin(), "--list-formats", url]
        if cookie_file:
            cmd.insert(1, "--cookies")
            cmd.insert(2, str(cookie_file))
        proc = subprocess.run(cmd, capture_output=True, text=True)
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        joined = "\n".join([x for x in [out, err] if x])
        return "\n".join(joined.splitlines()[-220:]) if joined else ""

    def _format_candidates() -> list[str]:
        """
        Return a list of format selectors to try, in order.
        We always keep audio-only (never `best`, which may include video).
        """
        abr = max(32, min(int(max_abr_kbps), 160))
        base_small = f"bestaudio[abr<={abr}]/bestaudio" if prefer_abr_128 else "bestaudio"
        base_any = "bestaudio"

        m = (audio_mode or "mp3").strip().lower()
        # Speed/stability: try m4a (itag 140 commonly) first when available since it often works even when bestaudio fails
        # Prefer smallest m4a first (itag 139 ~48kbps, then 140 ~128kbps) for fastest download
        m4a_first = "139/140/bestaudio[ext=m4a]/bestaudio"
        if prefer_abr_128:
            # Prioritize smallest audio: try m4a <=abr first (itag 139), fallback to any m4a
            m4a_first = f"bestaudio[ext=m4a][abr<={abr}]/139/140/bestaudio[ext=m4a]/bestaudio"
        if not prefer_itag_140:
            m4a_first = "bestaudio[ext=m4a]/bestaudio"
            if prefer_abr_128:
                m4a_first = f"bestaudio[ext=m4a][abr<={abr}]/bestaudio[ext=m4a]/bestaudio"
        if m in ("mp4_1080", "1080p"):
            return [
                "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=1080]+bestaudio[ext=m4a]/bestvideo[height<=1080]+140/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            ]
        if m in ("mp4", "mp4_720", "720p", "video"):
            return [
                "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio[ext=m4a]/bestvideo[height<=720]+140/bestvideo[height<=720]+bestaudio/best[height<=720]/best",
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            ]
        if m in ("mp4_480", "480p"):
            return [
                "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=480]+bestaudio[ext=m4a]/bestvideo[height<=480]+140/bestvideo[height<=480]+bestaudio/best[height<=480]/best",
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            ]
        if m in ("mp4_360", "360p"):
            return [
                "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=360]+bestaudio[ext=m4a]/bestvideo[height<=360]+140/bestvideo[height<=360]+bestaudio/best[height<=360]/best",
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio[ext=m4a]/bestvideo+bestaudio/best",
            ]
        if m in ("wav", "flac"):
            return [f"{base_any}/18/best", f"{m4a_first}/18/best"]
        if m in ("mp3", "mp3_320", "mp3_128"):
            return [f"{base_any}/18/best", f"{m4a_first}/18/best"]
        if m == "m4a":
            # Prefer m4a audio-only, but fall back to any audio-only or format 18 if unavailable
            return [
                f"{m4a_first}/18/best",
                f"{base_small}/18/best",
                f"{base_any}/18/best",
            ]
        if m == "opus":
            if prefer_abr_128:
                return [
                    "249/250/bestaudio[acodec=opus][abr<=64]/bestaudio[acodec=opus]/bestaudio[ext=m4a]/bestaudio",
                    m4a_first,
                    base_small,
                    base_any,
                ]
            else:
                return [
                    "251/bestaudio[acodec=opus]/250/249/bestaudio[ext=m4a]/bestaudio",
                    base_any,
                    m4a_first,
                ]
        # mp3/auto mode: download audio-only then (optionally) transcode
        return [m4a_first, base_small, base_any]

    def _video_fallback_formats() -> list[str]:
        # Last resort: download any available format (including HLS streams) and extract audio with ffmpeg.
        # "best" will match any format including HLS m3u8 streams.
        return [
            "best",  # Match any available format including HLS
        ]

    def _build_cmd_with_client(
        fmt: str,
        player_client: str | None,
        use_cookies: bool,
        *,
        force_mp3: bool,
        relax_retries: bool = False,
        skip_hls: bool = True,
    ) -> list[str]:
        cmd = [
            _get_ytdlp_bin(),
        ]
        if _FFMPEG_PATH:
            cmd += ["--ffmpeg-location", _FFMPEG_PATH]
        cmd += [
            "--force-overwrites",
            "--no-continue",
            "--no-playlist",
            "--no-mtime",
        ]
        mode_str = (audio_mode or "").strip().lower()
        is_video_mode = mode_str.startswith("mp4") or mode_str.startswith("video") or mode_str.endswith("p")
        if max_duration_s and max_duration_s > 0 and not is_video_mode and not _extract_id_from_url_or_id(source):
            cmd += ["--match-filter", f"duration < {int(max_duration_s)}"]
        cmd += [
            # Try different YouTube clients to improve success rate when signature/EJS challenges occur.
            # (Added via --extractor-args when player_client is not None)
            "-f",
            fmt,
            "--concurrent-fragments",
            str(max(16, concurrent_fragments)),
            "--http-chunk-size",
            "10M",
            "--buffer-size",
            "4096k",
            "--socket-timeout",
            "60",
            "--geo-bypass",
            "--cache-dir",
            str(ytdlp_cache_dir),
            "--paths",
            str(music_dir),
            "-o",
            "%(title).200B [%(id)s].%(ext)s",
            "--print-json",
            source,
        ]
        if skip_hls:
            # Speed: we only want audio-only DASH formats, never HLS streams.
            # Skipping HLS removes the ~2s "Downloading m3u8 information" step.
            # Video fallback (relax_retries) calls with skip_hls=False so HLS stays available there.
            cmd.insert(1, "--extractor-args")
            cmd.insert(2, "youtube:skip=hls")
        if not force_mp3 and (audio_mode or "").strip().lower() == "m4a":
            # Speed: 140/m4a downloaded from YouTube is already a valid m4a
            # (ffprobe-verified: same duration/size with and without fixup).
            # Skipping the ffmpeg FixupM4a remux saves ~1-2s per download.
            cmd.insert(1, "--fixup")
            cmd.insert(2, "never")
        if not ytdlp_verbose:
            # Avoid flooding stderr with warnings like GetPOT noise; we keep errors via return code anyway.
            cmd.insert(1, "--no-warnings")
        if fast_mode and not relax_retries:
            # Reduce time spent waiting on retries; better to fail fast and/or try next search result.
            # These flags significantly reduce "hang" time on throttled routes.
            # But keep extractor retries higher to handle 403 Forbidden from YouTube
            cmd.insert(1, "--retries")
            cmd.insert(2, "1")
            cmd.insert(3, "--fragment-retries")
            cmd.insert(4, "1")
            cmd.insert(5, "--extractor-retries")
            cmd.insert(6, "3")  # Increased from 1 to handle 403 errors
            cmd.insert(7, "--file-access-retries")
            cmd.insert(8, "1")
            cmd.insert(9, "--retry-sleep")
            cmd.insert(10, "2")  # Sleep 2s between retries to avoid 403
            cmd.insert(11, "--socket-timeout")
            cmd.insert(12, "10")
            cmd.insert(13, "--no-check-certificates")
            cmd.insert(14, "--throttled-rate")
            cmd.insert(15, "100K")
        elif relax_retries:
            # For video fallback (HLS), use more relaxed settings: need more retries for fragment downloads.
            cmd.insert(1, "--retries")
            cmd.insert(2, "3")
            cmd.insert(3, "--fragment-retries")
            cmd.insert(4, "3")
            cmd.insert(5, "--extractor-retries")
            cmd.insert(6, "2")
            cmd.insert(7, "--file-access-retries")
            cmd.insert(8, "2")
            cmd.insert(9, "--retry-sleep")
            cmd.insert(10, "1")
            cmd.insert(11, "--socket-timeout")
            cmd.insert(12, "30")
            cmd.insert(13, "--no-check-certificates")
        # Force JS runtime to help with EJS/n challenges (e.g. deno).
        runtime = ytdlp_js_runtimes
        if not runtime:
            runtime = _detect_js_engine()
        elif ytdlp_js_runtimes_path:
            runtime = f"{runtime}:{ytdlp_js_runtimes_path}"
        if runtime:
            cmd.insert(1, "--js-runtimes")
            cmd.insert(2, runtime)
        if "--remote-components" not in cmd:
            cmd.insert(1, "--remote-components")
            cmd.insert(2, "ejs:github")
        if "--no-check-certificates" not in cmd:
            cmd.insert(1, "--no-check-certificates")
        client_to_use = player_client or ytdlp_player_client
        if client_to_use:
            cmd.insert(1, "--extractor-args")
            cmd.insert(2, f"youtube:player_client={client_to_use}")
        mode_str = (audio_mode or "").strip().lower()
        if mode_str.startswith("mp4") or mode_str.startswith("video") or mode_str.endswith("p"):
            cmd.insert(1, "--merge-output-format")
            cmd.insert(2, "mp4")
        if force_mp3:
            cmd.insert(1, "--extract-audio")
            cmd.insert(2, "--audio-format")
            cmd.insert(3, "mp3")
            cmd.insert(4, "--postprocessor-args")
            cmd.insert(5, f"ffmpeg:-b:a {audio_bitrate}")
        elif not is_video:
            cmd.insert(1, "--extract-audio")
            cmd.insert(2, "--audio-format")
            cmd.insert(3, "m4a")
        if ytdlp_verbose:
            cmd.insert(1, "--verbose")
        if use_cookies and cookie_file and (player_client not in ("visionos", "android_vr")):
            cmd.insert(1, "--cookies")
            cmd.insert(2, str(cookie_file))
        return cmd

    def _try_download_current_source(*, fmt_list: list[str], force_mp3: bool, relax_retries: bool = False, skip_hls: bool = True) -> tuple[str, str, list[str] | None, int | None]:
        last_stdout = ""
        last_stderr = ""
        last_cmd: list[str] | None = None
        last_rc: int | None = None

        # In FAST_MODE: do not iterate across many clients. Keep overhead minimal.
        if cookie_file:
            player_clients: list[tuple[str | None, bool]] = [
                ("tv_samsung", True),
                (None, True),
                ("web", True),
            ]
        elif fast_mode:
            player_clients = [
                ("android", False),
                ("visionos", False),
                ("tv_samsung", True),
                (None, True),
            ]
        else:
            player_clients = [
                ("android", False),
                ("visionos", False),
                ("tv_samsung", True),
                ("web", True),
                ("tv", True),
            ]
        attempt = 0
        for fmt_try in fmt_list:
            for client, use_cookies in player_clients:
                attempt += 1
                cmd = _build_cmd_with_client(fmt_try, client, use_cookies, force_mp3=force_mp3, relax_retries=relax_retries, skip_hls=skip_hls)
                last_cmd = cmd
                logger.info(
                    "yt-dlp start | try=%s fmt=%r client=%r cookies=%s source=%r",
                    attempt,
                    fmt_try,
                    client,
                    use_cookies,
                    source,
                )
                t_cmd0 = time.perf_counter()
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    mode_str = (audio_mode or "").strip().lower()
                    is_vid = mode_str.startswith("mp4") or mode_str.startswith("video") or mode_str.endswith("p")
                    timeout_limit = 1800.0 if is_vid else 900.0
                    t_end = time.perf_counter() + timeout_limit
                    timed_out = False
                    while proc.poll() is None:
                        if ev.is_set():
                            proc.kill()
                            try:
                                proc.wait(timeout=2)
                            except Exception:
                                pass
                            logger.info("yt-dlp killed due to cancel signal for %s", target_id or source)
                            raise RuntimeError("Tiến trình tải đã được người dùng dừng lại.")
                        if time.perf_counter() > t_end:
                            proc.kill()
                            timed_out = True
                            break
                        time.sleep(0.08)

                    if timed_out:
                        logger.warning("yt-dlp timeout after %ss, killing process", int(timeout_limit))
                        last_stdout, last_stderr, last_rc = "", "yt-dlp timeout", 1
                        last_cmd = cmd
                        t_cmd_s = time.perf_counter() - t_cmd0
                        logger.info("yt-dlp done | try=%s rc=%s wall=%.2fs", attempt, last_rc, t_cmd_s)
                        continue

                    stdout, stderr = proc.communicate()
                    last_stdout = (stdout or "").strip()
                    last_stderr = (stderr or "").strip()
                    last_rc = proc.returncode
                except RuntimeError as re_err:
                    if "dừng lại" in str(re_err):
                        raise
                    continue
                t_cmd_s = time.perf_counter() - t_cmd0
                logger.info("yt-dlp done | try=%s rc=%s wall=%.2fs", attempt, last_rc, t_cmd_s)

                if stderr:
                    # yt-dlp may emit a lot of "[GetPOT] Failed to fetch ... No request handlers configured" noise.
                    # It doesn't usually indicate a failure, but it bloats logs and adds overhead.
                    tail_lines = stderr.splitlines()[-200:]
                    if not ytdlp_verbose:
                        filtered = [ln for ln in tail_lines if "[GetPOT]" not in ln]
                        dropped = len(tail_lines) - len(filtered)
                        if dropped:
                            logger.warning("yt-dlp stderr: (filtered %s GetPOT lines)", dropped)
                        tail_lines = filtered
                    if tail_lines:
                        logger.warning("yt-dlp stderr (tail):\n%s", "\n".join(tail_lines))

                if proc.returncode == 0:
                    return last_stdout, last_stderr, last_cmd, last_rc
        return last_stdout, last_stderr, last_cmd, last_rc

    def _has_json_output(stdout: str) -> bool:
        """Check if stdout contains valid JSON line from yt-dlp."""
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                return True
        return False

    mode = (audio_mode or "mp3").strip().lower()
    is_video = mode.startswith("mp4") or mode.startswith("video") or mode.endswith("p")
    merge_fmt = "mp4" if is_video else None
    t_codec = None
    if mode in ("mp3", "mp3_320", "mp3_128"):
        t_codec = "mp3"
    elif mode in ("wav", "flac"):
        t_codec = mode

    # High-speed in-process fast path (like major web converters, takes ~2-4s directly in RAM)
    if fast_mode and "://" in source:
        try:
            fmt_candidates = _format_candidates()
            best_fmt = fmt_candidates[0] if fmt_candidates else "bestaudio"
            # When cookies are present, client MUST support cookies (tv_samsung or web).
            # visionos/android/android_vr explicitly ignore cookies and trigger bot blocks on datacenters.
            if cookie_file:
                if not ytdlp_player_client or ytdlp_player_client in ("visionos", "android_vr", "android", "ios"):
                    primary_client = "tv_samsung"
                else:
                    primary_client = ytdlp_player_client
            else:
                primary_client = ytdlp_player_client or "android"

            fast_res = _fast_inprocess_download(
                source_url=source,
                music_dir=music_dir,
                cookie_file=cookie_file,
                format_spec=best_fmt,
                player_client=primary_client,
                concurrent_fragments=concurrent_fragments,
                ytdlp_cache_dir=ytdlp_cache_dir,
                ytdlp_verbose=ytdlp_verbose,
                force_mp3=(t_codec == "mp3"),
                audio_bitrate=audio_bitrate,
                target_codec=t_codec,
                merge_output_format=merge_fmt,
            )
            # Fallback to the other client if the first fails
            if not fast_res:
                alt_client = "android" if primary_client == "tv_samsung" else "tv_samsung"
                fast_res = _fast_inprocess_download(
                    source_url=source,
                    music_dir=music_dir,
                    cookie_file=cookie_file if alt_client != "android" else None,
                    format_spec=best_fmt,
                    player_client=alt_client,
                    concurrent_fragments=concurrent_fragments,
                    ytdlp_cache_dir=ytdlp_cache_dir,
                    ytdlp_verbose=ytdlp_verbose,
                    force_mp3=(t_codec == "mp3"),
                    audio_bitrate=audio_bitrate,
                    target_codec=t_codec,
                    merge_output_format=merge_fmt,
                )
            if fast_res:
                logger.info("fast in-process download succeeded | id=%s title=%r path=%s", fast_res.file_id, fast_res.title, fast_res.path)
                return fast_res
        except Exception as e:
            logger.warning("fast in-process download failed, falling back to subprocess: %s", e)

    # Keep only the working, simple modes: auto / mp3 / m4a / opus
    if mode == "auto":
        last_stdout, last_stderr, last_cmd, last_rc = _try_download_current_source(
            fmt_list=_format_candidates(),
            force_mp3=False,
        )
        # Treat "rc=0 but no JSON output" as failure (format not available).
        # This happens when yt-dlp silently fails due to format unavailability with certain retry settings.
        if last_rc != 0 or not _has_json_output(last_stdout):
            if last_rc == 0 and not _has_json_output(last_stdout):
                logger.warning("audio-only formats not available (no output from yt-dlp); falling back to video download + mp3 extract")
            else:
                logger.warning("audio-only not available; falling back to video download + mp3 extract")
            last_stdout, last_stderr, last_cmd, last_rc = _try_download_current_source(
                fmt_list=_video_fallback_formats(),
                force_mp3=True,
                relax_retries=True,
                skip_hls=False,
            )
    else:
        last_stdout, last_stderr, last_cmd, last_rc = _try_download_current_source(
            fmt_list=_format_candidates(),
            force_mp3=(mode == "mp3"),
        )
        if last_rc != 0 or not _has_json_output(last_stdout):
            logger.warning("specific formats failed, falling back to best video/audio format")
            last_stdout, last_stderr, last_cmd, last_rc = _try_download_current_source(
                fmt_list=_video_fallback_formats(),
                force_mp3=(mode in ("mp3", "mp3_320", "mp3_128", "m4a")),
                relax_retries=True,
                skip_hls=False,
            )
    if (last_rc != 0 or not _has_json_output(last_stdout)) and fallback_urls:
        for alt in fallback_urls[1:]:
            logger.warning("yt-dlp failed for first result; trying next search result: %s", alt)
            source = alt
            if mode == "auto":
                last_stdout, last_stderr, last_cmd, last_rc = _try_download_current_source(
                    fmt_list=_format_candidates(),
                    force_mp3=False,
                )
                if last_rc != 0 or not _has_json_output(last_stdout):
                    logger.warning("audio-only not available; falling back to video download + mp3 extract")
                    last_stdout, last_stderr, last_cmd, last_rc = _try_download_current_source(
                        fmt_list=_video_fallback_formats(),
                        force_mp3=True,
                        relax_retries=True,
                        skip_hls=False,
                    )
            else:
                last_stdout, last_stderr, last_cmd, last_rc = _try_download_current_source(
                    fmt_list=_format_candidates(),
                    force_mp3=(mode == "mp3"),
                )
            if last_rc == 0:
                break

    if last_rc != 0:
        logger.error("yt-dlp failed | rc=%s", last_rc)
        if last_cmd:
            logger.error("yt-dlp cmd: %s", " ".join(last_cmd))
        if last_stdout:
            logger.error("yt-dlp stdout (tail):\n%s", "\n".join(last_stdout.splitlines()[-60:]))
        if last_stderr:
            logger.error("yt-dlp stderr (tail):\n%s", "\n".join(last_stderr.splitlines()[-200:]))

        # If format selection fails, dump `--list-formats` to help debug what is actually available.
        st_lower = (last_stderr or "").lower()
        if ytdlp_debug_list_formats and ("requested format is not available" in st_lower or "only images are available" in st_lower):
            lf = _list_formats_for(source)
            if lf:
                logger.error("yt-dlp --list-formats (tail) for %s:\n%s", source, lf)
        # More descriptive error message
        error_msg = f"yt-dlp failed: {last_stderr[:1200]}" if last_stderr else "yt-dlp failed: no output"
        if "timeout" in error_msg.lower():
            error_msg = "Download timeout - server or network too slow"
        raise RuntimeError(error_msg)

    # yt-dlp --print-json prints one JSON line per downloaded entry.
    last_json_line = None
    for line in last_stdout.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            last_json_line = line
    if not last_json_line:
        raise RuntimeError("yt-dlp did not return JSON metadata")

    info = json.loads(last_json_line)
    file_id = str(info.get("id") or "").strip()
    title = str(info.get("title") or file_id).strip()
    webpage_url = str(info.get("webpage_url") or query_or_url).strip()
    duration_s = info.get("duration")
    try:
        duration_s = int(duration_s) if duration_s is not None else None
    except (TypeError, ValueError):
        duration_s = None

    # Find produced media file.
    # Prefer exact filepath returned by yt-dlp to avoid scanning large music directories.
    media_path: Path | None = None
    for k in ("filepath", "_filename"):
        v = info.get(k)
        if isinstance(v, str) and v.strip():
            p = Path(v.strip())
            if p.exists() and p.is_file():
                media_path = p
                break
    if media_path is None:
        rd = info.get("requested_downloads")
        if isinstance(rd, list) and rd:
            fp = rd[0].get("filepath") if isinstance(rd[0], dict) else None
            if isinstance(fp, str) and fp.strip():
                p = Path(fp.strip())
                if p.exists() and p.is_file():
                    media_path = p

    if media_path is None:
        # Fallback: locate by literal "[id]" in filename (slower on huge dirs, but reliable).
        pattern = f"*[[]{file_id}[]]*"
        candidates = sorted(music_dir.glob(pattern))
        candidates = [p for p in candidates if p.is_file()]
        if not candidates:
            candidates = sorted(music_dir.glob(f"{file_id}.*"))
            candidates = [p for p in candidates if p.is_file()]
        if not candidates:
            raise RuntimeError(f"Downloaded file not found for id={file_id}")
        media_path = candidates[0]

    if target_id and target_id in _ACTIVE_CANCELLATION_EVENTS:
        _ACTIVE_CANCELLATION_EVENTS.pop(target_id, None)

    return DownloadResult(
        file_id=file_id,
        title=title,
        path=media_path,
        source_url=webpage_url,
        duration_s=duration_s,
    )


