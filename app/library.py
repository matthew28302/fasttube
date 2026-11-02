from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

SUPPORTED_EXTS = {".mp3", ".m4a", ".mp4", ".opus", ".webm", ".aac", ".wav", ".flac"}

_STOPWORDS = {
    # common Vietnamese particles
    "va",
    "và",
    "la",
    "là",
    "mot",
    "một",
    "nhac",
    "nhạc",
    "bai",
    "bài",
    "official",
    "mv",
    "music",
    "video",
    "lyrics",
    "lyric",
    # Removed: "of" and "you" as they're useful for English song matching
}


def _norm(s: str) -> str:
    s = s.strip().lower()
    # Remove Vietnamese/Unicode diacritics by decomposing, then dropping combining marks.
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # Normalize separators
    s = re.sub(r"[\s\-_]+", " ", s)
    # Keep ascii alnum and spaces after de-accenting
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s.strip()


def _tokens(s: str) -> list[str]:
    parts = [p for p in _norm(s).split() if p and p not in _STOPWORDS]
    return parts


@dataclass(frozen=True)
class LibraryHit:
    file_id: str
    path: Path
    title: str


_ID_IN_BRACKETS_RE = re.compile(r"\[([A-Za-z0-9_-]{6,32})\]")


def _extract_id_from_filename(p: Path) -> Optional[str]:
    m = _ID_IN_BRACKETS_RE.search(p.stem)
    if m:
        return m.group(1)
    # fallback: legacy "id.ext"
    s = p.stem.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{6,32}", s):
        return s
    return None


def _title_from_filename(p: Path, file_id: str) -> str:
    # "%(title) [%(id)s].ext" -> strip the trailing " [id]"
    stem = p.stem
    tail = f" [{file_id}]"
    if stem.endswith(tail):
        return stem[: -len(tail)].strip()
    return stem


# In-memory cache to avoid per-request disk scans (important on large music_dir).
_CACHE_TTL_S = 2.0
_cache: dict[str, object] = {
    "t": 0.0,
    "dir_mtime": 0.0,
    "by_id": {},      # type: ignore[dict-unknown]
    "entries": [],    # type: ignore[list-unknown]
}


def _refresh_index(music_dir: Path) -> None:
    by_id: dict[str, LibraryHit] = {}
    entries: list[tuple[str, str, str]] = []  # (id, title, norm_tokens_str)
    files = sorted(iter_audio_files(music_dir), key=lambda p: p.stat().st_mtime)
    for p in files:
        fid = _extract_id_from_filename(p)
        if not fid:
            continue
        title = _title_from_filename(p, fid)
        hit = LibraryHit(file_id=fid, path=p, title=title)
        by_id[fid] = hit
        tt = " ".join(_tokens(title))
        if tt:
            entries.append((fid, title, tt))
    _cache["by_id"] = by_id
    _cache["entries"] = entries


def _get_index(music_dir: Path) -> tuple[dict[str, LibraryHit], list[tuple[str, str, str]]]:
    now = time.time()
    try:
        dir_mtime = music_dir.stat().st_mtime if music_dir.exists() else 0.0
    except Exception:
        dir_mtime = 0.0
    if (now - float(_cache["t"])) > _CACHE_TTL_S or float(_cache["dir_mtime"]) != dir_mtime:
        _cache["t"] = now
        _cache["dir_mtime"] = dir_mtime
        _refresh_index(music_dir)
    return _cache["by_id"], _cache["entries"]  # type: ignore[return-value]


DEFAULT_MAX_STORAGE_BYTES = int(os.getenv("MAX_STORAGE_MB", "10240")) * 1024 * 1024


def enforce_library_limit(
    music_dir: Path,
    max_bytes: int = DEFAULT_MAX_STORAGE_BYTES,
    exclude_id: Optional[str] = None,
) -> None:
    """Keep total library storage <= max_bytes (default 10GB).
    Purges stale temporary files and evicts oldest media files when quota is exceeded.
    """
    if not music_dir.exists():
        return

    now = time.time()
    # Purge only stale orphan temporary files older than 30 minutes.
    # NEVER delete actively downloading .part files!
    for p in music_dir.rglob("*"):
        if p.is_file() and (p.name.endswith(".part") or ".temp." in p.name or p.name.endswith(".ytdl")):
            if exclude_id and exclude_id in p.name:
                continue
            try:
                if (now - p.stat().st_mtime) > 1800:
                    p.unlink()
            except Exception:
                pass

    files = [p for p in music_dir.rglob("*") if p.is_file() and p.name != ".gitkeep"]
    total = sum(p.stat().st_size for p in files if p.exists())
    if total <= max_bytes:
        return

    logger.warning("Storage quota exceeded (%.2f MB > %.2f MB). Evicting oldest media files...", total / (1024 * 1024), max_bytes / (1024 * 1024))
    # Sort files by last modification time (oldest first)
    files.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    for p in files:
        if total <= max_bytes:
            break
        if exclude_id and exclude_id in p.name:
            continue
        try:
            sz = p.stat().st_size
            p.unlink()
            total -= sz
            logger.info("library purge: deleted %s (%.2f MB)", p.name, sz / (1024 * 1024))
        except Exception as e:
            logger.warning("library purge failed for %s: %s", p.name, e)

    # Invalidate memory cache
    _cache["by_id"] = {}
    _cache["entries"] = []
    _cache["t"] = 0.0


def iter_audio_files(music_dir: Path) -> Iterable[Path]:
    if not music_dir.exists():
        return []
    return (
        p for p in music_dir.rglob("*")
        if p.is_file()
        and p.suffix.lower() in SUPPORTED_EXTS
        and not p.name.endswith(".part")
        and not p.name.endswith(".ytdl")
        and ".temp." not in p.name
        and not p.name.startswith(".")
    )


def _iter_info_json(music_dir: Path) -> Iterable[Path]:
    if not music_dir.exists():
        return []
    return (p for p in music_dir.iterdir() if p.is_file() and p.name.endswith(".info.json"))


def _load_info(p: Path) -> Optional[dict]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _find_media_for_id(music_dir: Path, file_id: str) -> Optional[Path]:
    pattern = f"*[[]{file_id}[]]*"
    candidates = [p for p in music_dir.rglob(pattern) if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    for p in iter_audio_files(music_dir):
        if p.stem == file_id:
            return p
    return None

def find_all_by_file_id(music_dir: Path, file_id: str) -> list[LibraryHit]:
    file_id = (file_id or "").strip()
    if not file_id or not music_dir.exists():
        return []
    pattern = f"*[[]{file_id}[]]*"
    hits: list[LibraryHit] = []
    seen_paths = set()
    for p in music_dir.rglob(pattern):
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS and p not in seen_paths:
            seen_paths.add(p)
            hits.append(LibraryHit(file_id=file_id, path=p, title=_title_from_filename(p, file_id)))
    for ext in SUPPORTED_EXTS:
        for p in music_dir.rglob(f"{file_id}{ext}"):
            if p.is_file() and p not in seen_paths:
                seen_paths.add(p)
                hits.append(LibraryHit(file_id=file_id, path=p, title=_title_from_filename(p, file_id)))
    return hits

def find_by_file_id(music_dir: Path, file_id: str, ext: Optional[str] = None) -> Optional[LibraryHit]:
    file_id = (file_id or "").strip()
    if not file_id or not music_dir.exists():
        return None
    target_ext = f".{ext.lower().lstrip('.')}" if ext else None

    if target_ext:
        pattern = f"*[[]{file_id}[]]*"
        candidates = [p for p in music_dir.rglob(pattern) if p.is_file() and p.suffix.lower() == target_ext]
        if not candidates:
            for p in music_dir.rglob(f"{file_id}{target_ext}"):
                if p.is_file():
                    candidates.append(p)
        if candidates:
            best = max(candidates, key=lambda p: p.stat().st_mtime)
            return LibraryHit(file_id=file_id, path=best, title=_title_from_filename(best, file_id))
        return None

    by_id, _ = _get_index(music_dir)
    hit = by_id.get(file_id)
    if hit and hit.path.exists():
        return hit
    hits = find_all_by_file_id(music_dir, file_id)
    if hits:
        return max(hits, key=lambda h: h.path.stat().st_mtime)
    return None


def find_by_query(music_dir: Path, query: str) -> Optional[LibraryHit]:
    qn = _norm(query)
    if not qn:
        return None
    q_tokens = _tokens(query)
    
    # More lenient token check: allow very short queries or single character tokens
    # if query is short (e.g., "of you" should work)
    if q_tokens and len(query.strip()) < 3:
        # Too short, return None
        return None

    by_id, entries = _get_index(music_dir)
    qs = " ".join(q_tokens) if q_tokens else qn
    
    best: tuple[float, str, str] | None = None  # (score, id, title)
    for vid, title, ts in entries:
        # Use normalized query string for matching if no tokens available
        if not q_tokens:
            # Fallback: use raw normalized comparison
            ratio = fuzz.token_set_ratio(qn, _norm(title)) / 100.0
        else:
            ratio = fuzz.token_set_ratio(qs, ts) / 100.0
        
        inter = len(set(q_tokens) & set(ts.split())) if q_tokens else 0
        union = len(set(q_tokens) | set(ts.split())) if q_tokens else 1
        jacc = inter / union if union > 0 else 0
        
        # If there's NO token intersection, penalize heavily
        # This prevents "lac troi" from matching "viet nam toi"
        if q_tokens and inter == 0:
            # No matching tokens - require very high fuzzy score
            score = 0.8 * ratio - 0.2  # Can't exceed 0.8*1.0 - 0.2 = 0.8
        else:
            # Has token overlap - can use higher score
            score = 0.7 * ratio + 0.3 * jacc
        
        if best is None or score > best[0]:
            best = (score, vid, title)
    
    # First threshold: good fuzzy match
    # Increased from 0.55 to 0.65 for better safety
    if best and best[0] >= 0.65:
        _, vid, title = best
        hit = by_id.get(vid)
        if hit:
            return LibraryHit(file_id=vid, path=hit.path, title=title)
    
    # Second attempt: fuzzy match on individual tokens against title tokens
    # This helps with typos like "pround" -> "proud"
    if q_tokens:
        best_token_match: tuple[float, str, str] | None = None
        for vid, title, ts in entries:
            title_tokens = ts.split()
            token_scores = []
            for q_tok in q_tokens:
                # Find best matching token in title
                # Require exact match, very high similarity, or 4+ chars with 92%+ similarity
                best_tok_score = 0.0
                for t_tok in title_tokens:
                    score = fuzz.ratio(q_tok, t_tok) / 100.0
                    # Stricter: need 92%+ match if token is 4+ chars, 95%+ if shorter
                    if len(q_tok) >= 4:
                        if score >= 0.92:
                            best_tok_score = max(best_tok_score, score)
                    elif score >= 0.95:
                        best_tok_score = max(best_tok_score, score)
                token_scores.append(best_tok_score)
            
            # Average match score of all query tokens
            if token_scores:
                avg_score = sum(token_scores) / len(token_scores)
                # Boost score if many tokens match
                match_count = sum(1 for s in token_scores if s > 0.7)
                if match_count > 0:
                    avg_score = avg_score * 0.8 + (match_count / len(q_tokens)) * 0.2
                
                if best_token_match is None or avg_score > best_token_match[0]:
                    best_token_match = (avg_score, vid, title)
        
        # If we found a token-level match above threshold, use it
        # Also increased threshold from 0.65 to 0.75 for safety
        if best_token_match and best_token_match[0] >= 0.75:
            _, vid, title = best_token_match
            hit = by_id.get(vid)
            if hit:
                return LibraryHit(file_id=vid, path=hit.path, title=title)

    # Fallback: match filenames (non-yt-dlp files)
    best_file: tuple[int, Path] | None = None
    for p in iter_audio_files(music_dir):
        name = _norm(p.stem)
        if not name:
            continue
        if qn in name:
            score = len(qn)
        elif name in qn:
            score = len(name)
        else:
            continue
        if best_file is None or score > best_file[0]:
            best_file = (score, p)
    if not best_file:
        return None
    p = best_file[1]
    return LibraryHit(file_id=p.stem, path=p, title=p.stem)
