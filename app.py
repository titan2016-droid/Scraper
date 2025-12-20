import streamlit as st
from scraper import scrape_channel, normalize_channel_url
import pandas as pd
import time
import tempfile
import os

st.set_page_config(page_title="YouTube Transcript Scraper", page_icon="🎬", layout="wide")

st.title("🎬 YouTube Transcript Scraper — Popular-first + Transcripts + Full Metadata")
st.caption("Filters 300k+ views, ranks results, and outputs **video metadata + transcript** in one CSV.")

with st.sidebar:
    st.header("Channel")
    yt_api_key = st.text_input("YouTube Data API v3 Key (optional)", type="password", help="If provided, we use it to fetch reliable view counts + metadata (recommended on Streamlit Cloud).")
    channel_url_in = st.text_input("YouTube channel URL", placeholder="https://www.youtube.com/@davisfacts (no /shorts)")
    content_type = st.selectbox("Content type", ["shorts", "longform", "both"], index=0)

    st.divider()
    st.header("Scan Strategy")
    popular_first = st.checkbox("Start from 'Popular' order", value=True)
    early_stop = st.checkbox("Stop scanning when views drop below minimum", value=True)

    st.divider()
    st.header("Filter + Ranking")
    min_views = st.number_input("Minimum views", min_value=0, value=300_000, step=10_000)
    max_results = st.number_input("Max results to output", min_value=1, max_value=5_000, value=150, step=25)

    st.divider()
    st.header("Scope")
    scan_limit = st.number_input("Max videos to scan", min_value=1, max_value=50_000, value=600, step=50)

    st.divider()
    st.header("Transcript method")
    method = st.radio(
        "How should we get transcripts?",
        ["Auto (Captions → Audio Transcribe)", "Captions only", "Audio transcribe only"],
        index=0,
    )
    model = st.selectbox("Audio transcription model", ["gpt-4o-mini-transcribe", "gpt-4o-transcribe", "whisper-1"], index=0)
    st.caption("For audio transcription, set OPENAI_API_KEY in Streamlit Secrets.")

    st.divider()
    st.header("Captions options")
    language = st.text_input("Preferred caption language (optional)", value="", help="Example: en (used for captions).")
    include_auto = st.checkbox("Allow auto-generated captions", value=True)

    st.divider()
    st.header("Output")
    show_errors = st.checkbox("Include transcript error details in CSV", value=True)
    include_raw_description = st.checkbox("Include full description column", value=False, help="Descriptions can be long; leaving off keeps CSV lighter.")

    st.divider()
    st.header("Cookies (optional)")
    cookies_file = st.file_uploader("Upload cookies.txt", type=["txt"])

    st.divider()
    run_btn = st.button("🚀 Scrape + Export CSV")

if channel_url_in.strip():
    normalized = normalize_channel_url(channel_url_in.strip())
    if normalized != channel_url_in.strip():
        st.warning(f"Normalized channel URL to: {normalized}")

cookies_path = None
tmp_dir = None
if cookies_file is not None:
    tmp_dir = tempfile.mkdtemp(prefix="ytcookies_")
    cookies_path = os.path.join(tmp_dir, "cookies.txt")
    with open(cookies_path, "wb") as f:
        f.write(cookies_file.getbuffer())

st.info(
    "This version exports metadata like published date, channel ID, tags, languages, and thumbnail URLs "
    "alongside views + transcript."
)

if run_btn:
    if early_stop and (yt_api_key.strip() == ""):
        st.warning("Early-stop can return 0 results on Streamlit Cloud if YouTube blocks view counts. If you get 0 results, add a YouTube Data API key, or disable Early-stop.")
    if not channel_url_in.strip():
        st.error("Please enter a channel URL.")
        st.stop()

    channel_url = normalize_channel_url(channel_url_in.strip())

    st.write("### Progress")
    prog = st.progress(0)
    status = st.empty()
    start = time.time()

    def on_progress(i, total, msg):
        if total > 0:
            prog.progress(min(1.0, i / total))
        status.text(msg)

    rows, debug = scrape_channel(
        channel_url=channel_url,
        youtube_api_key=(yt_api_key.strip() or None),
        content_type=content_type,
        scan_limit=int(scan_limit),
        min_views=int(min_views),
        max_results=int(max_results),
        language=language.strip() or None,
        allow_auto=include_auto,
        include_error_details=show_errors,
        cookiefile=cookies_path,
        popular_first=popular_first,
        early_stop=early_stop,
        transcript_mode=method,
        openai_model=model,
        progress_cb=on_progress,
        return_debug=True,
        include_description=include_raw_description,
    )

    elapsed = time.time() - start
    status.text(f"Done. Returning {len(rows)} video(s) in {elapsed:.1f}s.")
    prog.progress(1.0)

    with st.expander("Debug (advanced)"):
        st.code("\n".join(debug))

    if not rows:
        st.error("0 qualifying videos returned. Try increasing scan_limit, switching content type to 'both', or disabling Popular-first.")
        st.stop()

    df = pd.DataFrame(rows)

    st.write("### Transcript status summary")
    if "transcript_status" in df.columns:
        st.dataframe(df["transcript_status"].value_counts().rename_axis("status").reset_index(name="count"),
                     use_container_width=True, height=240)

    preferred = [
        "rank",
        "view_count",
        "like_count",
        "comment_count",
        "publishedAt",
        "duration_seconds",
        "is_short",
        "title",
        "video_id",
        "url",
        "channelTitle",
        "channelId",
        "uploader",
        "tags",
        "categories",
        "categoryId",
        "defaultLanguage",
        "defaultAudioLanguage",
        "thumbnail_default",
        "thumbnail_medium",
        "thumbnail_high",
        "thumbnail_standard",
        "thumbnail_maxres",
        "transcript_method",
        "transcript_source",
        "transcript_format",
        "transcript_status",
        "transcript",
        "transcript_error",
    ]
    cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    df = df[cols]

    st.write("### Preview")
    st.dataframe(df, use_container_width=True, height=480)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download CSV",
        data=csv_bytes,
        file_name="channel_transcripts_ranked.csv",
        mime="text/csv"
    )
