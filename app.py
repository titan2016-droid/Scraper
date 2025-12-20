import streamlit as st
import pandas as pd
from scraper import scrape_channel, normalize_channel_url

st.set_page_config(page_title="YouTube Transcript + Metadata Scraper", layout="wide")

st.title("YouTube Transcript + Metadata Scraper")
st.caption("Fast mode: uses YouTube Data API for view counts + sorting. Then fetches transcripts only for qualifying videos.")

with st.sidebar:
    st.header("Inputs")

    yt_api_key = st.text_input(
        "YouTube Data API v3 Key (required for fast results)",
        type="password",
        help="This app uses the API key to pull view counts + metadata quickly. Without it, runs can be slow or return 0 on Streamlit Cloud.",
    )

    cookies_file = st.file_uploader(
        "Optional: cookies.txt (recommended for transcripts)",
        type=["txt"],
        help="Export cookies.txt from your browser (logged into YouTube) to access transcripts that are blocked for server traffic.",
    )
    cookies_bytes = cookies_file.getvalue() if cookies_file else None

    channel_url = st.text_input(
        "YouTube Channel URL",
        value="https://www.youtube.com/@davisfacts",
        help="Supports @handle, /channel/UC..., or full channel URL.",
    )

    content_type = st.selectbox("Content type", ["shorts", "longform", "both"], index=0)
    min_views = st.number_input("Min views (filter)", min_value=0, value=300000, step=10000)
    max_results = st.number_input("Max qualifying videos to return", min_value=1, max_value=5000, value=100, step=10)

    scan_limit = st.number_input(
        "Scan limit (how many channel videos to consider)",
        min_value=50,
        max_value=5000,
        value=800,
        step=50,
        help="Higher can find more qualifying videos.",
    )

run = st.button("Start scraping", type="primary")

if run:
    if not yt_api_key.strip():
        st.error("Please paste a YouTube Data API v3 key. Without it, runs can be very slow and often return 0 results on Streamlit Cloud.")
        st.stop()

    if not channel_url.strip():
        st.error("Please enter a channel URL.")
        st.stop()

    norm = normalize_channel_url(channel_url.strip())
    st.write(f"Normalized channel URL: {norm}")

    progress = st.progress(0)
    status = st.empty()

    def on_progress(i, n, msg):
        if n > 0:
            progress.progress(min(1.0, i / n))
        status.write(msg)

    rows, debug = scrape_channel(
        channel_url=norm,
        youtube_api_key=yt_api_key.strip(),
        cookies_txt_bytes=cookies_bytes,
        content_type=content_type,
        min_views=int(min_views),
        max_results=int(max_results),
        scan_limit=int(scan_limit),
        on_progress=on_progress,
    )

    st.success(f"Done. Returning {len(rows)} video(s).")

    with st.expander("Debug (advanced)"):
        st.code("\n".join(debug))

    if not rows:
        st.warning("Returned 0 videos. Lower min_views and/or increase scan_limit. Also verify the channel URL resolves correctly.")
        st.stop()

    df = pd.DataFrame(rows)
    preferred = ["rank","view_count","title","url","publishedAt","duration_seconds","like_count","comment_count","channelTitle","channelId","tags","thumbnail","transcript","transcript_error"]
    ordered = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    df = df[ordered]

    st.dataframe(df, use_container_width=True, height=520)
    st.download_button("Download CSV", data=df.to_csv(index=False).encode("utf-8"), file_name="channel_videos_with_transcripts.csv", mime="text/csv")
