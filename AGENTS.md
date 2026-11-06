# AGENTS.md: Echo

This file is read automatically by Claude Code, Antigravity, Cursor, Codex and other coding agents.

## If the user wants Reels from a video

If Echo is connected as an MCP server, use its tools. Otherwise run `echoclip guide` and follow the playbook it prints. In short:

1. `echoclip doctor`, adding `--fix` if anything is missing.
2. `echoclip prepare "<url>" --clips 4 --style echo --length medium --aspect 9:16 [--topic "..."]`
3. Read BRIEF, judge the moments for Instagram's signals (watch time, sends, likes), and write the JSON to WRITE.
4. `echoclip finish <job> --detach`, then poll `echoclip status <job>`.
5. Direct the camera for every clip: `echoclip shots <job> <clip>`, study the contact sheets, then `echoclip direct <job> <clip> '[...]'` with a layout, focus, zoom and motivated moves per shot. Never fill-and-centre by default.
6. `echoclip clips <job>`, with its `instagram_check` notes, and `echoclip frames <job> <clip>` to review.
7. `echoclip fix <job> <clip> --start-at <i> --end-after <i> --hook "..." --style solo` to fix.
8. `echoclip export <job>`, then hand over a table, the file paths and the captions.

Never publish to Instagram unless the user explicitly asks.

## If the user wants to change Echo itself

- Setup: `pip install -e ".[dev]"`, then `pytest -q`.
- Code map:
  - `echoclip/ingest.py`: yt-dlp for info, heatmap, comments, captions, section downloads.
  - `signals.py`: heat, crowd and pace scores.
  - `select.py` and `prompts/`: scout, judge, fuse, pick.
  - `pipeline.py`: resumable jobs in `~/.echo/jobs`. With `opts.judge = "agent"` it writes `brief.md` and waits for `judgments.json`.
  - `edit.py`: cut plan. `reframe.py`: layouts. `captions.py`: ASS captions. `render.py`: one ffmpeg graph per clip.
  - `api.py`: functions shared by the CLI, `mcp_server.py` (MCP for every agent) and the studio. `connect.py`: agent hookup. `wizard.py`: `echoclip start`.
  - `cli.py`: every command. `agent_guide.md`: the playbook printed by `echoclip guide`. `claude_skill/SKILL.md`: the `/echo` skill.
  - `server.py` and `web/`: local studio, no build step. `docs/`: GitHub Pages site and README artwork.
- New tunables go in `config.DEFAULTS`, never hardcoded.
- Keep agent-facing commands plain text or JSON, and keep the playbook in `agent_guide.md` the single source of truth.
- Brand:
  - Colors: three-color system from a glowing fiber palette. Void black `#05070D` (surfaces `#0A1022`, navy depth `#08174A`), electric blue `#1E7BFF` (core `#1673D6`, glow `#2F8BFF`), and ice blue `#48B4F0` for highlights. Text is `#EAF4FF` and soft text is `#8FA6C8`. The signature motif is the radiating fiber burst, with soft glows and subtle grain.
  - Fonts: Instrument Serif for display, Geist for UI, both self-hosted.
  - Style: minimal, generous space.
  - Motion: `cubic-bezier(.22,1,.36,1)`, always respecting reduced motion.
- Run `pytest -q` before committing.
