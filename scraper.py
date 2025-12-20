import re
import time
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi

# youtube-transcript-api internal module paths vary; support multiple layouts.
try:
    from youtube_transcript_api._errors import (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        TooManyRequests,
    )
except Exception:
    try:
        from youtube_transcript_api import (
            TranscriptsDisabled,
            NoTranscriptFound,
            VideoUnavailable,
            TooManyRequests,
        )
    except Exception:
        class TranscriptsDisabled(Exception): ...
        class NoTranscriptFound(Exception): ...
        class VideoUnavailable(Exception): ...
        class TooManyRequests(Exception): ...


YT_API_BASE = "https://www.googleapis.com/youtube/v3"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"


def normalize_channel_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    if not url.startswith("http"):
        url = "https://" + url
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}{p.path}".rstrip("/")


def _yt_api_get(path: str, params: Dict, timeout: int = 25) -> Optional[Dict]:
    try:
        r = requests.get(f"{YT_API_BASE}/{path}", params=params, timeout=timeout, headers={"User-Agent": UA})
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _yt_api_channel_from_url(channel_url: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    p = urlparse(channel_url)
    path = (p.path or "").strip("/")

    if path.startswith("@"):
        data = _yt_api_get("channels", {
            "part": "snippet,contentDetails",
            "forHandle": path.lstrip("@"),
            "key": api_key,
            "maxResults": 1,
        })
        if data and data.get("items"):
            item = data["items"][0]
            ch_id = item.get("id")
            uploads = (((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
            return ch_id, uploads

    m = re.search(r"/channel/([A-Za-z0-9_-]+)", channel_url)
    if m:
        ch_id = m.group(1)
        data = _yt_api_get("channels", {"part": "contentDetails", "id": ch_id, "key": api_key, "maxResults": 1})
        uploads = None
        if data and data.get("items"):
            uploads = (((data["items"][0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
        return ch_id, uploads

    return None, None


def _yt_api_playlist_items(uploads_playlist_id: str, api_key: str, max_items: int) -> List[Dict]:
    out: List[Dict] = []
    page = None
    while len(out) < max_items:
        params = {"part": "snippet,contentDetails", "playlistId": uploads_playlist_id, "maxResults": 50, "key": api_key}
        if page:
            params["pageToken"] = page
        data = _yt_api_get("playlistItems", params)
        if not data:
            break
        for it in data.get("items") or []:
            cd = it.get("contentDetails") or {}
            vid = cd.get("videoId")
            if not vid:
                continue
            out.append({"id": vid})
            if len(out) >= max_items:
                break
        page = data.get("nextPageToken")
        if not page:
            break
        time.sleep(0.12)
    return out


def _yt_api_videos(api_key: str, video_ids: List[str]) -> List[Dict]:
    out: List[Dict] = []
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        data = _yt_api_get("videos", {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(chunk),
            "key": api_key,
            "maxResults": 50,
        })
        if data:
            out.extend(data.get("items") or [])
        time.sleep(0.12)
    return out


def _parse_iso8601_duration(dur: str) -> Optional[int]:
    if not dur:
        return None
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", dur)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h*3600 + mi*60 + s


def _is_short(duration_seconds: Optional[int]) -> bool:
    if duration_seconds is None:
        return False
    return int(duration_seconds) <= 60


def _get_transcript(video_id: str, language: Optional[str], allow_auto: bool) -> Tuple[str, str]:
    try:
        if language:
            try:
                t = YouTubeTranscriptApi.get_transcript(video_id, languages=[language])
                return " ".join(x.get("text", "") for x in t).strip(), ""
            except Exception:
                pass

        tl = YouTubeTranscriptApi.list_transcripts(video_id)

        try:
            tr = tl.find_manually_created_transcript([language] if language else ["en"])
            t = tr.fetch()
            return " ".join(x.get("text", "") for x in t).strip(), ""
        except Exception:
            pass

        if allow_auto:
            try:
                tr = tl.find_generated_transcript([language] if language else ["en"])
                t = tr.fetch()
                return " ".join(x.get("text", "") for x in t).strip(), ""
            except Exception:
                pass

        return "", "No transcript found"
    except TranscriptsDisabled:
        return "", "TranscriptsDisabled"
    except NoTranscriptFound:
        return "", "NoTranscriptFound"
    except TooManyRequests:
        return "", "TooManyRequests"
    except VideoUnavailable:
        return "", "VideoUnavailable"
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def scrape_channel(
    channel_url: str,
    youtube_api_key: Optional[str] = None,
    content_type: str = "shorts",
    scan_limit: int = 600,
    min_views: int = 300_000,
    max_results: int = 100,
    language: Optional[str] = None,
    allow_auto: bool = True,
    popular_first: bool = True,
    early_stop: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    return_debug: bool = False,
) -> Tuple[List[Dict], List[str]]:
    debug: List[str] = []
    channel_url = normalize_channel_url(channel_url)
    debug.append(f"Normalized channel URL: {channel_url}")

    rows: List[Dict] = []

    if not youtube_api_key:
        debug.append("ERROR: Missing YouTube API key.")
        return rows, debug

    ch_id, uploads = _yt_api_channel_from_url(channel_url, youtube_api_key)
    debug.append(f"YouTube Data API enabled. channelId={ch_id} uploadsPlaylist={uploads}")
    if not uploads:
        debug.append("ERROR: Could not resolve uploads playlist. Use a @handle URL or /channel/UC... URL.")
        return rows, debug

    playlist_items = _yt_api_playlist_items(uploads, youtube_api_key, max_items=int(scan_limit))
    debug.append(f"Playlist items fetched: {len(playlist_items)}")

    ids = [x["id"] for x in playlist_items]
    api_items = _yt_api_videos(youtube_api_key, ids) if ids else []
    debug.append(f"Video details fetched via API: {len(api_items)}")

    candidates: List[Dict] = []
    for it in api_items:
        vid = it.get("id")
        sn = it.get("snippet") or {}
        stats = it.get("statistics") or {}
        cd = it.get("contentDetails") or {}

        dur = _parse_iso8601_duration(cd.get("duration") or "")
        is_short = _is_short(dur)
        if content_type == "shorts" and not is_short:
            continue
        if content_type == "longform" and is_short:
            continue

        view_count = 0
        try:
            view_count = int(stats.get("viewCount") or 0)
        except Exception:
            view_count = 0

        thumbs = sn.get("thumbnails") or {}
        def turl(k):
            return (thumbs.get(k) or {}).get("url") or ""

        candidates.append({
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "view_count": view_count,
            "publishedAt": sn.get("publishedAt") or "",
            "channelId": sn.get("channelId") or "",
            "channelTitle": sn.get("channelTitle") or "",
            "title": sn.get("title") or "",
            "description": sn.get("description") or "",
            "tags_list": sn.get("tags") or [],
            "categoryId": sn.get("categoryId") or "",
            "defaultLanguage": sn.get("defaultLanguage") or "",
            "defaultAudioLanguage": sn.get("defaultAudioLanguage") or "",
            "duration_seconds": dur,
            "thumbnail_default": turl("default"),
            "thumbnail_medium": turl("medium"),
            "thumbnail_high": turl("high"),
            "thumbnail_standard": turl("standard"),
            "thumbnail_maxres": turl("maxres"),
        })

    debug.append(f"Candidates after type filter: {len(candidates)}")

    if popular_first:
        candidates.sort(key=lambda x: x.get("view_count") or 0, reverse=True)
        debug.append("Sorted candidates by view_count desc.")

    below_streak = 0
    total = len(candidates)
    for i, c in enumerate(candidates, start=1):
        if progress_cb:
            progress_cb(i, total, f"Checking {i}/{total}: {c['video_id']} ({c['view_count']} views)")

        if (c.get("view_count") or 0) < int(min_views):
            if popular_first and early_stop:
                below_streak += 1
                if below_streak >= 20:
                    debug.append("Early stop: many consecutive videos below min_views.")
                    break
            continue
        below_streak = 0

        transcript, terr = _get_transcript(c["video_id"], language=language, allow_auto=allow_auto)

        rows.append({
            "rank": len(rows) + 1,
            **{k: (", ".join(v) if k=="tags_list" else v) for k, v in c.items()},
            "transcript": transcript,
            "transcript_error": terr,
        })

        if len(rows) >= int(max_results):
            break

    debug.append(f"Returned rows: {len(rows)}")
    return rows, debug
