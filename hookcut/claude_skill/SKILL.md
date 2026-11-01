---
name: hookcut
description: Turn a YouTube URL or local video into the best 3-5 Instagram Reels with Hookcut, then review every clip like an editor and fix weak openings, endings and hooks before handing them over. Use when the user pastes a YouTube link and asks for reels, shorts, clips, or viral cuts.
when_to_use: 'Trigger phrases: make reels from this, clip this video, cut this podcast into shorts, turn this into Instagram reels.'
argument-hint: "<youtube-url-or-file> [clips 3-5] [style hookcut|beast|karaoke|minimal]"
allowed-tools: Bash(hookcut *) Bash(ffmpeg *) Bash(ffprobe *) Read Edit
---

# Hookcut: best Reels from a URL

Input: $ARGUMENTS
Defaults when not given: 4 clips, style `hookcut`.

Hookcut stores each job in `~/.hookcut/jobs/<job-id>/`. For YouTube links the job id is the 11-character video id; for a local file it is the newest folder in `~/.hookcut/jobs/`. Inside it: `status.json` (stages), `clips.json` (all clips), and one folder per clip, e.g. `c01/`, holding `c01.mp4`, `c01.jpg`, `words.json` (timed words, local time) and `edit.json` (the final cut).

## 1. Preflight
Run `hookcut doctor`. If ffmpeg or Claude is not ok, show the fix line and stop. Instagram not ok is fine.

## 2. Check the source in one line
Say in a sentence whether this looks clip-worthy: talking content such as podcasts, interviews and debates clips best. Music, gameplay without talking and silent b-roll clip badly. Remind once, briefly, that the user should own the video or have permission. Do not lecture.

## 3. Run it
Start this as a background command so it isn't cut off by the shell timeout:
`hookcut run "<url>" --clips <n> --style <style>`
Every ~30 seconds read `~/.hookcut/jobs/<job-id>/status.json` and report the stage in a few words. A run takes about 4-6 minutes; the very first run also downloads the Whisper model. If `state` becomes `error`, show the `error` text, fix what you can, and rerun the same command, since finished stages are kept.

## 4. Review every clip like a picky editor
For each clip in `clips.json` with `state: "ready"`:
1. Read its `score`, `breakdown`, `hook_title`, `why`, `ig_caption`, `hashtags`.
2. Read the clip's `edit.json` and look at the `words` in output order. That is exactly what the viewer hears.
3. Extract three frames and look at them:
   `ffmpeg -y -v error -ss 0.6 -i c01.mp4 -frames:v 1 f_open.jpg`, and the same at the middle and 1 s before the end.
   Check that the speaker is framed, captions are readable and inside the frame, and the hook title isn't covering a face.
4. Judge it against three tests:
   - **Hook:** would the first 2 seconds of words stop a stranger scrolling?
   - **Standalone:** does it make sense with no context from the video?
   - **Payoff:** does it end right after the punchline, answer or twist, not mid-thought?

## 5. Fix what fails, then re-render
Edit `clips.json` for that clip only, then run `hookcut rerender <job-id> <clip-id>`. A re-render takes seconds. Fixes:
- **Slow or mid-thought opening:** add the `_i` index of every word before the first strong sentence to `overrides.removed`. Indices come from `edit.json` words, or from the position in `words.json`.
- **Rambling ending:** add the indices after the payoff to `overrides.removed`, or lower `local_end`.
- **Weak, vague or clickbait hook title:** rewrite `hook_title` in 3-6 specific, true words.
- **Confusing teaser:** set `overrides.cold_open` to `false`.
- **Wrong caption energy:** set `overrides.preset` to `beast` (loud), `karaoke`, or `minimal` (calm).

Never remove words in the middle of a sentence; cut only whole leading or trailing sentences or obvious stumbles. After re-rendering, check the new opening frame once more. Two fix rounds per clip at most.

If a clip still fails the tests after fixes, mark it as a skip and say why. Shipping 3 great clips beats 5 average ones.

## 6. Hand over
Reply with a short table: clip, score, hook title, length, verdict (post or skip), and the full path to each `.mp4`. Under it, give each clip's Instagram caption and hashtags in a code block ready to paste. Suggest posting order: highest score first, one or two a day.

Then offer, without doing it, two next steps: open the studio with `hookcut ui` to tweak visually, or queue posts.

## Posting rules
Never publish or queue anything unless the user explicitly asks in this conversation. If they ask, use `hookcut publish <job-id> <clip-id>` for one clip, add `--trial` when they want trial reels, and confirm each clip before posting it.
