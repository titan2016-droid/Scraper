# YouTube Transcript + Metadata Scraper (Streamlit) — v18 (Fast)

### Why your last run took ~30 minutes and returned nothing
That happens when the app has to crawl a huge amount of videos without a reliable view-count source.
This version **requires** the YouTube Data API key and filters by views FIRST, then fetches transcripts only for the qualifying videos.

### Deploy
Put these files in your repo folder: `/scraper/`
- `app.py`
- `scraper.py`
- `requirements.txt`

Streamlit entry point: `scraper/app.py`

### Transcripts still empty?
Upload `cookies.txt` exported from your browser while logged into YouTube.
