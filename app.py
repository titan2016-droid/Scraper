\
from __future__ import annotations

import os
import time
from typing import List, Optional

import pandas as pd
import streamlit as st

from scraper import normalize_channel_url, scrape_channel


# ---------------------------
# Page
# ---------------------------
st.set_page_config(
    page_title="YouTube Transcript + Metadata Scraper",
    page_icon="🧾",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------
# Theme (CSS)
# ---------------------------
def apply_theme(is_dark: bool) -> None:
    # Streamlit's base theming is static (config.toml). We do dynamic theming by overriding CSS variables.
    if is_dark:
        css = """
        <style>
          :root{
            --bg:#0B1220;
            --panel:#111A2E;
            --card:#0F172A;
            --muted:#94A3B8;
            --text:#E5E7EB;
            --border:rgba(148,163,184,0.18);
            --primary:#6366F1;
            --primary2:#A78BFA;
            --shadow:0 10px 25px rgba(0,0,0,.35);
          }
          .stApp { background: var(--bg) !important; color: var(--text) !important; }
          [data-testid="stSidebar"] { background: var(--panel) !important; border-right: 1px solid var(--border) !important; }
          [data-testid="stSidebar"] * { color: var(--text) !important; }
          .block-container { padding-top: 2.0rem; padding-bottom: 3rem; }
          .na-card{
            background: rgba(255,255,255,0.04);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 18px 18px;
            box-shadow: var(--shadow);
          }
          .na-title{ font-size: 30px; font-weight: 750; letter-spacing: -0.02em; margin-bottom: 4px; }
          .na-sub{ color: var(--muted); margin-top: 0; }
          .na-section-title{ font-size: 18px; font-weight: 700; margin: 8px 0 10px; }
          /* inputs */
          [data-testid="stTextInput"] input,
          [data-testid="stNumberInput"] input,
          [data-testid="stTextArea"] textarea,
          [data-testid="stSelectbox"] div[role="combobox"]{
            background: rgba(255,255,255,0.06) !important;
            border: 1px solid var(--border) !important;
            border-radius: 12px !important;
          }
          /* buttons */
          .stButton > button{
            border-radius: 12px !important;
            border: 1px solid var(--border) !important;
            background: linear-gradient(135deg, var(--primary), var(--primary2)) !important;
            color: white !important;
            padding: 0.7rem 1rem !important;
            font-weight: 700 !important;
          }
          /* dataframe */
          .stDataFrame { border: 1px solid var(--border) !important; border-radius: 16px !important; overflow: hidden; }
        </style>
        """
    else:
        css = """
        <style>
          :root{
            --bg:#FFFFFF;
            --panel:#F7F8FC;
            --card:#FFFFFF;
            --muted:#475569;
            --text:#0F172A;
            --border:rgba(15,23,42,0.10);
            --primary:#4F46E5;
            --primary2:#06B6D4;
            --shadow:0 10px 25px rgba(2,6,23,.08);
          }
          .stApp { background: var(--bg) !important; color: var(--text) !important; }
          [data-testid="stSidebar"] { background: var(--panel) !important; border-right: 1px solid var(--border) !important; }
          .block-container { padding-top: 2.0rem; padding-bottom: 3rem; }
          .na-card{
            background: var(--card);
            border: 1px solid var(--border);
            border-radius: 18px;
            padding: 18px 18px;
            box-shadow: var(--shadow);
          }
          .na-title{ font-size: 30px; font-weight: 750; letter-spacing: -0.02em; margin-bottom: 4px; }
          .na-sub{ color: var(--muted); margin-top: 0; }
          .na-section-title{ font-size: 18px; font-weight: 700; margin: 8px 0 10px; }
          /* inputs */
          [data-testid="stTextInput"] input,
          [data-testid="stNumberInput"] input,
          [data-testid="stTextArea"] textarea,
          [data-testid="stSelectbox"] div[role="combobox"]{
            background: #FFFFFF !important;
            border: 1px solid var(--border) !important;
            border-radius: 12px !important;
          }
          /* buttons */
          .stButton > button{
            border-radius: 12px !important;
            border: 1px solid var(--border) !important;
            background: linear-gradient(135deg, var(--primary), var(--primary2)) !important;
            color: white !important;
            padding: 0.7rem 1rem !important;
            font-weight: 700 !important;
          }
          .stDataFrame { border: 1px solid var(--border) !important; border-radius: 16px !important; overflow: hidden; }
        </style>
        """
    st.markdown(css, unsafe_allow_html=True)


if "dark_mode" not in st.session_state:
    st.session_state.dark_mode = False


# Sidebar
with st.sidebar:
    st.markdown("### 🧾 YouTube Transcript + Metadata Scraper")
    st.caption("Scrape channel videos, pull metadata + transcripts, export a CSV for your AI analysis.")
    st.session_state.dark_mode = st.toggle("🌙 Dark mode", value=st.session_state.dark_mode)
    apply_theme(st.session_state.dark_mode)

    st.markdown("---")
    st.markdown("#### 🔑 API Key")

    # Key can come from secrets/env OR user input
    default_key = (
        st.secrets.get("YT_API_KEY", "")
        if hasattr(st, "secrets")
        else ""
    ) or os.getenv("YT_API_KEY", "")

    yt_api_key = st.text_input(
        "YouTube Data API v3 key",
        value=default_key,
        type="password",
        placeholder="Paste your YouTube Data API key…",
        help="Recommended: set this as a Streamlit Secret named YT_API_KEY so you don't paste it every time.",
    )

    st.markdown("---")
    st.markdown("#### ⚙️ Scrape settings")

    content_type = st.selectbox("Content type", options=["both", "shorts", "videos"], index=0)
    scan_limit = st.number_input("Scan limit", min_value=1, max_value=2000, value=300, step=50,
                                 help="How many videos to scan from the channel's uploads playlist.")
    min_views = st.number_input("Min views", min_value=0, max_value=10_000_000_000, value=0, step=1000,
                                help="Skip low-view videos (faster).")
    popular_first = st.toggle("Popular-first (faster insights)", value=True,
                              help="Sort by view count so your top performers appear first.")
    include_transcripts = st.toggle("Include transcripts", value=True)
    lang = st.text_input("Transcript languages (comma)", value="en",
                         help="Example: en,es. We'll try these languages in order.")
    transcript_languages: Optional[List[str]] = [x.strip() for x in (lang or "").split(",") if x.strip()] or None

    st.markdown("---")
    st.info(
        "Tip: If you get 0 transcripts, it's often because the creator disabled captions.\n"
        "Try another channel, or leave transcripts OFF and use metadata-only."
    )


# Main layout
st.markdown(
    """
    <div class="na-card">
      <div class="na-title">Scrape a channel → Export CSV</div>
      <div class="na-sub">Paste a YouTube channel URL, run the scrape, preview the dataset, then download the CSV.</div>
    </div>
    """,
    unsafe_allow_html=True,
)
st.write("")

left, right = st.columns([1.15, 1])

with left:
    st.markdown('<div class="na-section-title">Channel</div>', unsafe_allow_html=True)
    channel_url = st.text_input(
        "YouTube Channel URL",
        value="https://www.youtube.com/@davisfacts",
        placeholder="https://www.youtube.com/@handle  OR  https://www.youtube.com/channel/UC…",
    )
    channel_url = normalize_channel_url(channel_url)

    run = st.button("🚀 Run scrape", use_container_width=True)

with right:
    st.markdown('<div class="na-section-title">Output</div>', unsafe_allow_html=True)
    st.caption("You’ll get one row per video with title, URL, views, publish date, and transcript (when available).")
    status_box = st.empty()

# Run scrape
if run:
    if not yt_api_key:
        st.error("Please add your YouTube Data API key in the sidebar.")
        st.stop()

    debug: List[str] = []
    status_box.info("Starting scrape…")

    t0 = time.time()
    try:
        rows, debug = scrape_channel(
            channel_url=channel_url,
            api_key=yt_api_key,
            content_type=content_type,
            scan_limit=int(scan_limit),
            min_views=int(min_views),
            popular_first=bool(popular_first),
            include_transcripts=bool(include_transcripts),
            transcript_languages=transcript_languages,
            debug=debug,
        )
    except Exception as e:
        status_box.error(f"Scrape failed: {e}")
        with st.expander("Debug log"):
            st.code("\n".join(debug) if debug else "No debug info.")
        st.stop()

    took = time.time() - t0
    status_box.success(f"Done in {took:.1f}s • {len(rows)} video(s) returned.")

    if not rows:
        st.warning("Returned 0 videos. Lower min_views, increase scan_limit, or verify the channel URL.")
        with st.expander("Debug log"):
            st.code("\n".join(debug))
        st.stop()

    df = pd.DataFrame(rows)

    preferred = [
        "rank",
        "view_count",
        "title",
        "url",
        "published_at",
        "duration_seconds",
        "like_count",
        "comment_count",
        "tags",
        "thumbnail",
        "transcript",
        "transcript_error",
        "channel_title",
        "channel_id",
        "video_id",
    ]
    ordered = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
    df = df[ordered]

    st.write("")
    st.dataframe(df, use_container_width=True, height=560)

    csv_bytes = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download CSV",
        data=csv_bytes,
        file_name="channel_videos_with_transcripts.csv",
        mime="text/csv",
        use_container_width=True,
    )

    with st.expander("Debug log (advanced)"):
        st.code("\n".join(debug))
