# Hookcut — notes for Claude Code

Local AI clipper: YouTube URL or file → 3–5 ranked vertical Reels → optional Instagram posting.

## Run
- `pip install -e ".[dev]"` then `pytest -q`
- `hookcut doctor`, `hookcut ui` (studio at :7777), `hookcut run <url> --clips 4`
- Jobs live in `~/.hookcut/jobs/<id>/` (override with HOOKCUT_HOME). Every stage is idempotent; delete a file to redo that stage.

## Map
- ingest.py: yt-dlp info (heatmap, chapters), comments, json3 captions, section downloads
- signals.py: heat / crowd / pace scores (0–100)
- select.py + prompts/: scout → judge → fuse (weights in settings) → pick
- edit.py: word timings → pieces (silence/filler cuts, cold open, punch zoom) + remapped words
- reframe.py: shots + faces → crop | split | blur | fit
- captions.py: ASS presets; render.py: one ffmpeg filtergraph per clip (cwd = clip dir, fonts copied there for Windows-safe paths)
- pipeline.py: Job state in status.json / clips.json; server.py: FastAPI + single worker thread; web/: vanilla JS, no build
- publish.py: Graph API (facebook = resumable local upload, instagram = public video_url), queue, insights → feedback/winners.md

## Rules
- Keep the brand: abyss #050817, night #0B1433, rim #1C2B63, electric #2F6BFF, volt #4FD1FF, champagne #E9CF9B (sparingly), platinum text #EDF1FF. Fonts: Instrument Serif (display) + Geist (UI), self-hosted woff2. Motion uses cubic-bezier(.22,1,.36,1) and always respects prefers-reduced-motion.
- New tunables go in config.DEFAULTS, not hardcoded.
- Never auto-publish; posting only via explicit button or `post-next`.
- Test edits on a short sample before long videos; run pytest before committing.
