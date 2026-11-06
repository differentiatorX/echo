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

Run `echoclip doctor`. If anything except Claude CLI or Instagram shows "fix", run `echoclip doctor --fix`, then check again. Don't patch Echo's environment by hand: Echo already falls back to the CPU if the GPU libraries are missing, and gives ffmpeg a clean PATH.

In one sentence, say whether the video will clip well: podcasts, interviews and debates clip best. Remind the user once that they should own the video or have permission.

## 2. Prepare (under a minute)

Run `echoclip prepare "<url-or-path>" --clips 4 --style echo --length medium --aspect 9:16 [--topic "..."]`.

It prints JOB, BRIEF and WRITE.

## 3. Judge

Read the whole BRIEF and follow it exactly. Favor lines tagged REPLAYED or COMMENTED, but only when they stand alone. Save the JSON array to the WRITE path, or call submit_judgments.

## 4. Cut and render (2-5 minutes)

Run `echoclip finish <job> --detach`, then run `echoclip status <job>` every 20-30 seconds. Give the user a short progress note each time. On error, show the message, fix what you can and run finish again; finished work is kept.

Never start a second `finish` while one is running; Echo refuses it anyway, so just keep polling. If a step fails, read the error Echo prints (renders save a `render.log` in the clip folder) before changing anything on the machine.

## 5. Direct the camera, every clip, every shot (required)

Echo's automatic framing is only a first draft. Never accept "fill the screen, subject in the centre" by default. Study each shot and decide what the scene needs, like a real editor would.

For each clip:
1. Run `echoclip shots <job> <clip>` and open every contact sheet image it lists. Each row is one shot: three frames from its start, middle and end, position guides (x 0.25 / 0.50 / 0.75, y 0.33 / 0.66), the automatic crop in gold, and what is said.
2. For every shot, decide:
   - **Layout**
     - `fill`: one person or one clear subject. Set `focus` to their face (x, y in 0..1, read off the guides). Use `follow: true` only if they walk or move a lot.
     - `wide`: the whole scene matters. Use it for a group, a demo, a sports play, a stunt, food on a counter, text or graphics on screen, or two people interacting across the frame. Never crop the action out to fill the screen.
     - `split`: two people talking side by side, each worth seeing.
   - **Zoom**
     - `fill`: 1.0 is a standard vertical crop. 1.15 to 1.3 is a closer, more intimate shot of a face.
     - `wide`: 1.0 shows the whole frame. Use 1.2 to 1.5 to cut away empty edges and get closer to the action.
   - **Moves.** Only when the content motivates them; most shots need none.
     - `push` (to 1.15 to 1.35, slowly over 2 to 6 s) builds tension into a reveal, a confession or a punchline.
     - `punch` (to 1.3 to 1.6, hold 0.6 to 1.5 s) lands a reaction, a shocked face or a key word, at the exact second it happens. Read the timing from the shot's words.
     - At most one or two moves per shot.
     - Moves zoom toward the shot's `focus`, so put the focus on whatever the move should land on: the face reacting, the ball, the plate.
3. Send all shots at once: `echoclip direct <job> <clip> '[{"shot":0,"layout":"fill","focus":[0.42,0.38],"zoom":1.1,"why":"..."},{"shot":1,"layout":"wide","zoom":1.2,"focus":[0.5,0.5],"why":"..."},{"shot":2,"layout":"fill","focus":[0.61,0.35],"moves":[{"type":"punch","to":1.45,"at":14.2,"hold":1.0}],"why":"..."}]'`
   It re-renders in seconds. Times are the clip's source seconds, shown in `shots`.

Rules:
- Every zoom and every layout choice needs a reason you can state in `why`: who is speaking, what is happening, what the viewer must see. No zoom just to fill the screen, no zoom on a timer, no zoom on empty space.
- Never cut off a face, a hand doing the action, or the object the scene is about. If in doubt, go wider.
- Keep one layout for a run of similar shots, so the clip doesn't feel jumpy.
- After directing, check `echoclip frames <job> <clip>` again. Fix any shot that still frames the wrong thing.

## 6. Review every clip

Run `echoclip clips <job>`. For each clip with state `ready`:
- Read its `words`: exactly what the viewer hears, each word with an index `i`.
- Read its `instagram_check` notes.
- Run `echoclip frames <job> <clip>` and look at the images, if you can view images. Check that the speaker is framed, the captions are readable, and nothing important hides behind Instagram's buttons on the right or the caption area at the bottom.

Watch for two common problems:
- **Missing action:** a visual moment (a swing, a kick, a reveal) has no words, so check that the clip still contains it. If it doesn't, use `--no-trim`.
- **Misheard names:** brands and people's names often come out wrong. Fix them with `--replace`.

Test each clip:
- **Hook:** do the first 2 seconds stop a stranger scrolling?
- **Standalone:** does it make sense with no context?
- **Payoff:** does it end right after the punchline, not mid-thought?
- **Send test:** would someone DM this to a friend?

## 7. Fix with one command (seconds)

`echoclip fix <job> <clip> [options]`:
- `--start-at <i>`: drop a slow opening.
- `--end-after <i>`: drop a rambling ending.
- `--cut "<i>-<j>,<k>"`: remove stumbles. Never cut mid-sentence.
- `--hook "<3-6 specific, true words>"`: rewrite the on-screen title.
- `--no-cold-open` or `--cold-open`: turn the teaser off or on.
- `--style echo|beast|solo|neon|karaoke|minimal`: change the captions.
- `--no-trim`: keep every second. Use it for sports, stunts and reactions, where the moment is visual and silence trimming would cut it.
- `--replace "Holland=Haaland"`: fix misheard names in the captions.
- `--reset`: undo all word cuts.

Do at most two rounds per clip. If a clip still fails, call it a skip and say why. Three great clips beat five average ones.

If the framing misses the subject in a clip, run `echoclip rerender <job> <clip> --reframe`.

## 8. Hand over

Run `echoclip export <job>`. It copies every finished clip, a grid-safe cover image and a ready-to-paste caption file into Videos/Echo.

Show a short table with each clip's score, hook title, length, a post or skip verdict and the file path. Below it, put each caption and its hashtags in a code block.

Posting advice:
- Post the highest score first.
- Post one or two Reels a day at fixed times. `echoclip besttime` finds the hours, if Instagram is connected.
- Try trial reels for risky clips.

## Posting

Never publish anything unless the user explicitly asks. If they do, run `echoclip publish <job> <clip>`, adding `--trial` for trial reels, and confirm each clip first.
