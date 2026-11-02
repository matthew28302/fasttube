from __future__ import annotations

import logging
import re
import unicodedata
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_INNERTUBE_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
_SESSION = requests.Session()


def _norm_title(s: str) -> str:
    s = s.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\[[^\]]+\]|\([^)]+\)", " ", s)
    s = re.sub(r"[\s\-_|/]+", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s.strip()


def _score_title(query: str, title: str, index: int = 0) -> tuple[int, bool]:
    """
    Score video title against search query and determine if it is a valid match.
    Valid match requires the song title (or significant part of it) to be present.
    """
    parts = [p.strip() for p in query.split(" - ", 1)] if " - " in query else [query.strip(), ""]
    song_part = parts[0]
    artist_part = parts[1] if len(parts) > 1 else ""

    norm_song = _norm_title(song_part)
    norm_artist = _norm_title(artist_part)
    norm_title = _norm_title(title)

    song_tokens = set(norm_song.split())
    artist_tokens = set(norm_artist.split()) if norm_artist else set()
    title_tokens = set(norm_title.split())

    if not song_tokens:
        return (0, True)

    matched_song_tokens = song_tokens & title_tokens
    song_match_ratio = len(matched_song_tokens) / len(song_tokens) if song_tokens else 0
    is_valid_match = song_match_ratio >= 0.5 or norm_song in norm_title

    score = 0
    # Exact or substring song match
    if norm_song in norm_title:
        score += 8000 + len(norm_song)
    else:
        score += int(song_match_ratio * 5000)

    # Artist match bonus
    if artist_tokens:
        matched_artist = artist_tokens & title_tokens
        if norm_artist and norm_artist in norm_title:
            score += 2500
        else:
            score += int((len(matched_artist) / len(artist_tokens)) * 1200)

    # Prefer official / mv / music video / audio / lyric strongly
    if any(w in title_tokens for w in ("official", "mv", "video", "music", "audio", "lyric", "lyrics")):
        score += 1500
    if "official" in title_tokens:
        score += 800
    if "mv" in title_tokens:
        score += 800

    # Penalize remix/cover/karaoke/reaction/parody unless requested in query
    for noisy in ("remix", "cover", "karaoke", "reaction", "remake", "parody", "beat", "speedup", "slowed", "mashup"):
        if noisy in title_tokens and noisy not in song_tokens:
            score -= 3500

    # YouTube rank priority: top search results by YouTube algorithm get weight
    score += max(0, 1000 - index * 100)

    # Exact match bonus
    if norm_song == norm_title or (norm_artist and f"{norm_song} {norm_artist}" == norm_title):
        score += 4000

    return (score, is_valid_match)


def _parse_duration(length_text: Optional[str]) -> Optional[int]:
    if not length_text:
        return None
    parts = length_text.strip().split(":")
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return None
    total = 0
    for n in nums:
        total = total * 60 + n
    return total


def _query_innertube_search(query: str, timeout: int = 10, continuation: Optional[str] = None) -> Optional[dict]:
    body: dict = {
        "context": {
            "client": {
                "clientName": "WEB",
                "clientVersion": "2.20240111.09.00",
                "hl": "vi",
                "gl": "VN",
            }
        }
    }
    if continuation:
        body["continuation"] = continuation
    else:
        body["query"] = query

    try:
        r = _SESSION.post(
            f"https://www.youtube.com/youtubei/v1/search?key={_INNERTUBE_KEY}",
            headers={"User-Agent": _UA, "Content-Type": "application/json", "Origin": "https://www.youtube.com"},
            json=body,
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json()
        logger.warning("innertube status %s for query %r", r.status_code, query)
    except Exception as e:
        logger.warning("innertube post failed: %s", e)
    return None


def fast_search_youtube(
    query: str,
    max_duration_s: int = 600,
    timeout: int = 10,
    search_mode: str = "first",
    exclude_ids: Optional[set[str] | list[str]] = None,
) -> Optional[dict]:
    """
    Fast YouTube search via a single innertube search call (~0.8-1.5s).
    Validates that the picked video actually matches the requested song title,
    prioritizes official releases, and respects excluded IDs (avoiding duplicate downloads).
    """
    excluded = set(exclude_ids or [])
    data = _query_innertube_search(query, timeout=timeout)
    
    # Retry once with "Bài hát " prefix if query hits YouTube safety filter (0 items)
    sections = (
        data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
        if data
        else []
    )
    has_videos = False
    for s in sections:
        items = s.get("itemSectionRenderer", {}).get("contents", [])
        if any("videoRenderer" in it for it in items):
            has_videos = True
            break
            
    if not has_videos and not query.lower().startswith("bài hát"):
        data = _query_innertube_search(f"Bài hát {query}", timeout=timeout)
        sections = (
            data.get("contents", {})
            .get("twoColumnSearchResultsRenderer", {})
            .get("primaryContents", {})
            .get("sectionListRenderer", {})
            .get("contents", [])
            if data
            else []
        )

    if not sections:
        return None

    candidates = []
    item_idx = 0
    try:
        for section in sections:
            items = section.get("itemSectionRenderer", {}).get("contents", [])
            for it in items:
                vr = it.get("videoRenderer")
                if not vr:
                    continue
                vid = (vr.get("videoId") or "").strip()
                runs = vr.get("title", {}).get("runs") or []
                title = "".join(run.get("text", "") for run in runs).strip()
                if not vid or not title or vid in excluded:
                    continue
                length = vr.get("lengthText", {}).get("simpleText")
                dur = _parse_duration(length)
                if max_duration_s and max_duration_s > 0 and dur is not None and dur > int(max_duration_s):
                    continue

                score, is_valid = _score_title(query, title, index=item_idx)
                item_idx += 1
                candidates.append({
                    "id": vid,
                    "title": title,
                    "duration": dur,
                    "score": score,
                    "is_valid": is_valid,
                })
            if len(candidates) >= 15:
                break
    except Exception as e:
        logger.warning("fast search parse failed: %s", e)
        return None

    if not candidates:
        return None

    # Priority 1: Valid match with highest score (Official MV, Lyric, Audio matching song)
    # Priority 2: If none is flagged as valid match, fallback to top-scoring non-excluded candidate
    candidates.sort(key=lambda c: (1 if c["is_valid"] else 0, c["score"]), reverse=True)
    best = candidates[0]
    logger.info(
        "fast search match | query=%r picked=%r id=%s is_valid=%s score=%s",
        query,
        best["title"],
        best["id"],
        best["is_valid"],
        best["score"],
    )
    return {"id": best["id"], "title": best["title"], "duration": best["duration"]}


def get_youtube_video_info(url_or_id: str) -> Optional[dict]:
    """
    Get info for a single YouTube video by ID or URL using fast oEmbed (~0.1s).
    """
    vid = url_or_id.strip()
    if "v=" in vid:
        vid = vid.split("v=", 1)[1].split("&", 1)[0]
    elif "youtu.be/" in vid:
        vid = vid.split("youtu.be/", 1)[1].split("?", 1)[0].split("&", 1)[0]
    if not vid:
        return None
    url = f"https://www.youtube.com/watch?v={vid}"
    try:
        r = _SESSION.get(f"https://www.youtube.com/oembed?url={url}&format=json", timeout=5)
        if r.status_code == 200:
            data = r.json()
            return {
                "id": vid,
                "title": data.get("title") or vid,
                "channel": data.get("author_name") or "YouTube",
                "duration": "Video",
                "duration_s": None,
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                "url": url,
            }
    except Exception as e:
        logger.warning("oembed error for %s: %s", vid, e)
    return {
        "id": vid,
        "title": f"YouTube Video ({vid})",
        "channel": "YouTube",
        "duration": "Video",
        "duration_s": None,
        "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "url": url,
    }


def fast_search_youtube_list(
    query: str,
    limit: int = 30,
    max_duration_s: Optional[int] = None,
    timeout: int = 10,
) -> list[dict]:
    """
    Fast YouTube search returning up to `limit` items (default 30) with rich metadata.
    Supports long videos, full movies (Châu Tinh Trì, etc.), podcasts, and multi-page pagination.
    """
    q = query.strip()
    if not q:
        return []
    # If the query is directly a YouTube URL or video ID:
    if "youtube.com/" in q or "youtu.be/" in q or re.fullmatch(r"^[A-Za-z0-9_-]{11}$", q):
        info = get_youtube_video_info(q)
        if info:
            return [info]

    results = []
    seen_ids = set()

    def _extract_item(it: dict):
        if not isinstance(it, dict):
            return
        vr = it.get("videoRenderer") or it.get("movieRenderer") or it.get("compactVideoRenderer")
        if not vr and "shelfRenderer" in it:
            shelf_items = (
                it["shelfRenderer"]
                .get("content", {})
                .get("verticalListRenderer", {})
                .get("items", [])
            )
            for sub in shelf_items:
                _extract_item(sub)
            return
        if not vr:
            return

        vid = (vr.get("videoId") or "").strip()
        if not vid or vid in seen_ids:
            return
        runs = vr.get("title", {}).get("runs") or []
        title = "".join(run.get("text", "") for run in runs).strip()
        if not title:
            # Movie renderer might store title under title simpleText
            title = vr.get("title", {}).get("simpleText") or ""
        if not title:
            return

        channel_runs = vr.get("ownerText", {}).get("runs") or vr.get("shortBylineText", {}).get("runs") or []
        channel = "".join(run.get("text", "") for run in channel_runs).strip()
        length = vr.get("lengthText", {}).get("simpleText") or ""
        dur_s = _parse_duration(length)
        if max_duration_s and max_duration_s > 0 and dur_s is not None and dur_s > max_duration_s:
            return

        thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
        thumb = thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        if thumb.startswith("//"):
            thumb = "https:" + thumb

        seen_ids.add(vid)
        results.append({
            "id": vid,
            "title": title,
            "channel": channel or "YouTube",
            "duration": length or "Video",
            "duration_s": dur_s,
            "thumbnail": thumb,
            "url": f"https://www.youtube.com/watch?v={vid}",
        })

    data = _query_innertube_search(q, timeout=timeout)
    sections = (
        data.get("contents", {})
        .get("twoColumnSearchResultsRenderer", {})
        .get("primaryContents", {})
        .get("sectionListRenderer", {})
        .get("contents", [])
        if data
        else []
    )

    continuation_token = None
    try:
        for section in sections:
            for it in section.get("itemSectionRenderer", {}).get("contents", []):
                _extract_item(it)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break
            cir = section.get("continuationItemRenderer", {})
            if cir:
                continuation_token = cir.get("continuationEndpoint", {}).get("continuationCommand", {}).get("token")

        # Automatically paginate up to `limit` items using continuation tokens
        page = 1
        while len(results) < limit and continuation_token and page < 5:
            page += 1
            data2 = _query_innertube_search(q, timeout=timeout, continuation=continuation_token)
            continuation_token = None
            if not data2:
                break
            cmds = data2.get("onResponseReceivedCommands", [])
            for cmd in cmds:
                for ci in cmd.get("appendContinuationItemsAction", {}).get("continuationItems", []):
                    for it in ci.get("itemSectionRenderer", {}).get("contents", []):
                        _extract_item(it)
                        if len(results) >= limit:
                            break
                    cir = ci.get("continuationItemRenderer", {})
                    if cir:
                        continuation_token = cir.get("continuationEndpoint", {}).get("continuationCommand", {}).get("token")
                    if len(results) >= limit:
                        break
                if len(results) >= limit:
                    break
    except Exception as e:
        logger.warning("fast search list parse error: %s", e)

    return results
