import os
import re
import time
import html
import tempfile
import datetime
import requests
import xml.etree.ElementTree as ET
from typing import Callable, Dict, List, Optional, Union, Tuple
from urllib.request import Request, urlopen
import http.cookiejar as cookiejar
from urllib.parse import urlparse, urlunparse

import yt_dlp

# youtube-transcript-api has changed internal error module paths across versions.
# We avoid hard-failing imports by trying multiple locations and falling back to generic Exceptions.
from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore

try:
    from youtube_transcript_api._errors import (  # type: ignore
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        TooManyRequests,
    )
except Exception:
    try:
        # Some versions expose these at package root
        from youtube_transcript_api import (  # type: ignore
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


def normalize_channel_url(url: str) -> str:
    u = url.strip()
    if not u.startswith("http"):
        u = "https://" + u.lstrip("/")
    p = urlparse(u)
    path = (p.path or "").rstrip("/")
    for tab in ("/shorts", "/videos", "/streams", "/featured", "/playlists", "/community", "/about"):
        if path.lower().endswith(tab):
            path = path[: -len(tab)].rstrip("/")
    return urlunparse((p.scheme, p.netloc, path, "", "", ""))


def _join_url(base: str, suffix: str) -> str:
    return base.rstrip("/") + suffix


def _popular_url(channel_url: str, tab: str) -> str:
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


def _iso_published_at(vinfo: Dict) -> str:
    ts = vinfo.get("timestamp")
    if ts:
        try:
            return datetime.datetime.utcfromtimestamp(int(ts)).replace(microsecond=0).isoformat() + "Z"
        except Exception:
            pass
    ud = vinfo.get("upload_date") or vinfo.get("release_date")
    if ud and re.match(r"^\d{8}$", str(ud)):
        y, m, d = str(ud)[:4], str(ud)[4:6], str(ud)[6:8]
        return f"{y}-{m}-{d}T00:00:00Z"
    return ""


def _thumbnail_urls(vinfo: Dict, video_id: str) -> Dict[str, str]:
    thumbs = vinfo.get("thumbnails") or []
    fallback = {
        "thumbnail_default": f"https://i.ytimg.com/vi/{video_id}/default.jpg",
        "thumbnail_medium": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
        "thumbnail_high": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "thumbnail_standard": f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
        "thumbnail_maxres": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
    }
    if not thumbs:
        return fallback

    def w(t): 
        return t.get("width") or 0
    thumbs_sorted = sorted([t for t in thumbs if t.get("url")], key=w)
    if not thumbs_sorted:
        return fallback

    max_url = thumbs_sorted[-1]["url"]

    def pick_closest(target_w: int) -> str:
        best = None
        best_diff = 10**18
        for t in thumbs_sorted:
            tw = t.get("width") or 0
            diff = abs(tw - target_w)
            if diff < best_diff:
                best = t.get("url")
                best_diff = diff
        return best or ""

    return {
        "thumbnail_default": pick_closest(120) or thumbs_sorted[0]["url"],
        "thumbnail_medium": pick_closest(320) or thumbs_sorted[0]["url"],
        "thumbnail_high": pick_closest(480) or thumbs_sorted[0]["url"],
        "thumbnail_standard": pick_closest(640) or max_url,
        "thumbnail_maxres": max_url,
    }



def _yt_api_videos_map(video_ids: List[str], api_key: str) -> Dict[str, Dict]:
    """Fetch snippet, contentDetails, statistics for up to 50 IDs per request."""
    out: Dict[str, Dict] = {}
    if not api_key or not video_ids:
        return out
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i:i+50]
        params = {
            "part": "snippet,contentDetails,statistics",
            "id": ",".join(chunk),
            "key": api_key,
            "maxResults": 50,
        }
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params, timeout=25)
            if r.status_code != 200:
                continue
            data = r.json()
            for item in data.get("items", []):
                vid = item.get("id")
                if vid:
                    out[vid] = item
        except Exception:
            continue
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
    include_description: bool = False,
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

    candidates = []
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
            candidates.append({"id": vid, "title": e.get("title") or "", "url": vurl, "duration": e.get("duration")})

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

    api_map: Dict[str, Dict] = {}
    if youtube_api_key:
        ids = [x["id"] for x in filtered if x.get("id")]
        api_map = _yt_api_videos_map(ids, youtube_api_key)
        debug.append(f"YouTube Data API enabled. Stats fetched for: {len(api_map)} videos.")

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


        title = item.get("title") or ""
        canonical_url = vurl
        vinfo = None

        api_item = api_map.get(vid) if api_map else None
        api_fields = _yt_api_extract_fields(api_item) if api_item else {}

        # Use yt-dlp for canonical URL + captions/audio URLs (needed for transcripts)
        try:
            with yt_dlp.YoutubeDL(ydl_opts_video) as ydl:
                vinfo = ydl.extract_info(vurl, download=False)
                if vinfo:
                    canonical_url = vinfo.get("webpage_url") or canonical_url
        except Exception:
            continue

        # Prefer API view count (reliable on Streamlit Cloud)
        view_count = api_fields.get("view_count")
        if view_count is None and vinfo:
            view_count = vinfo.get("view_count")

        if view_count is None:
            continue

        # Prefer API title if available
        if api_fields.get("title"):
            title = api_fields["title"]
        elif vinfo and vinfo.get("title"):
            title = vinfo.get("title") or title


        if int(view_count) < int(min_views):
            if popular_first and early_stop:
                below_streak += 1
                if below_streak >= 8:
                    debug.append("Early stop: consecutive videos below min_views.")
                    break
            continue
        else:
            below_streak = 0


        publishedAt = api_fields.get("publishedAt") or _iso_published_at(vinfo or {})
        channelId = api_fields.get("channelId") or (vinfo.get("channel_id") or vinfo.get("uploader_id") or "")
        channelTitle = api_fields.get("channelTitle") or (vinfo.get("channel") or vinfo.get("uploader") or "")
        uploader = (vinfo.get("uploader") or channelTitle or "")
        tags = api_fields.get("tags_list") or (vinfo.get("tags") or [])
        categories = vinfo.get("categories") or []
        categoryId = api_fields.get("categoryId") or (vinfo.get("category_id") or "")
        defaultLanguage = api_fields.get("defaultLanguage") or (vinfo.get("language") or vinfo.get("default_language") or "")
        defaultAudioLanguage = api_fields.get("defaultAudioLanguage") or (vinfo.get("audio_language") or vinfo.get("default_audio_language") or "")

        duration_seconds = api_fields.get("duration_seconds")
        if duration_seconds is None:
            duration_seconds = vinfo.get("duration")
        is_short = _is_shorts_candidate(canonical_url, duration_seconds)

        thumbs = {
            "thumbnail_default": api_fields.get("thumbnail_default") or "",
            "thumbnail_medium": api_fields.get("thumbnail_medium") or "",
            "thumbnail_high": api_fields.get("thumbnail_high") or "",
            "thumbnail_standard": api_fields.get("thumbnail_standard") or "",
            "thumbnail_maxres": api_fields.get("thumbnail_maxres") or "",
        }
        if not any(thumbs.values()):
            thumbs = _thumbnail_urls(vinfo or {}, video_id=vid)

        like_count = api_fields.get("like_count")
        comment_count = api_fields.get("comment_count")
        if like_count is None:
            like_count = vinfo.get("like_count")
        if comment_count is None:
            comment_count = vinfo.get("comment_count")


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
            "like_count": int(like_count) if isinstance(like_count, int) else like_count,
            "comment_count": int(comment_count) if isinstance(comment_count, int) else comment_count,
            "publishedAt": publishedAt,
            "duration_seconds": duration_seconds,
            "is_short": bool(is_short),
            "channelTitle": channelTitle,
            "channelId": channelId,
            "uploader": uploader,
            "tags": "|".join(tags) if isinstance(tags, list) else (tags or ""),
            "categories": "|".join(categories) if isinstance(categories, list) else (categories or ""),
            "categoryId": categoryId,
            "defaultLanguage": defaultLanguage,
            "defaultAudioLanguage": defaultAudioLanguage,
            **thumbs,
            "transcript_method": t_method,
            "transcript_source": t_source,
            "transcript_format": t_fmt,
            "transcript_status": t_status,
            "transcript": transcript,
        }

        if include_description:
            row["description"] = (vinfo.get("description") or "")

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
