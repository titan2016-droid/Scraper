import csv
import io
import re
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

# Transcript dependency (optional at runtime if include_transcripts=False)
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        TooManyRequests,
    )
except ModuleNotFoundError:
    YouTubeTranscriptApi = None  # type: ignore
    TranscriptsDisabled = NoTranscriptFound = VideoUnavailable = TooManyRequests = Exception  # type: ignore


ProgressCB = Optional[Callable[[float, str], None]]


def normalize_channel_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
    return url


def _api_get(api_key: str, endpoint: str, params: Dict) -> Dict:
    params = dict(params)
    params["key"] = api_key
    resp = requests.get(endpoint, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _extract_handle_or_id(channel_url: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Returns (channel_id, handle, username/custom) best effort.
    """
    u = urlparse(channel_url)
    path = (u.path or "").strip("/")
    parts = path.split("/")
    channel_id = None
    handle = None
    custom = None

    # /channel/UCxxxx
    if len(parts) >= 2 and parts[0].lower() == "channel":
        channel_id = parts[1]
        return channel_id, None, None

    # /@handle
    if parts and parts[0].startswith("@"):
        handle = parts[0].lstrip("@")
        return None, handle, None

    # /user/username
    if len(parts) >= 2 and parts[0].lower() == "user":
        custom = parts[1]
        return None, None, custom

    # /c/customName  OR just /someName
    if len(parts) >= 2 and parts[0].lower() == "c":
        custom = parts[1]
        return None, None, custom

    if parts and parts[0]:
        custom = parts[0]
        return None, None, custom

    return None, None, None


def resolve_channel_id(api_key: str, channel_url: str) -> Tuple[str, str]:
    """
    Resolve a channel URL into (channel_id, channel_title).
    Supports:
      - https://youtube.com/channel/UC...
      - https://youtube.com/@handle
      - https://youtube.com/user/username
      - https://youtube.com/c/custom
      - https://youtube.com/<custom>
    """
    channel_id, handle, custom = _extract_handle_or_id(channel_url)

    if channel_id:
        data = _api_get(
            api_key,
            "https://www.googleapis.com/youtube/v3/channels",
            {"part": "snippet", "id": channel_id, "maxResults": 1},
        )
        items = data.get("items", [])
        if not items:
            raise ValueError("Could not resolve channel ID from URL.")
        return channel_id, items[0]["snippet"]["title"]

    if handle:
        # Newer API supports forHandle
        data = _api_get(
            api_key,
            "https://www.googleapis.com/youtube/v3/channels",
            {"part": "snippet", "forHandle": handle, "maxResults": 1},
        )
        items = data.get("items", [])
        if items:
            return items[0]["id"], items[0]["snippet"]["title"]

        # Fallback: search channels
        data = _api_get(
            api_key,
            "https://www.googleapis.com/youtube/v3/search",
            {"part": "snippet", "q": handle, "type": "channel", "maxResults": 5},
        )
        items = data.get("items", [])
        if not items:
            raise ValueError("Could not resolve channel handle.")
        cid = items[0]["snippet"]["channelId"]
        title = items[0]["snippet"]["channelTitle"]
        return cid, title

    if custom:
        # Try forUsername (only works for legacy /user)
        data = _api_get(
            api_key,
            "https://www.googleapis.com/youtube/v3/channels",
            {"part": "snippet", "forUsername": custom, "maxResults": 1},
        )
        items = data.get("items", [])
        if items:
            return items[0]["id"], items[0]["snippet"]["title"]

        # Fallback: search channels
        data = _api_get(
            api_key,
            "https://www.googleapis.com/youtube/v3/search",
            {"part": "snippet", "q": custom, "type": "channel", "maxResults": 5},
        )
        items = data.get("items", [])
        if not items:
            raise ValueError("Could not resolve channel URL. Try a /channel/UC... or /@handle URL.")
        cid = items[0]["snippet"]["channelId"]
        title = items[0]["snippet"]["channelTitle"]
        return cid, title

    raise ValueError("Invalid channel URL.")


def _iso8601_duration_to_seconds(d: str) -> int:
    # PT#H#M#S
    if not d:
        return 0
    h = m = s = 0
    mobj = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d)
    if mobj:
        h = int(mobj.group(1) or 0)
        m = int(mobj.group(2) or 0)
        s = int(mobj.group(3) or 0)
    return h * 3600 + m * 60 + s


def get_uploads_playlist_id(api_key: str, channel_id: str) -> str:
    data = _api_get(
        api_key,
        "https://www.googleapis.com/youtube/v3/channels",
        {"part": "contentDetails", "id": channel_id, "maxResults": 1},
    )
    items = data.get("items", [])
    if not items:
        raise ValueError("Could not fetch channel contentDetails.")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def list_video_ids_from_uploads(api_key: str, uploads_playlist_id: str, limit: int, progress_cb: ProgressCB = None) -> List[str]:
    ids: List[str] = []
    page_token = None
    fetched = 0

    while True:
        data = _api_get(
            api_key,
            "https://www.googleapis.com/youtube/v3/playlistItems",
            {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": 50,
                "pageToken": page_token or "",
            },
        )
        for it in data.get("items", []):
            vid = it.get("contentDetails", {}).get("videoId")
            if vid:
                ids.append(vid)
                fetched += 1
                if fetched >= limit:
                    break

        if progress_cb:
            progress_cb(min(0.30, fetched / max(1, limit) * 0.30), f"Collected {fetched}/{limit} video IDs…")

        if fetched >= limit:
            break

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return ids


def fetch_video_details(api_key: str, video_ids: List[str], progress_cb: ProgressCB = None) -> List[Dict]:
    all_items: List[Dict] = []
    total = len(video_ids)
    for i in range(0, total, 50):
        batch = video_ids[i : i + 50]
        data = _api_get(
            api_key,
            "https://www.googleapis.com/youtube/v3/videos",
            {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "maxResults": 50},
        )
        items = data.get("items", [])
        all_items.extend(items)

        if progress_cb:
            done = min(total, i + len(batch))
            # progress from 30% to 70%
            base = 0.30
            span = 0.40
            progress_cb(base + (done / max(1, total)) * span, f"Fetched metadata for {done}/{total} videos…")

    return all_items


def _get_transcript_text(video_id: str) -> Tuple[str, bool, str]:
    """
    Returns (transcript_text, has_transcript, transcript_error).
    """
    if YouTubeTranscriptApi is None:
        return "", False, "MissingDependency(youtube-transcript-api)"

    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join([seg.get("text", "") for seg in transcript]).strip()
        return text, bool(text), ""
    except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable) as e:
        return "", False, type(e).__name__
    except TooManyRequests:
        return "", False, "TooManyRequests"
    except Exception as e:
        return "", False, f"TranscriptError: {str(e)[:120]}"


def scrape_channel(
    api_key: str,
    channel_url: str,
    content_type: str = "both",  # shorts|videos|both
    scan_limit: int = 500,
    min_views: int = 0,
    popular_first: bool = True,
    include_transcripts: bool = True,
    progress_cb: ProgressCB = None,
) -> Tuple[List[Dict], bytes]:
    """
    Returns (rows, csv_bytes).
    """
    if not api_key:
        raise ValueError("Missing YouTube API key.")
    channel_url = normalize_channel_url(channel_url)

    if progress_cb:
        progress_cb(0.02, "Resolving channel…")
    channel_id, channel_title = resolve_channel_id(api_key, channel_url)

    if progress_cb:
        progress_cb(0.06, f"Resolved: {channel_title} ({channel_id})")
        progress_cb(0.10, "Getting uploads playlist…")
    uploads_pid = get_uploads_playlist_id(api_key, channel_id)

    if progress_cb:
        progress_cb(0.14, "Collecting video IDs…")
    ids = list_video_ids_from_uploads(api_key, uploads_pid, scan_limit, progress_cb=progress_cb)

    if not ids:
        raise ValueError("No videos found for this channel (or scan limit too low).")

    if progress_cb:
        progress_cb(0.32, "Fetching video details…")
    items = fetch_video_details(api_key, ids, progress_cb=progress_cb)

    # Build rows
    rows: List[Dict] = []
    for it in items:
        vid = it["id"]
        snip = it.get("snippet", {})
        stats = it.get("statistics", {})
        cdet = it.get("contentDetails", {})

        duration_sec = _iso8601_duration_to_seconds(cdet.get("duration", ""))
        is_short = duration_sec <= 60

        if content_type == "shorts" and not is_short:
            continue
        if content_type == "videos" and is_short:
            continue

        views = int(stats.get("viewCount", 0) or 0)
        if views < (min_views or 0):
            continue

        row = {
            "channel_title": channel_title,
            "channel_id": channel_id,
            "video_id": vid,
            "title": snip.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "published_at": snip.get("publishedAt", ""),
            "views": views,
            "like_count": int(stats.get("likeCount", 0) or 0),
            "comment_count": int(stats.get("commentCount", 0) or 0),
            "duration_sec": duration_sec,
            "is_short": is_short,
            "has_transcript": False,
            "transcript": "",
            "transcript_error": "",
        }
        rows.append(row)

    # Popular-first: order by views desc
    if popular_first:
        rows.sort(key=lambda r: r.get("views", 0), reverse=True)

    if include_transcripts and rows:
        total = len(rows)
        for idx, r in enumerate(rows):
            if progress_cb:
                # progress from 70% to 95%
                progress_cb(0.70 + (idx / max(1, total)) * 0.25, f"Fetching transcripts {idx+1}/{total}…")

            t, has_t, terr = _get_transcript_text(r["video_id"])
            r["transcript"] = t
            r["has_transcript"] = has_t
            r["transcript_error"] = terr

    # CSV
    if progress_cb:
        progress_cb(0.97, "Building CSV…")

    fieldnames = list(rows[0].keys()) if rows else [
        "channel_title","channel_id","video_id","title","url","published_at","views",
        "like_count","comment_count","duration_sec","is_short","has_transcript","transcript","transcript_error"
    ]

    bio = io.StringIO()
    w = csv.DictWriter(bio, fieldnames=fieldnames)
    w.writeheader()
    for r in rows:
        w.writerow(r)

    if progress_cb:
        progress_cb(1.0, "Ready.")

    return rows, bio.getvalue().encode("utf-8")
