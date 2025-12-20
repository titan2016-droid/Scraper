# v8 — Popular-first + fixes “0 results” URL issues

If you paste a URL like `https://www.youtube.com/@name/shorts`, older builds could accidentally append `/shorts` again,
creating `/shorts/shorts?...` and returning 0 entries.

This build normalizes the channel URL to the base and prints the exact listing URLs it’s scraping for quick debugging.

Note: Popular sort URL params like `sort=p` are widely used on channel tabs, but Shorts extraction can still be flaky in yt-dlp when YouTube changes internals. citeturn0search11turn0search5


## v13
Adds optional YouTube Data API key to fetch reliable view counts + metadata and sort shorts by views (no reliance on the 'Popular' tab working). This prevents 0-result early-stop issues on Streamlit Cloud.


## v14
Fixes Streamlit ImportError for `youtube_transcript_api._errors` by supporting multiple import layouts and pinning `youtube-transcript-api==0.6.2`.
