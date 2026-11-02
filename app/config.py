from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def project_root() -> Path:
    # Thư mục gốc của project = thư mục cha của thư mục app/.
    # Cho phép ghi đè bằng biến môi trường PROJECT_ROOT khi deploy ở vị trí khác.
    override = os.getenv("PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return Path(__file__).resolve().parent.parent


def _resolve_path(value: str | None, default: str) -> Path:
    # Đường dẫn tuyệt đối thì giữ nguyên.
    # Đường dẫn tương đối (kể cả ./music, music, logs/app.log) luôn hiểu là
    # tương đối so với thư mục gốc project, KHÔNG phải thư mục đang chạy lệnh.
    # Nhờ vậy sang máy mới không cần sửa tay đường dẫn trong .env nữa:
    # cứ để trống hoặc để dạng tương đối là chạy.
    raw = (value or "").strip()
    if not raw:
        return (project_root() / default).resolve()
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (project_root() / path).resolve()


@dataclass(frozen=True)
class Settings:
    project_root: Path
    music_dir: Path
    cookies_dir: Path
    base_url: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    ytdlp_concurrent_fragments: int
    max_video_duration_s: int
    run_host: str
    run_port: int
    log_level: str
    log_file: Path
    audio_bitrate: str
    audio_mode: str
    prefer_abr_128: bool
    ytdlp_js_runtimes: str | None
    ytdlp_js_runtimes_path: str | None
    fast_mode: bool
    ytdlp_player_client: str | None
    max_abr_kbps: int
    youtube_search_mode: str
    ytdlp_verbose: bool
    prefer_itag_140: bool
    ytdlp_debug_list_formats: bool
    max_storage_mb: int
    max_file_age_hours: int
    access_password: str


def load_settings() -> Settings:
    # Đọc .env ở thư mục gốc project, không phụ thuộc thư mục đang chạy lệnh.
    # Biến môi trường thật (export trong shell / systemd) vẫn ưu tiên hơn .env
    # vì override=False.
    load_dotenv(dotenv_path=project_root() / ".env", override=False)

    music_dir = _resolve_path(os.getenv("MUSIC_DIR"), "music")
    cookies_dir = _resolve_path(os.getenv("COOKIES_DIR"), "cookies")
    base_url = os.getenv("BASE_URL") or None
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN") or None
    telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID") or None
    run_host = os.getenv("RUN_HOST", "0.0.0.0")
    try:
        run_port = int(os.getenv("SERVER_PORT") or os.getenv("PORT") or os.getenv("RUN_PORT", "8000"))
    except ValueError:
        run_port = 8000

    try:
        ytdlp_concurrent_fragments = int(os.getenv("YTDLP_CONCURRENT_FRAGMENTS", "16"))
    except ValueError:
        ytdlp_concurrent_fragments = 16

    try:
        max_video_duration_s = int(os.getenv("MAX_VIDEO_DURATION_S", "86400"))
    except ValueError:
        max_video_duration_s = 86400

    log_level = (os.getenv("LOG_LEVEL", "INFO") or "INFO").strip().upper()
    log_file = _resolve_path(os.getenv("LOG_FILE"), "logs/app.log")
    audio_bitrate = (os.getenv("AUDIO_BITRATE", "128k") or "128k").strip()
    audio_mode = (os.getenv("AUDIO_MODE", "m4a") or "m4a").strip().lower()
    if audio_mode not in {"auto", "mp3", "m4a", "opus"}:
        audio_mode = "m4a"
    prefer_abr_128 = (os.getenv("PREFER_ABR_128", "1") or "1").strip().lower() in {"1", "true", "yes", "y"}
    ytdlp_js_runtimes = (os.getenv("YTDLP_JS_RUNTIMES") or "").strip() or None
    ytdlp_js_runtimes_path = (os.getenv("YTDLP_JS_RUNTIMES_PATH") or "").strip() or None
    fast_mode = (os.getenv("FAST_MODE", "1") or "1").strip().lower() in {"1", "true", "yes", "y"}
    ytdlp_player_client = (os.getenv("YTDLP_PLAYER_CLIENT") or "").strip() or None
    try:
        max_abr_kbps = int(os.getenv("MAX_ABR_KBPS", "96"))
    except ValueError:
        max_abr_kbps = 96

    youtube_search_mode = (os.getenv("YOUTUBE_SEARCH_MODE", "first") or "first").strip().lower()
    ytdlp_verbose = (os.getenv("YTDLP_VERBOSE", "0") or "0").strip().lower() in {"1", "true", "yes", "y"}
    prefer_itag_140 = (os.getenv("PREFER_ITAG_140", "1") or "1").strip().lower() in {"1", "true", "yes", "y"}
    ytdlp_debug_list_formats = (os.getenv("YTDLP_DEBUG_LIST_FORMATS", "0") or "0").strip().lower() in {"1", "true", "yes", "y"}
    try:
        max_storage_mb = int(os.getenv("MAX_STORAGE_MB", "10240"))
    except ValueError:
        max_storage_mb = 10240

    try:
        max_file_age_hours = int(os.getenv("MAX_FILE_AGE_HOURS", "24"))
    except ValueError:
        max_file_age_hours = 24

    access_password = (os.getenv("ACCESS_PASSWORD", "duahau") or "duahau").strip()

    return Settings(
        project_root=project_root(),
        music_dir=music_dir,
        cookies_dir=cookies_dir,
        base_url=base_url,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        ytdlp_concurrent_fragments=ytdlp_concurrent_fragments,
        max_video_duration_s=max_video_duration_s,
        run_host=run_host,
        run_port=run_port,
        log_level=log_level,
        log_file=log_file,
        audio_bitrate=audio_bitrate,
        audio_mode=audio_mode,
        prefer_abr_128=prefer_abr_128,
        ytdlp_js_runtimes=ytdlp_js_runtimes,
        ytdlp_js_runtimes_path=ytdlp_js_runtimes_path,
        fast_mode=fast_mode,
        ytdlp_player_client=ytdlp_player_client,
        max_abr_kbps=max_abr_kbps,
        youtube_search_mode=youtube_search_mode,
        ytdlp_verbose=ytdlp_verbose,
        prefer_itag_140=prefer_itag_140,
        ytdlp_debug_list_formats=ytdlp_debug_list_formats,
        max_storage_mb=max_storage_mb,
        max_file_age_hours=max_file_age_hours,
        access_password=access_password,
    )


