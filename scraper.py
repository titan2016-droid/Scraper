import re
import time
import json
import html as html_lib
from typing import Dict, List, Optional, Tuple, Callable
from urllib.parse import urlparse

import requests
import yt_dlp

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

try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:
    YouTubeTranscriptApi = None  # type: ignore

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
YT_API_BASE = "https://www.googleapis.com/youtube/v3"


def normalize_channel_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    if url.startswith("@"):
        return "https://www.youtube.com/" + url
    if url.startswith("http"):
        return url
    return "https://" + url


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
        data = _yt_api_get("channels", {
            "part": "contentDetails",
            "id": ch_id,
            "key": api_key,
            "maxResults": 1,
        })
        uploads = None
        if data and data.get("items"):
            uploads = (((data["items"][0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"))
        return ch_id, uploads

    return None, None


def _yt_api_playlist_video_ids(uploads_playlist_id: str, api_key: str, max_items: int) -> List[Dict]:
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
            sn = it.get("snippet") or {}
            vid = cd.get("videoId")
            if not vid:
                continue
            out.append({"id": vid, "url": f"https://www.youtube.com/watch?v={vid}", "playlist_publishedAt": sn.get("publishedAt") or ""})
            if len(out) >= max_items:
                break
        page = data.get("nextPageToken")
        if not page:
            break
        time.sleep(0.12)
    return out


def _yt_api_videos_map(video_ids: List[str], api_key: str) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        data = _yt_api_get("videos", {"part": "snippet,contentDetails,statistics", "id": ",".join(chunk), "key": api_key, "maxResults": 50})
        if not data:
            continue
        for item in data.get("items") or []:
            vid = item.get("id")
            if vid:
                out[vid] = item
        time.sleep(0.12)
    return out


def _parse_iso8601_duration_to_seconds(dur_iso: str) -> Optional[int]:
    if not dur_iso:
        return None
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", dur_iso)
    if not m:
        return None
    h = int(m.group(1) or 0)
    mi = int(m.group(2) or 0)
    s = int(m.group(3) or 0)
    return h * 3600 + mi * 60 + s


def _yt_api_extract_fields(item: Dict) -> Dict:
    sn = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    cd = item.get("contentDetails") or {}

    thumbs = sn.get("thumbnails") or {}
    def turl(key: str) -> str:
        v = thumbs.get(key) or {}
        return v.get("url") or ""

    def to_int(x):
        try:
            return int(x)
        except Exception:
            return None

    dur_seconds = _parse_iso8601_duration_to_seconds(cd.get("duration") or "")

    return {
        "publishedAt": sn.get("publishedAt") or "",
        "channelId": sn.get("channelId") or "",
        "channelTitle": sn.get("channelTitle") or "",
        "title": sn.get("title") or "",
        "description": sn.get("description") or "",
        "tags_list": sn.get("tags") or [],
        "categoryId": sn.get("categoryId") or "",
        "defaultLanguage": sn.get("defaultLanguage") or "",
        "defaultAudioLanguage": sn.get("defaultAudioLanguage") or "",
        "thumbnail_default": turl("default"),
        "thumbnail_medium": turl("medium"),
        "thumbnail_high": turl("high"),
        "thumbnail_standard": turl("standard"),
        "thumbnail_maxres": turl("maxres"),
        "view_count": to_int(stats.get("viewCount")),
        "like_count": to_int(stats.get("likeCount")),
        "comment_count": to_int(stats.get("commentCount")),
        "duration_seconds": dur_seconds,
    }


def _is_short(duration_seconds: Optional[int], url: str) -> bool:
    if duration_seconds is not None:
        return duration_seconds <= 60
    return "/shorts/" in (url or "")


def _pick_caption_url(vinfo: Dict) -> Optional[Tuple[str, str]]:
    def best_from(d: Dict) -> Optional[Tuple[str, str]]:
        if not d:
            return None
        langs = ["en"] if "en" in d else list(d.keys())
        fmt_priority = ["json3", "vtt", "srv3", "srv2", "srv1", "ttml", "xml"]
        for lang in langs:
            tracks = d.get(lang) or []
            tracks_sorted = sorted(tracks, key=lambda t: fmt_priority.index(t.get("ext")) if t.get("ext") in fmt_priority else 999)
            for t in tracks_sorted:
                if t.get("url") and t.get("ext"):
                    return t["url"], t["ext"]
        return None
    res = best_from(vinfo.get("subtitles") or {})
    if res:
        return res
    return best_from(vinfo.get("automatic_captions") or {})


def _vtt_to_text(vtt: str) -> str:
    lines = []
    for line in (vtt or "").splitlines():
        line = line.strip()
        if not line or line.startswith("WEBVTT") or "-->" in line or re.match(r"^\d+$", line):
            continue
        line = re.sub(r"<[^>]+>", "", line).strip()
        if line:
            lines.append(line)
    return " ".join(lines).strip()


def _json3_to_text(js: str) -> str:
    try:
        data = json.loads(js)
    except Exception:
        return ""
    out = []
    for ev in data.get("events", []) if isinstance(data, dict) else []:
        for s in ev.get("segs") or []:
            t = s.get("utf8")
            if t:
                out.append(t.replace("\n", " ").strip())
    return " ".join([x for x in out if x]).strip()


def _xml_to_text(xml: str) -> str:
    chunks = re.findall(r"<text[^>]*>(.*?)</text>", xml or "", flags=re.DOTALL)
    cleaned = []
    for c in chunks:
        c = re.sub(r"<[^>]+>", "", c)
        c = html_lib.unescape(c).replace("\n", " ").strip()
        if c:
            cleaned.append(c)
    return " ".join(cleaned).strip()


def _download_with_ydl(ydl: yt_dlp.YoutubeDL, url: str) -> Optional[str]:
    try:
        resp = ydl.urlopen(url)
        raw = resp.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="ignore")
        return str(raw)
    except Exception:
        return None


def _get_transcript(video_url: str, video_id: str, vinfo: Dict, cookies_path: Optional[str]) -> Tuple[str, str]:
    if YouTubeTranscriptApi is not None:
        try:
            tlist = YouTubeTranscriptApi.list_transcripts(video_id)
            transcript = None
            try:
                transcript = tlist.find_manually_created_transcript(["en"])
            except Exception:
                transcript = None
            if transcript is None:
                try:
                    transcript = tlist.find_generated_transcript(["en"])
                except Exception:
                    transcript = None
            if transcript is None:
                transcript = next(iter(tlist), None)
            if transcript is not None:
                parts = transcript.fetch()
                text = " ".join([(p.get("text") or "").replace("\n", " ").strip() for p in parts if p.get("text")]).strip()
                if text:
                    return text, ""
        except (TranscriptsDisabled, NoTranscriptFound, VideoUnavailable, TooManyRequests) as e:
            last_err = f"YTTranscriptAPI:{type(e).__name__}"
        except Exception as e:
            last_err = f"YTTranscriptAPI:{type(e).__name__}"
    else:
        last_err = "YTTranscriptAPI:NotInstalled"

    try:
        ydl_opts = {"quiet": True, "nocheckcertificate": True, "skip_download": True, "http_headers": {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}}
        if cookies_path:
            ydl_opts["cookiefile"] = cookies_path

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            cap = _pick_caption_url(vinfo)
            if not cap:
                return "", "YTDLP:NoCaptions"
            cap_url, cap_ext = cap
            body = _download_with_ydl(ydl, cap_url)
            if not body:
                return "", "YTDLP:CaptionDownloadFailed"
            cap_ext = (cap_ext or "").lower()
            if cap_ext == "vtt":
                txt = _vtt_to_text(body)
            elif cap_ext == "json3":
                txt = _json3_to_text(body)
            else:
                txt = _xml_to_text(body)
            if txt:
                return txt, ""
            return "", "YTDLP:EmptyTranscript"
    except Exception as e:
        return "", f"YTDLP:{type(e).__name__}"

    return "", last_err


def scrape_channel(
    channel_url: str,
    youtube_api_key: str,
    cookies_txt_bytes: Optional[bytes] = None,
    content_type: str = "shorts",
    min_views: int = 300000,
    max_results: int = 100,
    scan_limit: int = 800,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
):
    debug: List[str] = []
    channel_url = normalize_channel_url(channel_url)

    cookies_path = None
    if cookies_txt_bytes:
        try:
            import tempfile, os
            fd, path = tempfile.mkstemp(prefix="cookies_", suffix=".txt")
            with open(path, "wb") as f:
                f.write(cookies_txt_bytes)
            cookies_path = path
            debug.append("cookies.txt provided: enabled yt-dlp cookiefile mode.")
        except Exception:
            cookies_path = None
            debug.append("cookies.txt provided but could not be written; ignoring.")

    ch_id, uploads = _yt_api_channel_from_url(channel_url, youtube_api_key)
    debug.append(f"YouTube Data API enabled. channelId={ch_id} uploadsPlaylist={uploads}")
    if not uploads:
        debug.append("Could not resolve uploads playlist via API. Check the channel URL.")
        return [], debug

    playlist_items = _yt_api_playlist_video_ids(uploads, youtube_api_key, max_items=min(max(scan_limit, 200), 5000))
    ids = [x["id"] for x in playlist_items]
    api_map = _yt_api_videos_map(ids, youtube_api_key) if ids else {}
    debug.append(f"API videos fetched: {len(api_map)}")

    candidates: List[Dict] = []
    for it in playlist_items:
        api_item = api_map.get(it["id"])
        if not api_item:
            continue
        fields = _yt_api_extract_fields(api_item)
        dur = fields.get("duration_seconds")
        is_short = _is_short(dur, it["url"])
        if content_type == "shorts" and not is_short:
            continue
        if content_type == "longform" and is_short:
            continue
        vc = fields.get("view_count") or 0
        candidates.append({"id": it["id"], "url": it["url"], "fields": fields, "view_count": vc})

    candidates.sort(key=lambda x: x.get("view_count") or 0, reverse=True)
    debug.append(f"API candidates after type filter: {len(candidates)} (sorted by views desc)")

    qualifying = [c for c in candidates if int(c.get("view_count") or 0) >= int(min_views)]
    debug.append(f"Qualifying by min_views={min_views}: {len(qualifying)}")
    qualifying = qualifying[: int(max_results)]
    debug.append(f"Will fetch transcripts for top {len(qualifying)} qualifying videos.")

    rows: List[Dict] = []

    ydl_opts_video = {"quiet": True, "skip_download": True, "nocheckcertificate": True, "http_headers": {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}}
    if cookies_path:
        ydl_opts_video["cookiefile"] = cookies_path

    total = len(qualifying)
    for i, item in enumerate(qualifying, start=1):
        vid = item["id"]
        url = item["url"]
        fields = item["fields"]

        if on_progress:
            on_progress(i-1, total, f"Fetching transcript {i}/{total}: {(fields.get('title') or '')[:60]}")

        try:
            with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                vinfo = ydl.extract_info(url, download=False) or {}
        except Exception:
            vinfo = {}

        transcript, terr = _get_transcript(url, vid, vinfo, cookies_path)

        tags = fields.get("tags_list") or []
        rows.append({
            "rank": i,
            "video_id": vid,
            "url": (vinfo.get("webpage_url") or url),
            "title": fields.get("title") or (vinfo.get("title") or ""),
            "view_count": int(fields.get("view_count") or 0),
            "like_count": fields.get("like_count"),
            "comment_count": fields.get("comment_count"),
            "duration_seconds": fields.get("duration_seconds"),
            "publishedAt": fields.get("publishedAt"),
            "channelId": fields.get("channelId"),
            "channelTitle": fields.get("channelTitle"),
            "description": fields.get("description"),
            "tags": ", ".join([str(t) for t in tags]),
            "categoryId": fields.get("categoryId"),
            "defaultLanguage": fields.get("defaultLanguage"),
            "defaultAudioLanguage": fields.get("defaultAudioLanguage"),
            "thumbnail": fields.get("thumbnail_maxres") or fields.get("thumbnail_high") or fields.get("thumbnail_medium") or "",
            "transcript": transcript,
            "transcript_error": terr,
        })

        if on_progress:
            on_progress(i, total, f"Done {i}/{total}")

    if cookies_path:
        try:
            import os
            os.remove(cookies_path)
        except Exception:
            pass

    return rows, debug
