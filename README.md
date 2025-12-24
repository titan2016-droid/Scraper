# YouTube Transcript + Metadata Scraper (Streamlit)

This app exports a CSV of channel videos (metadata + transcripts).

## Setup (Local)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export YOUTUBE_API_KEY="YOUR_KEY"
streamlit run app.py
```

## Streamlit Cloud
1) Deploy this repo
2) App → Settings → Secrets
```toml
YOUTUBE_API_KEY = "YOUR_KEY"
```

## If transcripts are blank
- Some videos legitimately have transcripts disabled/unavailable.
- If MANY are blank, YouTube may be blocking caption access from your server/IP.
  - Upload a `cookies.txt` in the sidebar (exported from your browser).
