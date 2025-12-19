# YouTube Transcript Scraper (Web App) — Ranked + View Filter

This **Streamlit** app:
- Scans a YouTube channel (Shorts, Longform, or Both)
- Filters to videos with **>= minimum views** (default 300,000)
- Pulls **transcripts** for qualifying videos
- Ranks results by **view_count**
- Lets you download a **ranked CSV**

## Run locally
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy online (Streamlit Community Cloud)
1. Push this folder to a GitHub repo.
2. In Streamlit Community Cloud, create a new app (main file: `app.py`).
3. Deploy.

## Notes / Limits
- View counts require per-video metadata calls. Scanning an entire large channel can take time.
- Start with a smaller "Max videos to scan" (e.g., 300–800), then increase.
- Some videos have transcripts disabled or unavailable.
