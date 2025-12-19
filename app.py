import streamlit as st
from scraper import scrape_channel
import pandas as pd
import time

st.set_page_config(page_title="YouTube Transcript Scraper", page_icon="🎬", layout="wide")

st.title("🎬 YouTube Transcript Scraper (Channel → Ranked CSV)")
st.caption("Filters a channel to videos above a view threshold, pulls transcripts, ranks results, and lets you download a CSV.")

with st.sidebar:
    st.header("Channel")
    channel_url = st.text_input("YouTube channel URL", placeholder="https://www.youtube.com/@davisfacts")
    content_type = st.selectbox("Content type", ["shorts", "longform", "both"], index=0)

    st.divider()
    st.header("Filter + Ranking")
    min_views = st.number_input("Minimum views", min_value=0, value=300_000, step=10_000, help="Only videos with view_count >= this will be included.")
    rank_by = st.selectbox("Rank by", ["view_count"], index=0)

    st.divider()
    st.header("Scope")
    scrape_entire = st.checkbox("Scan entire channel (can take a while)", value=False)
    scan_limit = st.number_input("Max videos to scan", min_value=1, max_value=50_000, value=800, step=50,
                                help="How many videos to *check* across the channel. Increase for big channels.")
    max_results = st.number_input("Max results to output", min_value=1, max_value=5_000, value=200, step=25,
                                 help="How many qualifying videos to include in the CSV after filtering.")

    st.divider()
    st.header("Transcript")
    language = st.text_input("Transcript language (optional)", value="", help="Example: en. Leave blank to use default when available.")
    include_auto = st.checkbox("Allow auto-generated transcripts", value=True)

    st.divider()
    run_btn = st.button("🚀 Scrape + Rank")

st.info(
    "Tip: Use the channel *root* URL (e.g., `https://www.youtube.com/@name`) even if you're scanning Shorts. "
    "Scanning an entire large channel may hit hosting time limits—start with a smaller scan limit, then increase."
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
            prog.progress(min(1.0, i / total))
        status.text(msg)

    try:
        rows = scrape_channel(
            channel_url=channel_url.strip(),
            content_type=content_type,
            scan_limit=None if scrape_entire else int(scan_limit),
            min_views=int(min_views),
            max_results=int(max_results),
            language=language.strip() or None,
            allow_auto=include_auto,
            progress_cb=on_progress,
        )
    except Exception as e:
        st.exception(e)
        st.stop()

    elapsed = time.time() - start
    status.text(f"Done. Found {len(rows)} qualifying video(s) in {elapsed:.1f}s.")
    prog.progress(1.0)

    if not rows:
        st.warning("No qualifying videos found. Try lowering the minimum views, switching to 'both', or increasing scan limit.")
        st.stop()

    df = pd.DataFrame(rows)

    # Ensure nice column order
    preferred = ["rank", "view_count", "title", "url", "video_id", "transcript"]
    cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    df = df[cols]

    st.write("### Preview")
    st.dataframe(df, use_container_width=True, height=420)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download Ranked CSV",
        data=csv_bytes,
        file_name="channel_transcripts_ranked.csv",
        mime="text/csv"
    )

    st.write("### Transcript Viewer")
    options = [f"{r['rank']}. {r['title']} ({(r.get('view_count') or 0):,} views)" for r in rows]
    choice = st.selectbox("Select a video", options)
    chosen_rank = int(choice.split(".", 1)[0])
    idx = next(i for i, r in enumerate(rows) if r.get("rank") == chosen_rank)
    st.write("**Video URL:**", rows[idx]["url"])
    st.text_area("Transcript", rows[idx].get("transcript") or "", height=260)
