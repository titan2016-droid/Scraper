
import os
import re
import time
import html
import tempfile
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Union, Tuple
from urllib.request import Request, urlopen
import http.cookiejar as cookiejar
import requests
from urllib.parse import urlparse, urlunparse

import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

# youtube-transcript-api internal module paths vary across versions; support multiple layouts.
try:
    # youtube-transcript-api internal module paths vary across versions; support multiple layouts.
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

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"

# -------- YouTube Data API helpers (optional but recommended) --------
YT_API_BASE = "https://www.googleapis.com/youtube/v3"

def _yt_api_get(path: str, params: Dict, timeout: int = 25) -> Optional[Dict]:
    try:
        r = requests.get(f"{YT_API_BASE}/{path}", params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None

def _yt_api_channel_from_url(channel_url: str, api_key: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (channelId, uploadsPlaylistId) if possible."""
    p = urlparse(channel_url)
    path = (p.path or "").strip("/")

    # Handle @handle URLs
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

    # /channel/UCxxxx
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
            out.append({
                "id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": sn.get("title") or "",
                "publishedAt": sn.get("publishedAt") or "",
            })
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
        data = _yt_api_get("videos", {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(chunk),
            "key": api_key,
            "maxResults": 50,
        })
        if not data:
            continue
        for item in data.get("items") or []:
            vid = item.get("id")
            if vid:
                out[vid] = item
        time.sleep(0.12)
    return out

def _yt_api_extract_fields(item: Dict) -> Dict:
    sn = item.get("snippet") or {}
    stats = item.get("statistics") or {}
    cd = item.get("contentDetails") or {}

    thumbs = sn.get("thumbnails") or {}
    def turl(key: str) -> str:
        v = thumbs.get(key) or {}
        return v.get("url") or ""

    dur_iso = cd.get("duration") or ""
    dur_seconds = None
    m = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", dur_iso)
    if m:
        h = int(m.group(1) or 0); mi = int(m.group(2) or 0); s = int(m.group(3) or 0)
        dur_seconds = h*3600 + mi*60 + s

    def to_int(x):
        try:
            return int(x)
        except Exception:
            return None

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

# -------------------------------------------------------------------


def normalize_channel_url(url: str) -> str:
    """
    Normalizes common inputs:
    - strips query/fragment
    - removes trailing /shorts, /videos, /streams, /featured, /playlists
    """
    u = url.strip()
    if not u.startswith("http"):
        u = "https://" + u.lstrip("/")
    p = urlparse(u)
    path = (p.path or "").rstrip("/")
    # remove known tabs
    for tab in ("/shorts", "/videos", "/streams", "/featured", "/playlists", "/community", "/about"):
        if path.lower().endswith(tab):
            path = path[: -len(tab)]
            path = path.rstrip("/")
    # Rebuild without query/fragment
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


def _join_url(base: str, suffix: str) -> str:
    return base.rstrip("/") + suffix


def _popular_url(channel_url: str, tab: str) -> str:
    # Popular sort params used by YouTube channel tabs.
    if tab == "videos":
        return _join_url(channel_url, "/videos?view=0&sort=p&flow=grid")
    if tab == "shorts":
        return _join_url(channel_url, "/shorts?view=0&sort=p&flow=grid")
    return channel_url


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


def _clean_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _parse_vtt(vtt: str) -> str:
    lines = []
    for line in vtt.splitlines():
        line = line.strip()
        if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or line.startswith("STYLE"):
            continue
        if re.match(r"^\d+$", line):
            continue
        if re.search(r"\d{1,2}:\d{2}:\d{2}\.\d{3}\s*-->\s*\d{1,2}:\d{2}:\d{2}\.\d{3}", line):
            continue
        if re.search(r"\d{1,2}:\d{2}\.\d{3}\s*-->\s*\d{1,2}:\d{2}\.\d{3}", line):
            continue
        line = re.sub(r"<\d{1,2}:\d{2}:\d{2}\.\d{3}>", "", line)
        line = re.sub(r"</?c[^>]*>", "", line)
        line = re.sub(r"</?i>", "", line)
        line = re.sub(r"</?b>", "", line)
        line = html.unescape(line)
        line = _clean_whitespace(line)
        if line:
            lines.append(line)
    out, prev = [], None
    for t in lines:
        if t != prev:
            out.append(t)
        prev = t
    return _clean_whitespace(" ".join(out))


def _parse_srv3(xml_text: str) -> str:
    try:
        root = ET.fromstring(xml_text)
        parts = []
        for node in root.iter():
            if node.tag.lower().endswith("text") and node.text:
                parts.append(html.unescape(node.text))
        return _clean_whitespace(" ".join(parts))
    except Exception:
        parts = re.findall(r"<text[^>]*>(.*?)</text>", xml_text, flags=re.DOTALL | re.IGNORECASE)
        parts = [html.unescape(re.sub(r"<.*?>", "", p)) for p in parts]
        return _clean_whitespace(" ".join(parts))


def _parse_ttml(ttml_text: str) -> str:
    try:
        root = ET.fromstring(ttml_text)
        parts = []
        for node in root.iter():
            if node.tag.lower().endswith("p"):
                txt = "".join(node.itertext())
                if txt:
                    parts.append(html.unescape(txt))
        return _clean_whitespace(" ".join(parts))
    except Exception:
        txt = re.sub(r"<.*?>", " ", ttml_text)
        return _clean_whitespace(html.unescape(txt))


def _download_text(url: str, cookiefile: Optional[str] = None) -> str:
    cj = None
    if cookiefile:
        cj = cookiejar.MozillaCookieJar()
        try:
            cj.load(cookiefile, ignore_discard=True, ignore_expires=True)
        except Exception:
            cj = None

    headers = {
        "User-Agent": UA,
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.youtube.com/",
    }
    req = Request(url, headers=headers)
    if cj is not None:
        import urllib.request
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
        with opener.open(req, timeout=25) as resp:
            return resp.read().decode("utf-8", errors="ignore")
    with urlopen(req, timeout=25) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _pick_subtitle(info: Dict, language: Optional[str], allow_auto: bool) -> Tuple[Optional[str], str, Optional[str]]:
    subs = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}

    def pick(bucket: Dict, src_label: str) -> Optional[Tuple[str, str, str]]:
        if not bucket:
            return None
        keys = list(bucket.keys())

        def score_lang(k: str) -> int:
            kl = k.lower()
            if language and (kl == language.lower() or kl.startswith(language.lower() + "-")):
                return 0
            if kl in ("en", "en-us", "en-gb"):
                return 1
            return 2

        keys.sort(key=score_lang)
        ext_order = ["vtt", "ttml", "srv3", "srv1"]
        for ext in ext_order:
            for k in keys:
                for fmt in bucket.get(k, []):
                    if fmt.get("ext") == ext and fmt.get("url"):
                        return fmt["url"], src_label, ext
        for k in keys:
            for fmt in bucket.get(k, []):
                if fmt.get("url") and fmt.get("ext"):
                    return fmt["url"], src_label, fmt.get("ext")
        return None

    got = pick(subs, "yt_dlp_subtitles_manual")
    if got:
        return got[0], got[1], got[2]
    if allow_auto:
        got = pick(auto, "yt_dlp_subtitles_auto")
        if got:
            return got[0], got[1], got[2]
    return None, "none", None


def _captions_transcript(video_id: str, vinfo: Dict, language: Optional[str], allow_auto: bool, cookiefile: Optional[str]) -> Tuple[str, str, str, str, str]:
    transcript = ""
    t_status = "not_found"
    t_err = ""
    t_source = "none"
    t_fmt = ""

    try:
        sub_url, sub_source, fmt = _pick_subtitle(vinfo or {}, language=language, allow_auto=allow_auto)
        if sub_url and fmt:
            raw = _download_text(sub_url, cookiefile=cookiefile)
            if fmt == "vtt":
                transcript = _parse_vtt(raw)
            elif fmt == "ttml":
                transcript = _parse_ttml(raw)
            elif fmt in ("srv3", "srv1"):
                transcript = _parse_srv3(raw)
            else:
                transcript = _clean_whitespace(html.unescape(re.sub(r"<.*?>", " ", raw)))
            t_fmt = fmt
            t_source = sub_source
            t_status = "ok" if transcript else "error"
            if not transcript:
                t_err = f"Downloaded {fmt} captions but parsed empty."
    except Exception as e:
        t_err = f"{type(e).__name__}: {e}"

    if not transcript:
        try:
            if language:
                segments = YouTubeTranscriptApi.get_transcript(video_id, languages=[language])
            else:
                segments = YouTubeTranscriptApi.get_transcript(video_id)
            transcript = "\n".join(s.get("text", "") for s in segments).strip()
            t_status = "ok" if transcript else "not_found"
            t_source = "youtube_transcript_api"
            t_fmt = "segments"
        except TranscriptsDisabled as e:
            t_status, t_source, t_fmt, t_err = "disabled", "youtube_transcript_api", "segments", str(e)
        except VideoUnavailable as e:
            t_status, t_source, t_fmt, t_err = "unavailable", "youtube_transcript_api", "segments", str(e)
        except TooManyRequests as e:
            t_status, t_source, t_fmt, t_err = "blocked", "youtube_transcript_api", "segments", str(e)
        except NoTranscriptFound as e:
            t_status, t_source, t_fmt, t_err = "not_found", "youtube_transcript_api", "segments", str(e)
        except Exception as e:
            t_status, t_source, t_fmt, t_err = "error", "youtube_transcript_api", "segments", f"{type(e).__name__}: {e}"

    return transcript, t_status, t_source, t_fmt if t_fmt else "", t_err


def _download_audio_for_transcription(video_url: str, cookiefile: Optional[str]) -> Tuple[Optional[str], str]:
    tmpdir = tempfile.mkdtemp(prefix="ytaudio_")
    outtmpl = os.path.join(tmpdir, "%(id)s.%(ext)s")
    fmt = "bestaudio[filesize<25M]/bestaudio[ext=m4a][filesize<25M]/bestaudio[ext=webm][filesize<25M]/worstaudio/worstaudio[ext=m4a]/worstaudio[ext=webm]"

    ydl_opts = {
        "quiet": True,
        "skip_download": False,
        "nocheckcertificate": True,
        "outtmpl": outtmpl,
        "format": fmt,
        "noplaylist": True,
    }
    if cookiefile:
        ydl_opts["cookiefile"] = cookiefile

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)
            vid = info.get("id")
            for name in os.listdir(tmpdir):
                if vid and name.startswith(vid + "."):
                    path = os.path.join(tmpdir, name)
                    if os.path.getsize(path) > 25 * 1024 * 1024:
                        return None, f"audio_too_large ({os.path.getsize(path)/1024/1024:.1f}MB)"
                    return path, "ok"
            return None, "audio_not_found"
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"


def _openai_transcribe(audio_path: str, model: str) -> Tuple[str, str]:
    try:
        from openai import OpenAI
        client = OpenAI()
        with open(audio_path, "rb") as f:
            tr = client.audio.transcriptions.create(model=model, file=f)
        text = getattr(tr, "text", None) or (tr.get("text") if isinstance(tr, dict) else "")
        return (text or "").strip(), ""
    except Exception as e:
        return "", f"{type(e).__name__}: {e}"


def _is_shorts_candidate(url: str, duration: Optional[Union[int, float]]) -> bool:
    if url and "youtube.com/shorts/" in url:
        return True
    if duration is not None:
        try:
            return int(duration) <= 60
        except Exception:
            return False
    return False


def _get_list_url(channel_url: str, content_type: str, popular_first: bool) -> List[str]:
    if not popular_first:
        return [channel_url]
    urls = []
    if content_type in ("longform", "both"):
        urls.append(_popular_url(channel_url, "videos"))
    if content_type in ("shorts", "both"):
        urls.append(_popular_url(channel_url, "shorts"))
    return urls or [channel_url]


def scrape_channel(
    channel_url: str,
    youtube_api_key: Optional[str] = None,
    content_type: str = "shorts",
    scan_limit: int = 600,
    min_views: int = 300_000,
    max_results: int = 150,
    language: Optional[str] = None,
    allow_auto: bool = True,
    include_error_details: bool = True,
    cookiefile: Optional[str] = None,
    popular_first: bool = True,
    early_stop: bool = True,
    transcript_mode: str = "Auto (Captions → Audio Transcribe)",
    openai_model: str = "gpt-4o-mini-transcribe",
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    return_debug: bool = False,
) -> Tuple[List[Dict], List[str]]:
    debug = []
    channel_url = normalize_channel_url(channel_url)
    debug.append(f"Normalized channel URL: {channel_url}")
    list_urls = _get_list_url(channel_url, content_type, popular_first)
    debug.append("Listing URLs:")
    debug.extend(list_urls)

    ydl_opts_list = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "nocheckcertificate": True,
        "playlistend": int(scan_limit) * 3,
    }
    if cookiefile:
        ydl_opts_list["cookiefile"] = cookiefile

    
candidates: List[Dict] = []
if api_sorted_candidates:
    candidates = api_sorted_candidates
    debug.append(f"Entries from API: {len(candidates)}")
else:
    seen = set()
    for lurl in list_urls:
        try:
            with yt_dlp.YoutubeDL(ydl_opts_list) as ydl:
                info = ydl.extract_info(lurl, download=False)
        except Exception as e:
            debug.append(f"List extraction failed for {lurl}: {type(e).__name__}: {e}")
            continue
        if not info:
            debug.append(f"No info returned for {lurl}")
            continue
        entries = list(info.get("entries") or [])
        debug.append(f"Entries from {lurl}: {len(entries)}")
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
            candidates.append({
                "id": vid,
                "title": e.get("title") or "",
                "url": vurl,
                "duration": e.get("duration"),
            })

filtered = []
    for c in candidates:
        is_shorts = _is_shorts_candidate(c.get("url") or "", c.get("duration"))
        if content_type == "shorts" and not is_shorts:
            continue
        if content_type == "longform" and is_shorts:
            continue
        filtered.append(c)

    filtered = filtered[: int(scan_limit)]
    total = len(filtered)
    debug.append(f"Candidates after type filter: {total}")

    ydl_opts_video = {"quiet": True, "skip_download": True, "nocheckcertificate": True}
    if cookiefile:
        ydl_opts_video["cookiefile"] = cookiefile

    results: List[Dict] = []
    below_streak = 0

    for i, item in enumerate(filtered, start=1):
        if progress_cb:
            progress_cb(i - 1, total, f"Checking views + transcript ({i}/{total})… qualifying: {len(results)}")

        vid = item["id"]
        vurl = item.get("url") or f"https://www.youtube.com/watch?v={vid}"
        api_fields = item.get("_api_fields") or {}


title = api_fields.get("title") or item.get("title") or ""
canonical_url = vurl
vinfo = None

try:
    with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
        vinfo = ydl.extract_info(vurl, download=False)
        if vinfo:
            canonical_url = vinfo.get("webpage_url") or canonical_url
            if not title:
                title = vinfo.get("title") or title
except Exception:
    continue

view_count = api_fields.get("view_count")
if view_count is None and vinfo:
    view_count = vinfo.get("view_count")

if view_count is None:
    continue


        if int(view_count) == 0:
            continue

        if int(view_count) < int(min_views):
            if early_stop:
                below_streak += 1
                if below_streak >= 8:
                    debug.append("Early stop: consecutive videos below min_views.")
                    break
            continue
        else:
            below_streak = 0

        transcript = ""
        t_status = "not_found"
        t_err = ""
        t_source = "none"
        t_fmt = ""
        t_method = ""

        mode = transcript_mode

        if mode == "Captions only":
            transcript, t_status, t_source, t_fmt, t_err = _captions_transcript(vid, vinfo or {}, language, allow_auto, cookiefile)
            t_method = "captions"
        elif mode == "Audio transcribe only":
            t_method = "audio_transcribe"
        else:
            transcript, t_status, t_source, t_fmt, t_err = _captions_transcript(vid, vinfo or {}, language, allow_auto, cookiefile)
            t_method = "captions_then_audio"
            if t_status != "ok" or not transcript:
                transcript = ""

        if (mode == "Audio transcribe only") or (mode == "Auto (Captions → Audio Transcribe)" and not transcript):
            audio_path, a_status = _download_audio_for_transcription(canonical_url, cookiefile)
            t_method = "audio_transcribe"
            if a_status != "ok" or not audio_path:
                t_status = "audio_download_failed"
                t_source = "yt_dlp_audio"
                t_err = a_status
            else:
                text, err = _openai_transcribe(audio_path, model=openai_model)
                t_source = "openai_audio_transcriptions"
                t_fmt = "text"
                if text:
                    transcript = text
                    t_status = "ok"
                    t_err = ""
                else:
                    t_status = "audio_transcribe_failed"
                    t_err = err or "empty_transcript"

        row = {
            "title": title,
            "url": canonical_url,
            "video_id": vid,
            "view_count": int(view_count),
            "transcript_method": t_method,
            "transcript_source": t_source,
            "transcript_format": t_fmt,
            "transcript_status": t_status,
            "transcript": transcript,
        }
        if include_error_details:
            row["transcript_error"] = t_err

        results.append(row)

        if len(results) >= int(max_results):
            break

        time.sleep(0.25)

    results.sort(key=lambda r: (r.get("view_count") or 0), reverse=True)
    for idx, r in enumerate(results, start=1):
        r["rank"] = idx

    if progress_cb:
        progress_cb(total, total, f"Complete. Returning {len(results)} ranked result(s).")

    return (results, debug) if return_debug else (results, [])
