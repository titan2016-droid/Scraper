import streamlit as st
from scraper import scrape_channel
import pandas as pd
import time

st.set_page_config(page_title="YouTube Transcript Scraper", page_icon="🎬", layout="wide")
st.title("🎬 YouTube Transcript Scraper (Channel → CSV)")
st.caption("Paste a YouTube channel URL, choose Shorts/Longform, scrape titles + URLs + view counts + transcripts, then download a CSV.")

with st.sidebar:
    st.header("Settings")
    channel_url = st.text_input("YouTube channel URL", placeholder="https://www.youtube.com/@davisfacts")
    content_type = st.selectbox("Content type", ["shorts", "longform"])
    max_videos = st.number_input("Max videos", min_value=1, max_value=200, value=25, step=1)
    language = st.text_input("Transcript language (optional)", value="", help="Leave blank to use the default transcript if available. Example: en")
    include_auto = st.checkbox("Allow auto-generated transcripts", value=True)
    run_btn = st.button("🚀 Scrape")

st.info(
    "Tip: Use the *channel root* URL (e.g., `https://www.youtube.com/@name`) even if you're scraping Shorts. "
    "The app will handle Shorts vs Longform based on your selection."
)

if run_btn:
    if not channel_url.strip():
        st.error("Please enter a channel URL.")
        st.stop()

    st.write("### Progress")
    prog = st.progress(0)
    status = st.empty()

    start = time.time()

    def on_progress(i, total, msg):
        if total > 0:
            prog.progress(min(1.0, i/total))
        status.text(msg)

    try:
        rows = scrape_channel(
            channel_url=channel_url.strip(),
            content_type=content_type,
            max_videos=int(max_videos),
            language=language.strip() or None,
            allow_auto=include_auto,
            progress_cb=on_progress
        )
    except Exception as e:
        st.exception(e)
        st.stop()

    elapsed = time.time() - start
    status.text(f"Done. Scraped {len(rows)} video(s) in {elapsed:.1f}s.")
    prog.progress(1.0)

    if not rows:
        st.warning("No videos returned. Try the channel root URL (without `/shorts`) or reduce max videos.")
        st.stop()

    df = pd.DataFrame(rows)
    st.write("### Preview")
    st.dataframe(df, use_container_width=True, height=420)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download CSV",
        data=csv_bytes,
        file_name="channel_transcripts.csv",
        mime="text/csv"
    )

    st.write("### Transcript Viewer")
    options = [f"{i+1}. {r['title']}" for i, r in enumerate(rows)]
    choice = st.selectbox("Select a video", options)
    idx = int(choice.split(".", 1)[0]) - 1
    st.write("**Video URL:**", rows[idx]["url"])
    st.text_area("Transcript", rows[idx].get("transcript") or "", height=260)
