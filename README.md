# YouTube Transcript + Metadata Scraper (Streamlit)

## What it does
- Pulls channel videos and ranks them by views (best with a YouTube Data API key)
- Filters by minimum views (ex: 300k+)
- Attempts to fetch transcripts for each video and exports everything to CSV

## Why transcripts sometimes fail online
YouTube often blocks transcript/caption endpoints for server traffic (Streamlit Cloud IPs).
Uploading a `cookies.txt` file (exported from your browser where transcripts are visible) makes transcript fetching far more reliable.

## Deploy
Put these files into your repo folder: `/scraper/`
- app.py
- scraper.py
- requirements.txt

Then set Streamlit entry point to: `scraper/app.py`
