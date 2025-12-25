import os
from typing import Optional, List, Dict, Any
import streamlit as st
from dotenv import load_dotenv

from scraper import scrape_channel, normalize_channel_url

APP_TITLE = "YouTube Transcript + Metadata Scraper"
APP_TAGLINE = "Scrape a channel → pull metadata + transcripts → export a clean CSV."


# ---------------------------
# Theme / UI helpers
# ---------------------------

def _get_api_key_from_env_or_secrets() -> Optional[str]:
    # Streamlit secrets (preferred for Streamlit Cloud)
    try:
        if "YOUTUBE_API_KEY" in st.secrets:
            return str(st.secrets["YOUTUBE_API_KEY"]).strip()
    except Exception:
        pass

    # .env / environment variables
    load_dotenv()
    for k in ("YOUTUBE_API_KEY", "YT_API_KEY", "YOUTUBE_DATA_API_KEY"):
        v = os.getenv(k)
        if v and v.strip():
            return v.strip()
    return None


def _inject_theme_css(dark: bool) -> None:
    """Hard-override Streamlit surfaces so the toggle actually changes the whole app."""
    if dark:
        bg = "#0B1220"
        surface = "#0F172A"
        surface2 = "#111C33"
        text = "#E6EAF2"
        muted = "#A7B0C3"
        border = "rgba(255,255,255,.10)"
        primary = "#60A5FA"
        shadow = "0 12px 30px rgba(0,0,0,.35)"
        code_bg = "#0A1020"
        scheme = "dark"
    else:
        bg = "#F3F5FA"
        surface = "#FFFFFF"
        surface2 = "#F8FAFF"
        text = "#0B1220"
        muted = "#5B6478"
        border = "rgba(15,23,42,.10)"
        primary = "#2563EB"
        shadow = "0 10px 26px rgba(2,6,23,.08)"
        code_bg = "#F3F4F6"
        scheme = "light"

    css = f"""
    <style>
      :root {{
        --nu-bg: {bg};
        --nu-surface: {surface};
        --nu-surface2: {surface2};
        --nu-text: {text};
        --nu-muted: {muted};
        --nu-border: {border};
        --nu-primary: {primary};
        --nu-shadow: {shadow};
        --nu-code: {code_bg};
      }}

      /* Force the whole page background */
      html, body {{
        background: var(--nu-bg) !important;
        color: var(--nu-text) !important;
        color-scheme: {scheme};
      }}

      /* Main viewport + sidebar */
      [data-testid="stAppViewContainer"] {{
        background: var(--nu-bg) !important;
      }}
      [data-testid="stSidebar"] > div:first-child {{
        background: var(--nu-surface) !important;
        border-right: 1px solid var(--nu-border) !important;
      }}

      /* Make header transparent (so bg shows) */
      [data-testid="stHeader"] {{
        background: rgba(0,0,0,0) !important;
      }}

      /* Global layout */
      .block-container {{
        padding-top: 1.75rem;
        padding-bottom: 3rem;
        max-width: 1200px;
      }}

      /* Typography */
      .nu-title {{
        font-size: 2.1rem;
        font-weight: 850;
        letter-spacing: -0.03em;
        line-height: 1.1;
        margin: 0;
      }}
      .nu-sub {{
        color: var(--nu-muted);
        margin: .35rem 0 0 0;
        font-size: 1rem;
      }}

      /* Cards */
      .nu-card {{
        background: var(--nu-surface);
        border: 1px solid var(--nu-border);
        border-radius: 18px;
        padding: 18px 18px 14px 18px;
        box-shadow: var(--nu-shadow);
      }}
      .nu-card h3 {{
        margin: 0 0 .25rem 0;
        font-size: 1.1rem;
      }}
      .nu-card p {{
        margin: 0;
        color: var(--nu-muted);
      }}

      /* Inputs */
      div[data-baseweb="input"] > div,
      div[data-baseweb="select"] > div,
      div[data-baseweb="textarea"] > div {{
        background: var(--nu-surface2) !important;
        border-color: var(--nu-border) !important;
        border-radius: 14px !important;
      }}

      /* Buttons */
      .stButton > button {{
        border-radius: 14px !important;
        padding: .75rem 1rem !important;
        font-weight: 750 !important;
      }}
      .stButton > button[kind="primary"] {{
        background: var(--nu-primary) !important;
        border: 1px solid rgba(255,255,255,.12) !important;
      }}

      /* Dataframe & code blocks */
      pre, code {{
        background: var(--nu-code) !important;
      }}
      [data-testid="stDataFrame"] {{
        border: 1px solid var(--nu-border) !important;
        border-radius: 14px !important;
        overflow: hidden;
      }}

      /* Sidebar sections */
      .nu-sidebar-label {{
        font-weight: 800;
        letter-spacing: .01em;
        margin-top: .35rem;
      }}
      .nu-help {{
        color: var(--nu-muted);
        font-size: .9rem;
      }}
    </style>
    """
    st.markdown(css, unsafe_allow_html=True)


def _hero() -> None:
    st.markdown(
        f"""
        <div class="nu-card">
          <p class="nu-title">{APP_TITLE}</p>
          <p class="nu-sub">{APP_TAGLINE}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _card(title: str, subtitle: str) -> None:
    st.markdown(
        f"""
        <div class="nu-card">
          <h3>{title}</h3>
          <p>{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------
# App
# ---------------------------

def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🧽", layout="wide")

    if "dark_mode" not in st.session_state:
        st.session_state.dark_mode = False
    if "results" not in st.session_state:
        st.session_state.results = None
    if "csv_bytes" not in st.session_state:
        st.session_state.csv_bytes = None

    # Sidebar
    with st.sidebar:
        st.markdown("### 🧽 Scraper")
        st.session_state.dark_mode = st.toggle("🌙 Dark mode", value=st.session_state.dark_mode)

        _inject_theme_css(st.session_state.dark_mode)

        st.markdown("---")
        st.markdown('<div class="nu-sidebar-label">🔑 API Key</div>', unsafe_allow_html=True)

        default_key = _get_api_key_from_env_or_secrets() or ""
        api_key = st.text_input(
            "YouTube Data API v3 key",
            value=default_key,
            type="password",
            placeholder="Paste your API key…",
            help="Recommended: store it in Streamlit Secrets on Streamlit Cloud.",
        ).strip()

        st.markdown("---")
        st.markdown('<div class="nu-sidebar-label">⚙️ Scrape settings</div>', unsafe_allow_html=True)

        content_type = st.selectbox(
            "Content type",
            options=["shorts", "videos", "both"],
            index=2,
            help="Shorts = <= 60s. Videos = > 60s. Both = all.",
        )

        scan_limit = st.number_input(
            "Scan limit",
            min_value=10,
            max_value=5000,
            value=500,
            step=50,
            help="How many recent uploads to scan before stopping.",
        )

        min_views = st.number_input(
            "Min views",
            min_value=0,
            value=0,
            step=1000,
            help="Filter out videos below this view count.",
        )

        popular_first = st.toggle(
            "Popular-first (faster)",
            value=True,
            help="When ON, we try to fetch popular items first so you hit winners sooner.",
        )

        include_transcripts = st.toggle(
            "Include transcripts",
            value=True,
            help="Transcripts are pulled when publicly available (some videos don't have them).",
        )

        st.markdown('<p class="nu-help">Tip: If you get 0 results, raise <b>Scan limit</b>, set <b>Min views</b> to 0, or turn off <b>Popular-first</b>.</p>', unsafe_allow_html=True)

    # Main content
    _hero()
    st.write("")

    left, right = st.columns([1.35, 1], gap="large")

    with left:
        _card("Channel", "Paste a channel URL (handle, /channel/..., or /@handle).")
        st.write("")
        channel_url = st.text_input(
            "YouTube Channel URL",
            value="https://www.youtube.com/@davisfacts",
            label_visibility="collapsed",
            placeholder="https://www.youtube.com/@channelhandle",
        ).strip()

        st.write("")
        run = st.button("🚀 Run scrape", type="primary", use_container_width=True, disabled=not (api_key and channel_url))

        if run:
            st.session_state.results = None
            st.session_state.csv_bytes = None

            channel_url_norm = normalize_channel_url(channel_url)
            st.info(f"Normalized channel URL: {channel_url_norm}")

            with st.status("Scraping…", expanded=True) as status:
                progress = st.progress(0)
                log_box = st.empty()

                logs: List[str] = []

                def on_progress(p: float, msg: str):
                    p = max(0.0, min(1.0, p))
                    progress.progress(int(p * 100))
                    logs.append(msg)
                    # show last ~15 lines
                    log_box.code("\n".join(logs[-15:]), language="text")

                try:
                    rows, csv_bytes = scrape_channel(
                        api_key=api_key,
                        channel_url=channel_url_norm,
                        content_type=content_type,
                        scan_limit=int(scan_limit),
                        min_views=int(min_views),
                        popular_first=bool(popular_first),
                        include_transcripts=bool(include_transcripts),
                        progress_cb=on_progress,
                    )
                    st.session_state.results = rows
                    st.session_state.csv_bytes = csv_bytes

                    status.update(label=f"Done — {len(rows)} video(s) returned.", state="complete", expanded=False)
                except Exception as e:
                    status.update(label="Scrape failed", state="error", expanded=True)
                    st.exception(e)

    with right:
        _card("Output", "Preview results and download a CSV (one row per video).")
        st.write("")

        if st.session_state.results:
            rows: List[Dict[str, Any]] = st.session_state.results
            st.success(f"✅ {len(rows)} videos ready.")

            # small preview
            preview_cols = ["title", "url", "views", "published_at", "duration_sec", "has_transcript"]
            preview = [{k: r.get(k) for k in preview_cols} for r in rows[:25]]
            st.dataframe(preview, use_container_width=True, height=360)

            st.write("")
            st.download_button(
                "⬇️ Download CSV",
                data=st.session_state.csv_bytes,
                file_name="channel_videos_with_transcripts.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.info("Run a scrape to see results here.")


if __name__ == "__main__":
    main()
