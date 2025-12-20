# YouTube Channel Transcript Scraper (Streamlit) — v15

## Important
This version **requires** a YouTube Data API v3 key to pull view counts + metadata + sort by views.

## Output columns
Includes transcript + transcript_error so you can see why a transcript is missing for a specific video.


## v16
Adds transcript fallback that parses YouTube watch HTML captionTracks and downloads VTT (closer to 'Show transcript' behavior).


## Cookies (recommended for transcripts)

If transcripts come back empty or disabled, upload a `cookies.txt` exported from your browser. This lets the app fetch captions the same way your browser does.
