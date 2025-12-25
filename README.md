# YouTube Transcript + Metadata Scraper (Streamlit)

Paste a YouTube channel URL, scrape recent videos, and export a CSV with:
- title, url, video_id
- views, published_at, duration
- transcript (when available)

## Run locally
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## API key
Set one of these:
- **Streamlit Secrets**: `YOUTUBE_API_KEY`
- **Environment**: `YOUTUBE_API_KEY` (or `YT_API_KEY`)
- Or paste it into the sidebar at runtime.

## Deploy (Streamlit Community Cloud)
- Put code in GitHub
- Deploy on Streamlit
- Add `YOUTUBE_API_KEY` in **App settings → Secrets**
