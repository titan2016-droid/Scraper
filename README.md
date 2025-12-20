# v9 — Exports full metadata + transcript

Adds CSV columns similar to YouTube Data API snippet data:
- publishedAt (UTC, best-effort)
- channelId / channelTitle
- title, tags, categories
- defaultLanguage / defaultAudioLanguage (best-effort)
- thumbnail URLs (default/medium/high/standard/maxres)

Still supports Popular-first + early stop + transcript fallback (captions → audio transcription).

## Streamlit Secrets
Add in Streamlit Cloud → Settings → Secrets:
```
OPENAI_API_KEY = "YOUR_KEY"
```

## Notes
`categoryId` is usually only available via the official YouTube Data API; yt-dlp provides `categories` more reliably.


## v10 Hotfix
If Streamlit shows an ImportError on `youtube_transcript_api._errors`, update to v10. This version is compatible with multiple youtube-transcript-api layouts and pins the dependency.


## v11 Fix: 0 results on Streamlit Cloud
If you see `Early stop: consecutive videos below min_views` and 0 results, add a YouTube Data API key in the sidebar. This makes view counts reliable and allows Popular-first + early-stop to work.
