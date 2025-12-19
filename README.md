# YouTube Transcript Scraper (Web App)

A simple **Streamlit** app that scrapes a YouTube channel's videos (Shorts or Longform) and lets you download a CSV containing:
- Title
- URL
- Video ID
- View Count (best effort)
- Transcript (if available)

## Run locally
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Deploy online (Streamlit Community Cloud)
1. Push this folder to a GitHub repo.
2. In Streamlit Community Cloud, create a new app:
   - Main file: `app.py`
3. Deploy.

## Notes
- Some videos have transcripts disabled.
- YouTube may rate-limit aggressive scraping. Keep `Max videos` reasonable.
- Use the channel root URL (e.g., `https://www.youtube.com/@name`) even when scraping Shorts.
