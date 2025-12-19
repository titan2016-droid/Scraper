import re
import time
from typing import Callable, Dict, List, Optional, Union

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
    CouldNotRetrieveTranscript,
)


def extract_video_id(url: str) -> Optional[str]:
    url = url.strip()
    m = re.search(r"youtube\.com/shorts/([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"youtu\.be/([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    m = re.search(r"/embed/([A-Za-z0-9_-]{6,})", url)
    if m:
        return m.group(1)
    return None


def get_transcript(video_id: str, language: Optional[str], allow_auto: bool) -> str:
    try:
        if language:
            segments = YouTubeTranscriptApi.get_transcript(video_id, languages=[language])
            return "\n".join(s.get("text", "") for s in segments).strip()
        segments = YouTubeTranscriptApi.get_transcript(video_id)
        return "\n".join(s.get("text", "") for s in segments).strip()
    except NoTranscriptFound:
        if not allow_auto:
            return ""
        try:
            tlist = YouTubeTranscriptApi.list_transcripts(video_id)
            chosen = None
            for t in tlist:
                if not t.is_generated:
                    chosen = t
                    break
            if chosen is None:
                chosen = next(iter(tlist), None)
            if chosen is None:
                return ""
            segments = chosen.fetch()
            return "\n".join(s.get("text", "") for s in segments).strip()
        except Exception:
            return ""
    except (TranscriptsDisabled, VideoUnavailable, CouldNotRetrieveTranscript):
        return ""
    except Exception:
        return ""


def _is_shorts_candidate(url: str, duration: Optional[Union[int, float]]) -> bool:
    if url and "youtube.com/shorts/" in url:
        return True
    if duration is not None:
        try:
            return int(duration) <= 60
        except Exception:
            return False
    return False


def scrape_channel(
    channel_url: str,
    content_type: str = "shorts",       # 'shorts' | 'longform' | 'both'
    scan_limit: Optional[int] = 800,     # how many videos to *check* for view_count
    min_views: int = 300_000,            # include only videos above this
    max_results: int = 200,              # cap output size
    language: Optional[str] = None,
    allow_auto: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict]:
    """
    Scans a channel, checks view counts, and only pulls transcripts for videos meeting min_views.
    Then ranks results by view_count descending.
    """
    if content_type not in {"shorts", "longform", "both"}:
        raise ValueError("content_type must be 'shorts', 'longform', or 'both'")

    url = channel_url.strip()

    # Extract a "flat" list of potential videos. We'll then fetch full metadata per video (view_count).
    # Overfetch a bit because filtering by shorts/longform may drop some entries.
    playlistend = None
    if scan_limit is not None:
        playlistend = int(scan_limit) * 3

    ydl_opts_list = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "nocheckcertificate": True,
    }
    if playlistend is not None:
        ydl_opts_list["playlistend"] = playlistend

    entries = []
    with yt_dlp.YoutubeDL(ydl_opts_list) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            return []
        if "entries" in info and info["entries"]:
            entries = list(info["entries"])
        else:
            entries = [info]

    # Normalize candidates (id, title, url, duration)
    candidates = []
    seen = set()
    for e in entries:
        if not e:
            continue

        vurl = e.get("url") or e.get("webpage_url")
        if vurl and not vurl.startswith("http"):
            vurl = f"https://www.youtube.com/watch?v={vurl}"

        vid = extract_video_id(vurl or "") or e.get("id")
        if not vid or vid in seen:
            continue
        seen.add(vid)

        title = e.get("title") or ""
        duration = e.get("duration")

        candidates.append({"id": vid, "title": title, "url": vurl, "duration": duration})

    # Apply a *pre-filter* by content type using URL/duration hints (best-effort)
    filtered = []
    for c in candidates:
        is_shorts = _is_shorts_candidate(c.get("url") or "", c.get("duration"))
        if content_type == "shorts" and not is_shorts:
            continue
        if content_type == "longform" and is_shorts:
            continue
        filtered.append(c)

    # Respect scan_limit after filtering (scan_limit is how many we actually check)
    if scan_limit is not None:
        filtered = filtered[: int(scan_limit)]

    total = len(filtered)
    results: List[Dict] = []

    ydl_opts_video = {
        "quiet": True,
        "skip_download": True,
        "nocheckcertificate": True,
    }

    for i, item in enumerate(filtered, start=1):
        if progress_cb:
            progress_cb(i - 1, total, f"Checking views ({i}/{total})… qualifying: {len(results)}")

        vid = item["id"]
        vurl = item.get("url") or f"https://www.youtube.com/watch?v={vid}"

        title = item.get("title") or ""
        view_count = None
        canonical_url = vurl

        # Fetch metadata to get view_count (required for min_views filter)
        try:
            with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                vinfo = ydl.extract_info(vurl, download=False)
                if vinfo:
                    title = vinfo.get("title") or title
                    view_count = vinfo.get("view_count")
                    canonical_url = vinfo.get("webpage_url") or canonical_url
        except Exception:
            # If we can't get views, skip (can't verify >= min_views)
            continue

        if view_count is None or int(view_count) < int(min_views):
            continue

        # Only now fetch transcript (saves time)
        transcript = get_transcript(vid, language=language, allow_auto=allow_auto)

        results.append(
            {
                "title": title,
                "url": canonical_url,
                "video_id": vid,
                "view_count": int(view_count) if view_count is not None else None,
                "transcript": transcript,
            }
        )

        # Stop early once we've collected enough results (still "across channel" within scan_limit)
        if len(results) >= int(max_results):
            break

        time.sleep(0.15)  # gentle throttle

    # Rank + sort
    results.sort(key=lambda r: (r.get("view_count") or 0), reverse=True)
    for idx, r in enumerate(results, start=1):
        r["rank"] = idx

    if progress_cb:
        progress_cb(total, total, f"Complete. Returning {len(results)} ranked result(s).")

    return results
