"""
YouTube Transcript + Metadata Scraper (API-safe)

- Lists channel videos using the YouTube Data API v3 (no HTML scraping)
- Fetches public transcripts:
    1) youtube-transcript-api (fast when available)
    2) yt-dlp caption URL fallback (often works when (1) fails)
- Exports a CSV with transcript + transcript_error + transcript_source

Notes:
- YouTube Data API does NOT provide public caption download without OAuth; transcripts here are from public caption endpoints.
- Some videos legitimately have no transcript (disabled/unavailable).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Callable
import time
import math
import json
import re
import urllib.parse

import requests
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    TooManyRequests,
)

import yt_dlp


YT_API_BASE = "https://www.googleapis.com/youtube/v3"


# -----------------------------
# Helpers
# -----------------------------
def _iso8601_duration_to_seconds(duration: Optional[str]) -> Optional[int]:
    """Parse ISO8601 duration like PT1M2S -> seconds."""
    if not duration:
        return None
    # Very small parser for PT#H#M#S
    m = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def normalize_channel_url(url_or_handle: str) -> str:
    s = (url_or_handle or "").strip()
    if not s:
        return s
    # Allow @handle input
    if s.startswith("@"):
        return f"https://www.youtube.com/{s}"
    # Allow handle without @
    if re.fullmatch(r"[A-Za-z0-9._-]{3,}", s) and "youtube" not in s.lower():
        return f"https://www.youtube.com/@{s}"
    # If missing scheme
    if s.startswith("www.youtube.com/"):
        s = "https://" + s
    return s


def _extract_handle(url: str) -> Optional[str]:
    # https://www.youtube.com/@davisfacts or /@davisfacts/shorts
    m = re.search(r"/@([A-Za-z0-9._-]+)", url)
    return m.group(1) if m else None


def _extract_channel_id(url: str) -> Optional[str]:
    # https://www.youtube.com/channel/UCxxxx
    m = re.search(r"/channel/(UC[0-9A-Za-z_-]{20,})", url)
    return m.group(1) if m else None


def _yt_api_get(path: str, params: Dict[str, Any], api_key: str, timeout: int = 30) -> Dict[str, Any]:
    """GET wrapper with basic retry for 429/5xx."""
    if not api_key:
        raise ValueError("Missing YouTube Data API key")
    url = f"{YT_API_BASE}/{path}"
    params = dict(params or {})
    params["key"] = api_key

    backoff = 1.5
    last_err = None
    for attempt in range(6):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            # Backoff on quota/rate/temporary errors
            if r.status_code in (429, 500, 502, 503, 504):
                last_err = f"{r.status_code} {r.text[:200]}"
                time.sleep(backoff)
                backoff *= 2
                continue
            # Non-retryable
            raise RuntimeError(f"YouTube API error {r.status_code}: {r.text[:500]}")
        except requests.RequestException as e:
            last_err = str(e)
            time.sleep(backoff)
            backoff *= 2
    raise RuntimeError(f"YouTube API request failed after retries: {last_err}")


def resolve_channel_id(channel_url_or_handle: str, api_key: str) -> str:
    url = normalize_channel_url(channel_url_or_handle)
    cid = _extract_channel_id(url)
    if cid:
        return cid

    handle = _extract_handle(url)
    if handle:
        # forHandle is supported by YouTube Data API v3 (newer feature); fallback to search if it fails.
        try:
            data = _yt_api_get("channels", {"part": "id", "forHandle": handle, "maxResults": 1}, api_key)
            items = data.get("items") or []
            if items:
                return items[0]["id"]
        except Exception:
            pass
        # Fallback: search channels by query
        data = _yt_api_get("search", {"part": "snippet", "q": handle, "type": "channel", "maxResults": 1}, api_key)
        items = data.get("items") or []
        if items:
            return items[0]["id"]["channelId"]

    # As a last resort, attempt to use the URL as a query (works for /c/ or /user/ sometimes)
    query = url.split("/")[-1]
    data = _yt_api_get("search", {"part": "snippet", "q": query, "type": "channel", "maxResults": 1}, api_key)
    items = data.get("items") or []
    if not items:
        raise RuntimeError("Could not resolve channel ID from input.")
    return items[0]["id"]["channelId"]


def _get_uploads_playlist_id(channel_id: str, api_key: str) -> str:
    data = _yt_api_get("channels", {"part": "contentDetails", "id": channel_id, "maxResults": 1}, api_key)
    items = data.get("items") or []
    if not items:
        raise RuntimeError("Channel not found.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _list_playlist_video_ids(playlist_id: str, api_key: str, max_items: int = 200) -> List[str]:
    out: List[str] = []
    page_token = None
    while True:
        data = _yt_api_get(
            "playlistItems",
            {"part": "contentDetails", "playlistId": playlist_id, "maxResults": 50, "pageToken": page_token},
            api_key,
        )
        for it in data.get("items") or []:
            vid = (it.get("contentDetails") or {}).get("videoId")
            if vid:
                out.append(vid)
                if len(out) >= max_items:
                    return out
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return out


def _get_videos_details(video_ids: List[str], api_key: str) -> Dict[str, Dict[str, Any]]:
    """Return dict videoId -> fields from videos.list."""
    out: Dict[str, Dict[str, Any]] = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        data = _yt_api_get(
            "videos",
            {
                "part": "snippet,contentDetails,statistics",
                "id": ",".join(batch),
                "maxResults": 50,
            },
            api_key,
        )
        for it in data.get("items") or []:
            vid = it.get("id")
            if not vid:
                continue
            snippet = it.get("snippet") or {}
            stats = it.get("statistics") or {}
            cd = it.get("contentDetails") or {}
            thumbs = snippet.get("thumbnails") or {}
            def _thumb(k: str) -> str:
                t = thumbs.get(k) or {}
                return t.get("url") or ""
            out[vid] = {
                "title": snippet.get("title") or "",
                "publishedAt": snippet.get("publishedAt"),
                "channelTitle": snippet.get("channelTitle"),
                "tags_list": snippet.get("tags") or [],
                "categoryId": snippet.get("categoryId"),
                "defaultLanguage": snippet.get("defaultLanguage"),
                "defaultAudioLanguage": snippet.get("defaultAudioLanguage"),
                "view_count": int(stats.get("viewCount") or 0),
                "like_count": int(stats.get("likeCount") or 0) if stats.get("likeCount") is not None else None,
                "comment_count": int(stats.get("commentCount") or 0) if stats.get("commentCount") is not None else None,
                "duration_seconds": _iso8601_duration_to_seconds(cd.get("duration")),
                "thumbnail_maxres": _thumb("maxres") or _thumb("standard") or _thumb("high") or _thumb("medium") or _thumb("default"),
            }
    return out


def _clean_vtt_to_text(vtt: str) -> str:
    # Remove WEBVTT header and timestamps, keep text lines.
    lines = []
    for raw in (vtt or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("WEBVTT"):
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{3}\s-->\s", s) or re.match(r"^\d{2}:\d{2}\.\d{3}\s-->\s", s):
            continue
        # Remove simple tags
        s = re.sub(r"<[^>]+>", "", s).strip()
        if s:
            lines.append(s)
    # Deduplicate consecutive duplicates
    cleaned = []
    for s in lines:
        if not cleaned or cleaned[-1] != s:
            cleaned.append(s)
    return " ".join(cleaned).strip()


def _pick_caption_url(vinfo: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    """
    Pick best caption URL from yt-dlp info.
    Returns (url, ext) or None.
    """
    subtitles = (vinfo.get("subtitles") or {})  # manually uploaded
    auto = (vinfo.get("automatic_captions") or {})
    # Prefer manual English, then auto English.
    candidates = []
    for src, bucket in (("manual", subtitles), ("auto", auto)):
        for lang_key in ("en", "en-US", "en-GB", "en-CA", "en-AU"):
            tracks = bucket.get(lang_key) or []
            for t in tracks:
                url = t.get("url")
                ext = t.get("ext") or ""
                if url:
                    candidates.append((src, lang_key, ext, url))

    if not candidates:
        # Try any language if English not found (better than nothing)
        for src, bucket in (("manual", subtitles), ("auto", auto)):
            for lang_key, tracks in (bucket or {}).items():
                for t in tracks or []:
                    url = t.get("url")
                    ext = t.get("ext") or ""
                    if url:
                        candidates.append((src, lang_key, ext, url))

    if not candidates:
        return None

    # Sort: manual > auto, vtt > json3 > srv3 > ttml > srt
    src_pri = {"manual": 0, "auto": 1}
    fmt_pri = {"vtt": 0, "json3": 1, "srv3": 2, "ttml": 3, "srt": 4}
    candidates.sort(key=lambda x: (src_pri.get(x[0], 9), fmt_pri.get(x[2], 9)))
    best = candidates[0]
    return best[3], best[2]


def _download_with_ydl(url: str, cookies_path: Optional[str]) -> Optional[str]:
    ydl_opts = {
        "quiet": True,
        "nocheckcertificate": True,
        "cookiesfrombrowser": None,  # do not auto-pull
    }
    if cookies_path:
        ydl_opts["cookiefile"] = cookies_path

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        with ydl.urlopen(url) as resp:
            b = resp.read()
            # yt-dlp returns bytes; attempt utf-8 then latin-1 fallback
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError:
                return b.decode("latin-1", errors="ignore")


def _get_transcript(video_url: str, video_id: str, vinfo: Dict[str, Any], cookies_path: Optional[str]) -> Tuple[str, str, str]:
    """
    Returns (transcript_text, transcript_error, transcript_source)
    transcript_source: "youtube_transcript_api" | "yt_dlp" | ""
    """
    # 1) youtube-transcript-api
    try:
        tlist = YouTubeTranscriptApi.list_transcripts(video_id)
        # Prefer English, then any.
        transcript_obj = None
        for lang in ("en", "en-US", "en-GB"):
            try:
                transcript_obj = tlist.find_manually_created_transcript([lang])
                break
            except Exception:
                pass
        if transcript_obj is None:
            for lang in ("en", "en-US", "en-GB"):
                try:
                    transcript_obj = tlist.find_generated_transcript([lang])
                    break
                except Exception:
                    pass
        if transcript_obj is None:
            # pick the first available transcript
            transcript_obj = next(iter(tlist), None)

        if transcript_obj is None:
            return "", "No transcript tracks found.", ""

        parts = transcript_obj.fetch() or []
        text = " ".join([p.get("text", "").replace("\n", " ").strip() for p in parts]).strip()
        if text:
            text = re.sub(r"\s+", " ", text)
            return text, "", "youtube_transcript_api"
        # If fetch returned empty, continue to yt-dlp
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
        err = f"{type(e).__name__}: {str(e)}"
    except TooManyRequests as e:
        err = f"TooManyRequests: {str(e)}"
    except Exception as e:
        err = f"Transcript API error: {type(e).__name__}: {str(e)}"

    # 2) yt-dlp fallback
    try:
        if not vinfo:
            # Extract minimal info (no download)
            ydl_opts = {
                "quiet": True,
                "skip_download": True,
                "nocheckcertificate": True,
            }
            if cookies_path:
                ydl_opts["cookiefile"] = cookies_path
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                vinfo = ydl.extract_info(video_url, download=False) or {}

        picked = _pick_caption_url(vinfo)
        if not picked:
            return "", err + " | No captions available via yt-dlp.", ""

        cap_url, ext = picked
        raw = _download_with_ydl(cap_url, cookies_path) or ""
        if not raw.strip():
            return "", err + " | Caption download returned empty.", ""

        # Most common: VTT
        if ext.lower() == "vtt":
            text = _clean_vtt_to_text(raw)
        else:
            # Try to parse json3 (YouTube caption JSON)
            if ext.lower() in ("json3", "srv3"):
                try:
                    obj = json.loads(raw)
                    # json3 format has events[].segs[].utf8
                    out = []
                    for ev in obj.get("events") or []:
                        for seg in ev.get("segs") or []:
                            s = (seg.get("utf8") or "").replace("\n", " ").strip()
                            if s:
                                out.append(s)
                    text = " ".join(out).strip()
                except Exception:
                    text = raw
            else:
                text = raw

        text = re.sub(r"\s+", " ", (text or "")).strip()
        if text:
            return text, "", "yt_dlp"
        return "", err + " | Caption parse produced empty text.", ""
    except Exception as e:
        return "", err + f" | yt-dlp error: {type(e).__name__}: {str(e)}", ""


# -----------------------------
# Public API
# -----------------------------
def scrape_channel(
    channel_url_or_handle: str,
    api_key: str,
    max_videos: int = 200,
    only_shorts: bool = True,
    cookies_path: Optional[str] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict[str, Any]]:
    """
    Scrape channel videos and return rows suitable for CSV export.

    max_videos: maximum videos to process from uploads playlist (latest first)
    only_shorts: if True, keep duration <= 60s
    cookies_path: optional path to cookies.txt for yt-dlp (helps when YouTube blocks captions)
    """
    channel_id = resolve_channel_id(channel_url_or_handle, api_key)
    uploads = _get_uploads_playlist_id(channel_id, api_key)
    video_ids = _list_playlist_video_ids(uploads, api_key, max_items=max_videos)

    # Newest first (uploads playlist already newest first)
    details = _get_videos_details(video_ids, api_key)

    # Filter
    final_ids = []
    for vid in video_ids:
        d = details.get(vid) or {}
        dur = d.get("duration_seconds")
        if only_shorts and (dur is None or dur > 60):
            continue
        final_ids.append(vid)

    total = len(final_ids)
    rows: List[Dict[str, Any]] = []
    if on_progress:
        on_progress(0, max(total, 1), f"Found {total} videos to process...")

    for i, vid in enumerate(final_ids, start=1):
        d = details.get(vid) or {}
        url = f"https://www.youtube.com/watch?v={vid}"

        # Pull yt-dlp info once per video (also gives webpage_url)
        try:
            ydl_opts = {"quiet": True, "skip_download": True, "nocheckcertificate": True}
            if cookies_path:
                ydl_opts["cookiefile"] = cookies_path
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                vinfo = ydl.extract_info(url, download=False) or {}
        except Exception:
            vinfo = {}

        transcript, terr, tsource = _get_transcript(url, vid, vinfo, cookies_path)

        tags = d.get("tags_list") or []
        rows.append(
            {
                "rank": i,
                "video_id": vid,
                "url": vinfo.get("webpage_url") or url,
                "title": d.get("title") or (vinfo.get("title") or ""),
                "view_count": int(d.get("view_count") or 0),
                "like_count": d.get("like_count"),
                "comment_count": d.get("comment_count"),
                "duration_seconds": d.get("duration_seconds"),
                "publishedAt": d.get("publishedAt"),
                "channelTitle": d.get("channelTitle"),
                "tags": ", ".join([str(t) for t in tags]),
                "categoryId": d.get("categoryId"),
                "defaultLanguage": d.get("defaultLanguage"),
                "defaultAudioLanguage": d.get("defaultAudioLanguage"),
                "thumbnail": d.get("thumbnail_maxres") or "",
                "transcript": transcript,
                "transcript_source": tsource,
                "transcript_error": terr,
            }
        )

        if on_progress:
            ok = "✅" if transcript else "⚠️"
            on_progress(i, total, f"{ok} {i}/{total}: {d.get('title','')[:60]}")

    return rows


def scrape_single_video(video_url: str, api_key: str, cookies_path: Optional[str] = None) -> Dict[str, Any]:
    """Scrape one video URL (YouTube) and return one row."""
    parsed = urllib.parse.urlparse(video_url)
    q = urllib.parse.parse_qs(parsed.query)
    vid = (q.get("v") or [""])[0]
    if not vid and "youtu.be" in parsed.netloc:
        vid = parsed.path.strip("/")
    if not vid:
        raise ValueError("Could not extract video ID from URL.")

    details = _get_videos_details([vid], api_key).get(vid) or {}
    url = f"https://www.youtube.com/watch?v={vid}"

    try:
        ydl_opts = {"quiet": True, "skip_download": True, "nocheckcertificate": True}
        if cookies_path:
            ydl_opts["cookiefile"] = cookies_path
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            vinfo = ydl.extract_info(url, download=False) or {}
    except Exception:
        vinfo = {}

    transcript, terr, tsource = _get_transcript(url, vid, vinfo, cookies_path)

    tags = details.get("tags_list") or []
    return {
        "video_id": vid,
        "url": vinfo.get("webpage_url") or url,
        "title": details.get("title") or (vinfo.get("title") or ""),
        "view_count": int(details.get("view_count") or 0),
        "like_count": details.get("like_count"),
        "comment_count": details.get("comment_count"),
        "duration_seconds": details.get("duration_seconds"),
        "publishedAt": details.get("publishedAt"),
        "channelTitle": details.get("channelTitle"),
        "tags": ", ".join([str(t) for t in tags]),
        "thumbnail": details.get("thumbnail_maxres") or "",
        "transcript": transcript,
        "transcript_source": tsource,
        "transcript_error": terr,
    }
