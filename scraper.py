\
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# --- youtube-transcript-api imports (robust across versions) ---
try:
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    from youtube_transcript_api.errors import (  # type: ignore
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        TooManyRequests,
        CouldNotRetrieveTranscript,
    )
except Exception:  # pragma: no cover
    from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    from youtube_transcript_api._errors import (  # type: ignore
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        TooManyRequests,
        CouldNotRetrieveTranscript,
    )

# Optional: yt-dlp fallback for subtitle extraction (requires a cookies.txt)
# Only use this for videos you are authorized to access.
try:  # pragma: no cover
    from yt_dlp import YoutubeDL  # type: ignore
except Exception:  # pragma: no cover
    YoutubeDL = None  # type: ignore


YOUTUBE_URL_RE = re.compile(r"^https?://(www\.)?youtube\.com/.*", re.I)


def normalize_channel_url(url: str) -> str:
    """Normalize common channel URL shapes (incl. @handle)."""
    url = (url or "").strip()
    if not url:
        return ""
    # allow user to paste @handle only
    if url.startswith("@"):
        return f"https://www.youtube.com/{url}"
    if not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    # strip query/fragment
    url = url.split("#", 1)[0].split("?", 1)[0]
    return url


def _extract_handle_or_channel_id(channel_url: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (handle, channel_id) if present.
    - https://www.youtube.com/@davisfacts -> handle="davisfacts"
    - https://www.youtube.com/channel/UCxxxx -> channel_id="UCxxxx"
    """
    channel_url = normalize_channel_url(channel_url)

    m = re.search(r"youtube\.com/@([^/]+)", channel_url, re.I)
    if m:
        return m.group(1), None

    m = re.search(r"youtube\.com/channel/([^/]+)", channel_url, re.I)
    if m:
        return None, m.group(1)

    # legacy username / custom url: /c/Name or /user/Name or /Name
    m = re.search(r"youtube\.com/(c|user)/([^/]+)", channel_url, re.I)
    if m:
        return m.group(2), None

    m = re.search(r"youtube\.com/([^/]+)$", channel_url, re.I)
    if m and m.group(1) not in {"watch", "shorts"}:
        return m.group(1), None

    return None, None


def _build_yt(api_key: str):
    return build("youtube", "v3", developerKey=api_key, cache_discovery=False)


def _resolve_channel_id(api_key: str, channel_url: str, debug: List[str]) -> str:
    handle, channel_id = _extract_handle_or_channel_id(channel_url)

    if channel_id:
        debug.append(f"Resolved channel id from URL: {channel_id}")
        return channel_id

    yt = _build_yt(api_key)

    # If we have a handle/custom identifier, use search to find channel id
    query = handle or channel_url
    debug.append(f"Resolving channel via search: q={query!r}")

    try:
        resp = yt.search().list(part="snippet", q=query, type="channel", maxResults=1).execute()
        items = resp.get("items", [])
        if not items:
            raise ValueError("Could not resolve channel. Try using the /channel/UC... URL.")
        ch_id = items[0]["snippet"]["channelId"]
        debug.append(f"Resolved channel id via search: {ch_id}")
        return ch_id
    except HttpError as e:
        debug.append(f"Channel resolve HttpError: {e}")
        raise


def _parse_iso8601_duration_to_seconds(duration: str) -> Optional[int]:
    # PT#M#S format; may also contain hours
    if not duration or not duration.startswith("PT"):
        return None
    hours = minutes = seconds = 0
    m = re.search(r"(\d+)H", duration)
    if m:
        hours = int(m.group(1))
    m = re.search(r"(\d+)M", duration)
    if m:
        minutes = int(m.group(1))
    m = re.search(r"(\d+)S", duration)
    if m:
        seconds = int(m.group(1))
    return hours * 3600 + minutes * 60 + seconds


def _get_transcript_text(
    video_id: str,
    languages: Optional[List[str]] = None,
    cookies_txt_path: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Returns (transcript_text, error_string).
    """
    languages = languages or ["en"]

    # Primary path: youtube-transcript-api (fast + simple)
    try:
        parts = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)  # type: ignore
        text = " ".join([p.get("text", "") for p in parts]).strip()
        return (text if text else None), None
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, TooManyRequests, CouldNotRetrieveTranscript) as e:
        primary_err = f"{type(e).__name__}"
    except Exception as e:
        primary_err = f"TranscriptError: {e}"

    # Fallback path (optional): yt-dlp with user-provided cookies
    if cookies_txt_path and YoutubeDL is not None:
        try:
            fallback_text = _get_subtitle_text_via_ytdlp(video_id, languages=languages, cookies_txt_path=cookies_txt_path)
            if fallback_text:
                return fallback_text, None
        except Exception as e:  # keep primary error as the surfaced reason
            return None, f"{primary_err} (+CookiesFallbackFailed: {type(e).__name__})"

    return None, primary_err


def _vtt_or_srt_to_text(raw: str) -> str:
    """Best-effort cleanup for .vtt/.srt subtitle files."""
    out: List[str] = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            continue
        if s.startswith("WEBVTT"):
            continue
        if "-->" in s:
            continue
        if s.isdigit():
            continue
        if s.startswith("NOTE"):
            continue
        # basic cue settings cleanup
        if s.startswith("<") and s.endswith(">"):
            continue
        out.append(s)
    text = " ".join(out)
    # Remove leftover markup-ish tags
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _get_subtitle_text_via_ytdlp(
    video_id: str,
    languages: Optional[List[str]],
    cookies_txt_path: str,
) -> Optional[str]:
    """Attempt to fetch subtitles using yt-dlp.

    This is only intended as a fallback when youtube-transcript-api fails.
    Requires a cookies.txt in Netscape format.
    """
    if YoutubeDL is None:
        return None

    import tempfile

    # Expand language list to common variants (yt-dlp expects BCP47-ish strings)
    langs = languages or ["en"]
    expanded: List[str] = []
    for l in langs:
        l = (l or "").strip()
        if not l:
            continue
        expanded.append(l)
        if l.lower() == "en":
            expanded.extend(["en-US", "en-GB"])

    url = f"https://www.youtube.com/watch?v={video_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        ydl_opts = {
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": expanded or ["en"],
            "subtitlesformat": "vtt/srt",
            "outtmpl": os.path.join(tmpdir, "%(id)s.%(ext)s"),
            "cookiefile": cookies_txt_path,
            "quiet": True,
            "no_warnings": True,
        }
        with YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Pick the largest subtitle file produced (usually the most complete)
        candidates: List[str] = []
        for fn in os.listdir(tmpdir):
            if not fn.startswith(video_id + "."):
                continue
            if fn.endswith((".vtt", ".srt")):
                candidates.append(os.path.join(tmpdir, fn))
        if not candidates:
            return None

        best = max(candidates, key=lambda p: os.path.getsize(p))
        raw = open(best, "r", encoding="utf-8", errors="ignore").read()
        text = _vtt_or_srt_to_text(raw)
        return text if text else None


def scrape_channel(
    channel_url: str,
    api_key: str,
    content_type: str = "both",   # "shorts" | "videos" | "both"
    scan_limit: int = 200,
    min_views: int = 0,
    popular_first: bool = True,
    include_transcripts: bool = True,
    transcript_languages: Optional[List[str]] = None,
    cookies_txt_path: Optional[str] = None,
    sleep_every: int = 0,
    debug: Optional[List[str]] = None,
):
    """
    Scrape a channel's videos (metadata + optional transcripts) using YouTube Data API v3 + youtube-transcript-api.
    Returns a list of rows (dicts).
    """
    debug = debug if debug is not None else []
    channel_url = normalize_channel_url(channel_url)

    if not api_key:
        raise ValueError("YouTube Data API key is required.")
    if not channel_url:
        raise ValueError("Please provide a YouTube Channel URL.")

    scan_limit = int(max(1, min(int(scan_limit or 200), 2000)))
    min_views = int(max(0, int(min_views or 0)))
    content_type = (content_type or "both").lower().strip()
    if content_type not in {"shorts", "videos", "both"}:
        content_type = "both"

    channel_id = _resolve_channel_id(api_key, channel_url, debug)
    yt = _build_yt(api_key)

    # Step 1: get uploads playlist
    debug.append("Fetching channel contentDetails to locate uploads playlist...")
    ch = yt.channels().list(part="contentDetails,snippet,statistics", id=channel_id, maxResults=1).execute()
    ch_items = ch.get("items", [])
    if not ch_items:
        raise ValueError("Channel not found or not accessible.")
    uploads_pl = ch_items[0]["contentDetails"]["relatedPlaylists"]["uploads"]
    channel_title = ch_items[0].get("snippet", {}).get("title")
    channel_subs = ch_items[0].get("statistics", {}).get("subscriberCount")

    debug.append(f"Channel: {channel_title} | subs={channel_subs} | uploads={uploads_pl}")

    # Step 2: iterate playlist items to get video ids
    debug.append("Listing videos from uploads playlist...")
    video_ids: List[str] = []
    next_token: Optional[str] = None

    while True:
        resp = yt.playlistItems().list(
            part="contentDetails",
            playlistId=uploads_pl,
            maxResults=50,
            pageToken=next_token,
        ).execute()

        for it in resp.get("items", []):
            vid = it["contentDetails"]["videoId"]
            video_ids.append(vid)
            if len(video_ids) >= scan_limit:
                break

        if len(video_ids) >= scan_limit:
            break

        next_token = resp.get("nextPageToken")
        if not next_token:
            break

    debug.append(f"Collected {len(video_ids)} video ids.")

    # If popular_first, we'll need stats; order by views after fetching
    # Step 3: fetch details in batches
    rows: List[Dict] = []
    batch_size = 50
    for i in range(0, len(video_ids), batch_size):
        batch = video_ids[i : i + batch_size]
        try:
            vids = yt.videos().list(part="snippet,statistics,contentDetails", id=",".join(batch), maxResults=len(batch)).execute()
        except HttpError as e:
            debug.append(f"videos.list HttpError (batch {i}): {e}")
            raise

        for v in vids.get("items", []):
            vid = v["id"]
            snippet = v.get("snippet", {}) or {}
            stats = v.get("statistics", {}) or {}
            content_details = v.get("contentDetails", {}) or {}

            title = snippet.get("title")
            published = snippet.get("publishedAt")
            tags = snippet.get("tags", [])
            thumbnails = snippet.get("thumbnails", {}) or {}
            thumb = None
            for k in ("maxres", "standard", "high", "medium", "default"):
                if k in thumbnails:
                    thumb = thumbnails[k].get("url")
                    if thumb:
                        break

            view_count = int(stats.get("viewCount", 0) or 0)
            like_count = int(stats.get("likeCount", 0) or 0)
            comment_count = int(stats.get("commentCount", 0) or 0)

            duration = content_details.get("duration")
            seconds = _parse_iso8601_duration_to_seconds(duration) if duration else None

            # content_type filter
            if content_type == "shorts" and seconds is not None and seconds > 60:
                continue
            if content_type == "videos" and seconds is not None and seconds <= 60:
                continue

            if view_count < min_views:
                continue

            row = {
                "video_id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": title,
                "published_at": published,
                "duration_seconds": seconds,
                "view_count": view_count,
                "like_count": like_count,
                "comment_count": comment_count,
                "channel_title": channel_title,
                "channel_id": channel_id,
                "tags": ",".join(tags) if isinstance(tags, list) else (tags or ""),
                "thumbnail": thumb,
            }
            rows.append(row)

        if sleep_every and (i // batch_size + 1) % int(sleep_every) == 0:
            time.sleep(1.0)

    debug.append(f"After filtering: {len(rows)} videos.")

    # Sort popular-first
    if popular_first:
        rows.sort(key=lambda r: r.get("view_count", 0), reverse=True)
    else:
        # playlist order is newest-first typically; keep as is
        pass

    # Rank
    for idx, r in enumerate(rows, start=1):
        r["rank"] = idx

    # Step 4: transcripts (optional)
    if include_transcripts:
        debug.append("Fetching transcripts (where available)...")
        for idx, r in enumerate(rows, start=1):
            t, err = _get_transcript_text(
                r["video_id"],
                languages=transcript_languages,
                cookies_txt_path=cookies_txt_path,
            )
            r["transcript"] = t
            r["transcript_error"] = err
            # mild backoff if rate-limited
            if err == "TooManyRequests":
                time.sleep(2.0)
            # short sleep to be polite when doing lots
            if idx % 25 == 0:
                time.sleep(0.2)
    else:
        for r in rows:
            r["transcript"] = None
            r["transcript_error"] = None

    return rows, debug
