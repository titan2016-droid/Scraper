# YouTube Transcript + Metadata Scraper (Streamlit)

This app scrapes a YouTube channel’s videos, filters by view count (via YouTube Data API v3), and exports a CSV that includes:
- video URL
- title
- views
- publish date
- transcript (when available)
- tags/keywords (when available)

## What changed in this build (fixes your Streamlit Cloud issue)
- ✅ **No pandas** (Streamlit Cloud was defaulting to Python 3.13 and could hang compiling wheels)
- ✅ Added **`runtime.txt`** to force a stable Python version on Streamlit Cloud
- ✅ Added a working **Light/Dark toggle** that changes the *entire* app (main + sidebar)
- ✅ More modern UI + clearer workflow

## Deploy to Streamlit Cloud
Place these files in your GitHub repo (recommended at the repo root):
- `app.py`
- `scraper.py`
- `requirements.txt`
- `runtime.txt`
- `.streamlit/config.toml`

Streamlit entry point: `app.py`

### Secrets (recommended)
In Streamlit Cloud:
- App → **Settings** → **Secrets**

Add:
```toml
YOUTUBE_API_KEY = "YOUR_KEY_HERE"
```

The app also lets you paste a key in the sidebar (not recommended for long-term use).

## Local run
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## If transcripts are empty
Some videos don’t have transcripts (or they’re restricted).
For harder cases, upload a **`cookies.txt`** (exported from your browser while logged into YouTube) so the scraper can access transcripts that require a logged-in session.
