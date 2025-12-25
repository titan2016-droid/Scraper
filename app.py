import io
import csv
from datetime import datetime
from typing import List, Dict, Any, Optional

import streamlit as st

from scraper import scrape_channel, normalize_channel_url


APP_TITLE = "YouTube Transcript + Metadata Scraper"


def _inject_theme_css(dark: bool) -> None:
    """Force a full-page light/dark theme (including sidebar) via CSS.

    Streamlit has multiple nested containers that keep their own background colors.
    The previous version only changed the <body> and a couple wrappers, so parts of
    the UI stayed light even when toggled. This version aggressively targets the
    major containers and common BaseWeb components.
    """

    if dark:
        bg = "#0b1020"
        surface = "#0f172a"
        surface2 = "#0c142b"
        text = "#e5e7eb"
        muted = "#9ca3af"
        border = "#22304a"
        accent = "#60a5fa"
        accent_soft = "rgba(96, 165, 250, 0.15)"
        code_bg = "#0b1224"
    else:
        bg = "#f6f7fb"
        surface = "#ffffff"
        surface2 = "#f9fafb"
        text = "#0f172a"
        muted = "#475569"
        border = "#e5e7eb"
        accent = "#2563eb"
        accent_soft = "rgba(37, 99, 235, 0.10)"
        code_bg = "#f1f5f9"

    st.markdown(
        f"""
<style>
:root {{
  --app-bg: {bg};
  --surface: {surface};
  --surface2: {surface2};
  --text: {text};
  --muted: {muted};
  --border: {border};
  --accent: {accent};
  --accent-soft: {accent_soft};
  --code-bg: {code_bg};
}}

/* ===== App background (hit ALL wrappers) ===== */
html, body {{ background: var(--app-bg) !important; color: var(--text) !important; }}
.stApp {{ background: var(--app-bg) !important; color: var(--text) !important; }}
div[data-testid="stAppViewContainer"] {{ background: var(--app-bg) !important; }}
div[data-testid="stAppViewContainer"] > .main {{ background: var(--app-bg) !important; }}
div[data-testid="stHeader"] {{ background: transparent !important; }}

/* ===== Sidebar ===== */
section[data-testid="stSidebar"] {{
  background: var(--surface) !important;
  border-right: 1px solid var(--border) !important;
}}
section[data-testid="stSidebar"] > div {{ background: var(--surface) !important; }}

/* ===== Layout ===== */
.block-container {{ padding-top: 1.75rem; padding-bottom: 2.75rem; max-width: 1200px; }}

/* ===== Cards ===== */
.nu-card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 18px 18px;
  box-shadow: 0 10px 35px rgba(0,0,0,0.08);
}}
.nu-step {{
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: var(--surface);
  margin-right: 8px;
}}
.nu-badge {{
  width: 26px;
  height: 26px;
  border-radius: 999px;
  background: var(--accent-soft);
  color: var(--accent);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}}
.nu-muted {{ color: var(--muted) !important; }}

/* ===== Typography tweaks ===== */
h1, h2, h3, h4, h5, h6, p, span, label {{ color: var(--text) !important; }}

/* ===== Inputs (BaseWeb + Streamlit) ===== */
div[data-testid="stTextInput"] input,
div[data-testid="stNumberInput"] input,
div[data-testid="stTextArea"] textarea {{
  background: var(--surface2) !important;
  border: 1px solid var(--border) !important;
  color: var(--text) !important;
  border-radius: 12px !important;
}}

/* Selectbox: target BaseWeb select control */
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
  background: var(--surface2) !important;
  border: 1px solid var(--border) !important;
  border-radius: 12px !important;
  color: var(--text) !important;
}}

/* Toggle/checkbox backgrounds */
div[data-baseweb="checkbox"] > div {{
  border-color: var(--border) !important;
}}

/* ===== Buttons ===== */
.stButton > button {{
  border-radius: 999px !important;
  border: 1px solid var(--border) !important;
  padding: 0.65rem 1.0rem !important;
  font-weight: 650 !important;
}}
.stButton > button[kind="primary"], .stButton > button[data-testid="baseButton-primary"] {{
  background: var(--accent) !important;
  color: white !important;
  border-color: transparent !important;
}}

/* ===== Dataframe ===== */
div[data-testid="stDataFrame"] {{
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: 14px !important;
  overflow: hidden;
}}

/* ===== Code blocks ===== */
code, pre {{ background: var(--code-bg) !important; }}

/* ===== Links ===== */
a {{ color: var(--accent) !important; }}

/* ===== Small polish ===== */
hr {{ border-color: var(--border) !important; }}
</style>
        """,
        unsafe_allow_html=True,
    )


def _rows_to_csv_bytes(rows: List[Dict[str, Any]]) -> bytes:
    if not rows:
        return b""
    # Stable column order
    cols = list(rows[0].keys())
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})
    return buf.getvalue().encode("utf-8")


def _get_api_key_from_ui_or_secrets() -> Optional[str]:
    # Try Streamlit secrets first
    key = None
    try:
        key = st.secrets.get("YOUTUBE_API_KEY")
    except Exception:
        key = None

    ui_key = st.session_state.get("api_key_input")
    return (ui_key or key or "").strip() or None


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🎬", layout="wide")

    # -------- Sidebar --------
    with st.sidebar:
        st.markdown(f"## 🎬 {APP_TITLE}")
        st.markdown('<div class="nu-muted">Scrape channel videos, pull metadata + transcripts, export a CSV.</div>', unsafe_allow_html=True)
        st.divider()

        # Persist theme choice + force a clean rerun when toggled so the CSS
        # re-applies consistently across Streamlit Cloud.
        current_dark = bool(st.session_state.get("dark_mode", False))
        new_dark = st.toggle("🌙 Dark mode", key="dark_mode", value=current_dark)
        if "_last_dark_mode" not in st.session_state:
            st.session_state["_last_dark_mode"] = new_dark
        elif st.session_state["_last_dark_mode"] != new_dark:
            st.session_state["_last_dark_mode"] = new_dark
            st.rerun()

        _inject_theme_css(dark=bool(st.session_state.get("dark_mode")))

        st.markdown("### 🔐 API Key")
        st.text_input(
            "YouTube Data API v3 key",
            key="api_key_input",
            type="password",
            placeholder="Paste your API key…",
            help="Tip: On Streamlit Cloud you can store this in Secrets as YOUTUBE_API_KEY.",
        )

        st.markdown("### ⚙️ Scrape settings")
        content_type = st.selectbox(
            "Content type",
            ["shorts", "videos", "both"],
            index=2,
            help="Shorts = short-form content. Videos = regular uploads. Both = scan both tabs.",
        )
        scan_limit = st.number_input(
            "Scan limit",
            min_value=25,
            max_value=5000,
            value=500,
            step=25,
            help="Max items to scan before filtering. Higher = slower but more complete.",
        )
        min_views = st.number_input(
            "Min views",
            min_value=0,
            max_value=10_000_000_000,
            value=0,
            step=1000,
            help="Filter out low-view videos.",
        )
        popular_first = st.toggle(
            "Popular-first (faster)",
            value=True,
            help="Starts with the most popular items first. Good for finding winners fast.",
        )
        include_transcripts = st.toggle(
            "Include transcripts",
            value=True,
            help="Uses YouTube Transcript API when available. Some videos will not have transcripts.",
        )
        st.caption("If transcripts are missing: many videos simply don't publish them, or they’re disabled.")

    # -------- Header --------
    st.markdown(
        """
<div class="nu-card">
  <div style="display:flex; align-items:center; justify-content:space-between; gap:16px;">
    <div>
      <div style="font-size:28px; font-weight:800; line-height:1.1;">Scrape a channel → Export CSV</div>
      <div class="nu-muted" style="margin-top:6px;">Paste a channel URL, run the scrape, download results. Works great as input to your AI analyzer.</div>
    </div>
    <div style="font-size:34px;">📄</div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )

    st.write("")

    # -------- Main form --------
    left, right = st.columns([1.2, 1])

    with left:
        st.markdown("### Channel")
        channel_url = st.text_input(
            "YouTube Channel URL",
            value="https://www.youtube.com/@davisfacts",
            help="Examples: https://www.youtube.com/@handle  |  https://www.youtube.com/channel/UC...",
        )

    with right:
        st.markdown("### Output")
        st.markdown(
            "<div class='nu-muted'>You'll get one row per video with title, URL, views, publish date, and transcript (when available).</div>",
            unsafe_allow_html=True,
        )

    st.write("")

    api_key = _get_api_key_from_ui_or_secrets()

    if not api_key:
        st.warning("Add your YouTube Data API key in the sidebar to run the scraper.")
        st.stop()

    run = st.button("🚀 Run scrape", use_container_width=True)

    if run:
        norm = normalize_channel_url(channel_url)
        if not norm:
            st.error("That doesn't look like a valid YouTube channel URL.")
            st.stop()

        with st.spinner("Scraping channel… this can take a bit on large channels."):
            try:
                rows, debug = scrape_channel(
                    channel_url=norm,
                    api_key=api_key,
                    content_type=content_type,
                    scan_limit=int(scan_limit),
                    min_views=int(min_views),
                    popular_first=bool(popular_first),
                    include_transcripts=bool(include_transcripts),
                )
            except Exception as e:
                st.error("Scrape failed.")
                st.exception(e)
                st.stop()

        st.success(f"Done. Returned {len(rows)} video(s).")

        # Show debug
        with st.expander("Debug (advanced)"):
            st.code("\n".join(debug) if debug else "(no debug)")

        if not rows:
            st.warning(
                "0 qualifying videos returned. Try increasing scan_limit, switching content type to 'both', "
                "or setting Min views to 0. If you used Popular-first, try turning it off for deeper scans."
            )
            st.stop()

        st.markdown("### Results")
        st.dataframe(rows, use_container_width=True, hide_index=True)

        csv_bytes = _rows_to_csv_bytes(rows)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        st.download_button(
            "⬇️ Download CSV",
            data=csv_bytes,
            file_name=f"channel_videos_with_transcripts_{ts}.csv",
            mime="text/csv",
            use_container_width=True,
        )


if __name__ == "__main__":
    main()
