# YouTube Transcript + Metadata Scraper (Streamlit)

Scrape a YouTube channel's uploads, pull video metadata + transcripts (when available), and export a CSV.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## YouTube API key

Create a **YouTube Data API v3** key in Google Cloud Console and paste it into the sidebar.

### Streamlit Cloud (recommended)
Add a secret named `YT_API_KEY`:

- Streamlit app → **Settings** → **Secrets**
- Add:

```toml
YT_API_KEY="YOUR_KEY_HERE"
```

## Notes
- Some channels disable transcripts/captions. Those videos will show `transcript_error`.
- `Scan limit` controls how many uploads are scanned.
- If you hit quota limits, lower scan_limit and/or turn off transcripts.
