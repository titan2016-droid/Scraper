import streamlit as st
import pandas as pd
from scraper import scrape_channel, normalize_channel_url

st.set_page_config(page_title="YouTube Transcript + Metadata Scraper", layout="wide")

st.title("YouTube Transcript + Metadata Scraper")
st.caption("Fetch top videos (by views) and export metadata + transcripts to CSV. Best results on Streamlit Cloud require a YouTube Data API key; transcripts may require cookies for some channels/videos.")

with st.sidebar:
    st.header("Inputs")

    yt_api_key = st.text_input(
        "YouTube Data API v3 Key (recommended)",
        type="password",
        help="If provided, the app fetches reliable view counts + full metadata and sorts by views.",
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
        help="Higher can find more qualifying videos but takes longer. With API key we will scan up to this many items from uploads.",
    )

    popular_first = st.checkbox(
        "Popular-first (only used when NO API key)",
        value=True,
        help="When no API key is provided, the scraper tries to start from YouTube's Popular sort URL.",
    )

    early_stop = st.checkbox(
        "Early-stop when views drop (only used with Popular-first)",
        value=True,
        help="Stops after several consecutive videos below min views. Disable if you suspect ordering is wrong.",
    )

run = st.button("Start scraping", type="primary")

if run:
    if not channel_url.strip():
        st.error("Please enter a channel URL.")
        st.stop()

    norm = normalize_channel_url(channel_url.strip())
    st.write(f"Normalized channel URL: {norm}")

    with st.spinner("Scraping…"):
        rows, debug = scrape_channel(
            channel_url=norm,
            youtube_api_key=(yt_api_key.strip() or None),
            cookies_txt_bytes=cookies_bytes,
            content_type=content_type,
            min_views=int(min_views),
            max_results=int(max_results),
            scan_limit=int(scan_limit),
            popular_first=bool(popular_first),
            early_stop=bool(early_stop),
        )

    st.success(f"Done. Returning {len(rows)} video(s).")

    with st.expander("Debug (advanced)"):
        st.code("\n".join(debug))

    if not rows:
        st.warning("No qualifying videos returned. If you used min_views, try lowering it, increasing scan_limit, and make sure you provided a YouTube API key.")
        st.stop()

    df = pd.DataFrame(rows)

    cols = list(df.columns)
    for c in ["rank", "view_count", "title", "url", "publishedAt", "transcript", "transcript_error"]:
        if c in cols:
            cols.remove(c)
    ordered = [c for c in ["rank", "view_count", "title", "url", "publishedAt", "transcript", "transcript_error"] if c in df.columns] + cols
    df = df[ordered]

    st.dataframe(df, use_container_width=True, height=520)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV", data=csv_bytes, file_name="channel_videos_with_transcripts.csv", mime="text/csv")
