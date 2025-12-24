import os
import io
import pandas as pd
import streamlit as st

from scraper import scrape_channel, scrape_single_video, normalize_channel_url

# -----------------------------
# Page config
# -----------------------------
st.set_page_config(
    page_title="YouTube Transcript + Metadata Scraper",
    page_icon="🎬",
    layout="wide",
)

# -----------------------------
# Theme toggle (CSS-based)
# -----------------------------
DEFAULT_THEME = "light"

def _apply_theme(theme: str):
    # Minimal but effective Streamlit theming via CSS
    if theme == "dark":
        bg = "#0b1220"
        panel = "#111a2c"
        text = "#e8eefc"
        muted = "#a9b6d3"
        border = "rgba(255,255,255,0.08)"
        accent = "#37B7FF"
    else:
        bg = "#ffffff"
        panel = "#f7f8fb"
        text = "#0b1220"
        muted = "#5b667a"
        border = "rgba(12,18,32,0.10)"
        accent = "#1976d2"

    st.markdown(
        f"""
<style>
/* App background */
.stApp {{
  background: {bg};
  color: {text};
}}

/* Headings */
h1, h2, h3, h4, h5, h6, p, div, span, label {{
  color: {text};
}}

/* Sidebar */
section[data-testid="stSidebar"] > div {{
  background: {panel};
  border-right: 1px solid {border};
}}

/* Cards */
.na-card {{
  background: {panel};
  border: 1px solid {border};
  border-radius: 16px;
  padding: 16px 18px;
  margin-bottom: 14px;
}}
.na-muted {{ color: {muted}; }}

/* Inputs */
div[data-baseweb="input"] > div,
div[data-baseweb="textarea"] > div,
div[data-baseweb="select"] > div {{
  background: transparent;
  border-radius: 12px;
}}

/* Buttons */
.stButton>button {{
  border-radius: 12px;
  border: 1px solid {border};
}}
.stDownloadButton>button {{
  border-radius: 12px;
  border: 1px solid {border};
}}

/* Links / accent */
a, a:visited {{ color: {accent}; }}

/* Dataframe */
div[data-testid="stDataFrame"] {{
  border: 1px solid {border};
  border-radius: 16px;
  overflow: hidden;
}}
</style>
        """,
        unsafe_allow_html=True,
    )


if "theme" not in st.session_state:
    st.session_state.theme = DEFAULT_THEME

_apply_theme(st.session_state.theme)

# -----------------------------
# Sidebar controls
# -----------------------------
with st.sidebar:
    st.markdown("## ⚙️ Settings")

    theme_toggle = st.toggle("🌙 Dark mode", value=(st.session_state.theme == "dark"))
    st.session_state.theme = "dark" if theme_toggle else "light"
    _apply_theme(st.session_state.theme)

    # API key: env -> secrets -> user input
    env_key = os.getenv("YOUTUBE_API_KEY") or ""
    secrets_key = ""
    try:
        secrets_key = st.secrets.get("YOUTUBE_API_KEY", "")
    except Exception:
        secrets_key = ""

    default_key = env_key or secrets_key

    api_key = st.text_input(
        "YouTube Data API Key",
        value=st.session_state.get("api_key", default_key),
        type="password",
        help="Stored only in your browser session. In Streamlit Cloud, set it in Secrets as YOUTUBE_API_KEY.",
    )
    st.session_state.api_key = api_key

    st.markdown("---")
    st.markdown("### 🍪 Optional cookies.txt")
    cookies_file = st.file_uploader(
        "Upload cookies.txt (optional)",
        type=["txt"],
        help="Helps yt-dlp access captions when YouTube blocks anonymous requests. Export in Netscape format.",
    )

    cookies_path = None
    if cookies_file is not None:
        # Save to a temp path Streamlit can read
        cookies_path = os.path.join(os.getcwd(), "cookies.txt")
        with open(cookies_path, "wb") as f:
            f.write(cookies_file.getvalue())

    st.session_state.cookies_path = cookies_path

    st.markdown("---")
    st.markdown("### 🔎 Defaults")
    max_videos = st.number_input("Max videos", min_value=1, max_value=2000, value=200, step=25)
    only_shorts = st.toggle("Only Shorts (<= 60s)", value=True)

# -----------------------------
# Header
# -----------------------------
st.title("🎬 YouTube Transcript + Metadata Scraper")
st.caption("Scrape videos using the YouTube Data API and attempt to fetch public transcripts (with fallbacks).")

# Navigation
tabs = st.tabs(["📥 Channel Scrape", "🎯 Single Video", "ℹ️ Help"])

# -----------------------------
# Tab 1: Channel scrape
# -----------------------------
with tabs[0]:
    st.markdown(
        """
<div class="na-card">
  <div style="font-size:18px; font-weight:700;">Channel → CSV export</div>
  <div class="na-muted" style="margin-top:6px;">
    Paste a channel URL or handle (e.g., @davisfacts). The app will export a CSV with metadata + transcript columns.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([2, 1])
    with col1:
        channel_url = st.text_input("YouTube Channel URL or Handle", value=st.session_state.get("channel_url", "https://www.youtube.com/@davisfacts"))
        st.session_state.channel_url = channel_url
    with col2:
        run = st.button("🚀 Run scrape", use_container_width=True)

    if run:
        if not st.session_state.api_key:
            st.error("Please enter your YouTube Data API key in the sidebar.")
        else:
            channel_url_norm = normalize_channel_url(channel_url)

            prog = st.progress(0)
            status = st.empty()

            def on_progress(done, total, msg):
                total = max(total, 1)
                prog.progress(min(done / total, 1.0))
                status.markdown(f"**{msg}**")

            try:
                rows = scrape_channel(
                    channel_url_norm,
                    api_key=st.session_state.api_key,
                    max_videos=int(max_videos),
                    only_shorts=only_shorts,
                    cookies_path=st.session_state.cookies_path,
                    on_progress=on_progress,
                )
            except Exception as e:
                st.error(f"Scrape failed: {e}")
                rows = []

            if rows:
                df = pd.DataFrame(rows)

                # Summary
                ok = int((df["transcript"].str.len().fillna(0) > 0).sum())
                fail = len(df) - ok

                st.markdown(
                    f"""
<div class="na-card">
  <div style="display:flex; gap:18px; flex-wrap:wrap;">
    <div><div class="na-muted">Videos scraped</div><div style="font-size:22px; font-weight:800;">{len(df)}</div></div>
    <div><div class="na-muted">Transcripts found</div><div style="font-size:22px; font-weight:800;">{ok}</div></div>
    <div><div class="na-muted">Missing / failed</div><div style="font-size:22px; font-weight:800;">{fail}</div></div>
  </div>
</div>
                    """,
                    unsafe_allow_html=True,
                )

                # Show failures first
                with st.expander("Show transcript failures (top)", expanded=(fail > 0)):
                    failures = df[df["transcript"].fillna("").str.len() == 0][
                        ["rank", "title", "url", "transcript_source", "transcript_error"]
                    ].head(50)
                    if len(failures) == 0:
                        st.write("No failures 🎉")
                    else:
                        st.dataframe(failures, use_container_width=True, hide_index=True)

                st.subheader("Preview")
                st.dataframe(df.head(25), use_container_width=True, hide_index=True)

                # Download CSV
                csv_bytes = df.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇️ Download CSV",
                    data=csv_bytes,
                    file_name="channel_videos_with_transcripts.csv",
                    mime="text/csv",
                    use_container_width=True,
                )
            else:
                st.warning("No rows returned. Try increasing Max videos, disabling 'Only Shorts', or adding cookies.txt.")


# -----------------------------
# Tab 2: Single video
# -----------------------------
with tabs[1]:
    st.markdown(
        """
<div class="na-card">
  <div style="font-size:18px; font-weight:700;">Single video → transcript</div>
  <div class="na-muted" style="margin-top:6px;">
    Paste a YouTube URL to fetch metadata + transcript for one video.
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    video_url = st.text_input("YouTube Video URL", value="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    run_one = st.button("🎯 Fetch video", use_container_width=True)

    if run_one:
        if not st.session_state.api_key:
            st.error("Please enter your YouTube Data API key in the sidebar.")
        else:
            try:
                row = scrape_single_video(video_url, api_key=st.session_state.api_key, cookies_path=st.session_state.cookies_path)
                st.success("Done.")
                st.write("**Title:**", row.get("title"))
                st.write("**Views:**", row.get("view_count"))
                st.write("**Duration (s):**", row.get("duration_seconds"))
                st.write("**Transcript source:**", row.get("transcript_source") or "—")
                if row.get("transcript_error"):
                    st.warning(row["transcript_error"])
                st.text_area("Transcript", value=row.get("transcript") or "", height=250)
            except Exception as e:
                st.error(f"Failed: {e}")

# -----------------------------
# Tab 3: Help
# -----------------------------
with tabs[2]:
    st.markdown(
        """
<div class="na-card">
  <div style="font-size:18px; font-weight:700;">How to use</div>
  <ol>
    <li>Put your <b>YouTube Data API key</b> in the sidebar.</li>
    <li>(Optional) Upload a <b>cookies.txt</b> if transcripts come back empty.</li>
    <li>Run <b>Channel Scrape</b> and download the CSV.</li>
  </ol>
  <div class="na-muted">If transcripts are missing, it usually means the video has transcripts disabled/unavailable or YouTube blocked anonymous caption access.</div>
</div>

<div class="na-card">
  <div style="font-size:18px; font-weight:700;">Streamlit Cloud secrets</div>
  <div class="na-muted" style="margin-top:6px;">
    In Streamlit Cloud: App → Settings → Secrets → add:
    <pre>YOUTUBE_API_KEY = "YOUR_KEY"</pre>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )
