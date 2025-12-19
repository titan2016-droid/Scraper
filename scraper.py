import re
import time
from typing import Callable, Dict, List, Optional

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

def scrape_channel(
    channel_url: str,
    content_type: str,
    max_videos: int = 25,
    language: Optional[str] = None,
    allow_auto: bool = True,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
) -> List[Dict]:
    if content_type not in {"shorts", "longform"}:
        raise ValueError("content_type must be 'shorts' or 'longform'")

    url = channel_url.strip()

    ydl_opts_list = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "playlistend": int(max_videos) * 3,  # overfetch then filter
        "nocheckcertificate": True,
    }

    entries = []
    with yt_dlp.YoutubeDL(ydl_opts_list) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            return []
        if "entries" in info and info["entries"]:
            entries = list(info["entries"])
        else:
            entries = [info]

    normalized = []
    for e in entries:
        if not e:
            continue
        vurl = e.get("url") or e.get("webpage_url")
        if vurl and not vurl.startswith("http"):
            vurl = f"https://www.youtube.com/watch?v={vurl}"

        title = e.get("title") or ""
        vid = extract_video_id(vurl or "") or e.get("id")
        if not vid:
            continue

        is_shorts = False
        if vurl and "youtube.com/shorts/" in vurl:
            is_shorts = True
        dur = e.get("duration")
        if dur is not None and dur <= 60:
            is_shorts = True

        if content_type == "shorts" and not is_shorts:
            continue
        if content_type == "longform" and is_shorts:
            continue

        normalized.append({"id": vid, "title": title, "url": vurl})
        if len(normalized) >= max_videos:
            break

    total = len(normalized)
    results = []

    ydl_opts_video = {"quiet": True, "skip_download": True, "nocheckcertificate": True}

    for i, item in enumerate(normalized, start=1):
        if progress_cb:
            progress_cb(i - 1, total, f"Fetching metadata & transcript ({i}/{total})...")

        vid = item["id"]
        vurl = item["url"] or f"https://www.youtube.com/watch?v={vid}"

        title = item["title"]
        view_count = None

        try:
            with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                vinfo = ydl.extract_info(vurl, download=False)
                if vinfo:
                    title = vinfo.get("title") or title
                    view_count = vinfo.get("view_count")
                    vurl = vinfo.get("webpage_url") or vurl
        except Exception:
            pass

        transcript = get_transcript(vid, language=language, allow_auto=allow_auto)

        results.append(
            {"title": title, "url": vurl, "video_id": vid, "view_count": view_count, "transcript": transcript}
        )

        time.sleep(0.15)

    if progress_cb:
        progress_cb(total, total, "Complete.")
    return results
