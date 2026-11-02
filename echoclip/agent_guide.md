# Echo playbook for AI agents

You are making the best 3-5 Instagram Reels from a long video. Echo handles downloading, transcription and editing. You judge which moments become Reels, then review and fix every clip like a picky editor.

If Echo is connected as an MCP server, use its tools: guide, prepare, read_brief, submit_judgments, finish, status, clips, frames, fix, cover, export. Otherwise, run the `echoclip` commands below in a terminal. Every command prints plain text or JSON.

## What wins on Instagram, so judge for it

- **Watch time** is the strongest signal everywhere. The first second must hook; no slow intros.
- **Sends per reach** (people DMing the Reel to a friend) is the strongest signal for reaching non-followers. Pick moments someone would send to a specific person.
- **Likes per reach** counts more with existing followers.
- **Original, raw content** is favored, so keep the real voice and remove nothing that makes it human.

## Inputs to ask for, if the user didn't say

Ask only for what's missing:
- the link or file
- how many Reels (default 4)
- caption style (default `echo`): `echo` is clean, `beast` loud, `solo` one huge word, `neon` glowing, `karaoke`, `minimal` calm
- length (default `medium`): `short` is 15-30 s, `medium` 25-55 s, `long` 45-90 s
- an optional topic
- aspect (default 9:16): 9:16 for Reels, 4:5 for feed, 1:1

## 1. Preflight

Run `echoclip doctor`. If ffmpeg, yt-dlp or faster-whisper shows "fix", run `echoclip doctor --fix`, then check again. Ignore the Claude CLI and Instagram lines.

In one sentence, say whether the video will clip well: podcasts, interviews and debates clip best. Remind the user once that they should own the video or have permission.

## 2. Prepare (under a minute)

Run `echoclip prepare "<url-or-path>" --clips 4 --style echo --length medium --aspect 9:16 [--topic "..."]`.

It prints JOB, BRIEF and WRITE.

## 3. Judge

Read the whole BRIEF and follow it exactly. Favor lines tagged REPLAYED or COMMENTED, but only when they stand alone. Save the JSON array to the WRITE path, or call submit_judgments.

## 4. Cut and render (2-5 minutes)

Run `echoclip finish <job> --detach`, then run `echoclip status <job>` every 20-30 seconds. Give the user a short progress note each time. On error, show the message, fix what you can and run finish again; finished work is kept.

## 5. Review every clip

Run `echoclip clips <job>`. For each clip with state `ready`:
- Read its `words`: exactly what the viewer hears, each word with an index `i`.
- Read its `instagram_check` notes.
- Run `echoclip frames <job> <clip>` and look at the images, if you can view images. Check that the speaker is framed, the captions are readable, and nothing important hides behind Instagram's buttons on the right or the caption area at the bottom.

Test each clip:
- **Hook:** do the first 2 seconds stop a stranger scrolling?
- **Standalone:** does it make sense with no context?
- **Payoff:** does it end right after the punchline, not mid-thought?
- **Send test:** would someone DM this to a friend?

## 6. Fix with one command (seconds)

`echoclip fix <job> <clip> [options]`:
- `--start-at <i>`: drop a slow opening.
- `--end-after <i>`: drop a rambling ending.
- `--cut "<i>-<j>,<k>"`: remove stumbles. Never cut mid-sentence.
- `--hook "<3-6 specific, true words>"`: rewrite the on-screen title.
- `--no-cold-open` or `--cold-open`: turn the teaser off or on.
- `--style echo|beast|solo|neon|karaoke|minimal`: change the captions.
- `--reset`: undo all word cuts.

Do at most two rounds per clip. If a clip still fails, call it a skip and say why. Three great clips beat five average ones.

## 7. Hand over

Run `echoclip export <job>`. It copies every finished clip, a grid-safe cover image and a ready-to-paste caption file into Videos/Echo.

Show a short table with each clip's score, hook title, length, a post or skip verdict and the file path. Below it, put each caption and its hashtags in a code block.

Posting advice:
- Post the highest score first.
- Post one or two Reels a day at fixed times. `echoclip besttime` finds the hours, if Instagram is connected.
- Try trial reels for risky clips.

## Posting

Never publish anything unless the user explicitly asks. If they do, run `echoclip publish <job> <clip>`, adding `--trial` for trial reels, and confirm each clip first.
