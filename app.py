import streamlit as st
import pandas as pd

from scraper import scrape_channel, normalize_channel_url


st.set_page_config(page_title="YouTube Channel Transcript Scraper", layout="wide")
st.title("YouTube Channel Transcript Scraper")
st.caption("Fetch shorts/longform videos, filter by views, and export metadata + transcripts to CSV.")

with st.sidebar:
    st.header("Channel")
    yt_api_key = st.text_input(
        "YouTube Data API v3 Key (recommended)",
        type="password",
        help="Needed for reliable view counts + metadata + sorting by views (Popular-first).",
    )

cookies_file = st.file_uploader(
    "Optional: cookies.txt (recommended for transcripts)",
    type=["txt"],
    help="Some transcripts are blocked for server IPs. Upload a cookies.txt exported from your browser to fetch transcripts more reliably.",
)
    channel_url = st.text_input("YouTube Channel URL", value="https://www.youtube.com/@davisfacts")
    content_type = st.selectbox("Content type", ["shorts", "longform", "both"], index=0)
    min_views = st.number_input("Minimum views", min_value=0, value=300000, step=10000)
    max_results = st.number_input("Max results to return", min_value=1, value=100, step=10)
    scan_limit = st.number_input("Scan limit (how many uploads to inspect)", min_value=50, value=600, step=50)

    st.divider()
    st.header("Transcripts")
    language = st.text_input("Preferred transcript language (optional)", value="")
    allow_auto = st.checkbox("Allow auto-generated captions", value=True)

    st.divider()
    st.header("Sorting / speed")
    popular_first = st.checkbox("Popular-first (sort by views desc)", value=True)
    early_stop = st.checkbox("Early stop below threshold", value=True, help="Stops once we hit many consecutive videos below min views (only when sorted by views).")

    run_btn = st.button("Start Scraping", type="primary")

if run_btn:
    if not channel_url.strip():
        st.error("Please enter a YouTube channel URL.")
        st.stop()

    if not yt_api_key.strip():
        st.warning("No YouTube API key provided. Add a key for best results.")

    st.info(f"Channel: {normalize_channel_url(channel_url)}")
    progress = st.progress(0, text="Starting...")

    def cb(i, total, msg):
        if total <= 0:
            progress.progress(0, text=msg)
            return
        progress.progress(min(i / total, 1.0), text=msg)

    rows, debug = scrape_channel(
        channel_url=channel_url,
        cookies_txt=(cookies_file.getvalue() if cookies_file else None),
        youtube_api_key=(yt_api_key.strip() or None),
        content_type=content_type,
        scan_limit=int(scan_limit),
        min_views=int(min_views),
        max_results=int(max_results),
        language=(language.strip() or None),
        allow_auto=allow_auto,
        popular_first=popular_first,
        early_stop=early_stop,
        progress_cb=cb,
        return_debug=True,
    )

    progress.progress(1.0, text=f"Done. Returning {len(rows)} video(s).")

    if not rows:
        st.error("0 qualifying videos returned. See Debug (advanced) below.")
    else:
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", data=csv, file_name="channel_videos_with_transcripts.csv", mime="text/csv")

    with st.expander("Debug (advanced)", expanded=True):
        st.code("\n".join(debug))
