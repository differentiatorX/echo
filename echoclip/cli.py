"""echoclip command line."""
from __future__ import annotations

import time
import webbrowser

import typer

from .config import load_settings

app = typer.Typer(add_completion=False, help="Long video in, the few Reels that echo.")


@app.command()
def ui(port: int = typer.Option(None), open_browser: bool = typer.Option(True, "--open/--no-open")):
    """Start the studio at http://127.0.0.1:7777"""
    import uvicorn
    cfg = load_settings()["server"]
    port = port or cfg["port"]
    url = f"http://{cfg['host']}:{port}"
    typer.echo(f"Echo studio → {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run("echoclip.server:app", host=cfg["host"], port=port, log_level="warning")


@app.command()
def run(source: str, count: int = typer.Option(None, "--clips", "-n"),
        preset: str = typer.Option(None, "--style"), no_cold_open: bool = typer.Option(False)):
    """Cut a YouTube URL or a local video file, headless."""
    from .pipeline import STAGES, Job, run_job
    opts = {k: v for k, v in {"count": count, "preset": preset}.items() if v}
    if no_cold_open:
        opts["cold_open"] = False
    job = Job.create(source, opts)
    t0 = time.perf_counter()
    run_job(job)
    s = job.status()
    for key, label in STAGES:
        st = s.get("stages", {}).get(key, {})
        typer.echo(f"  {label:<18} {st.get('status', '-'):<8} {st.get('secs') or '':>6}s")
    if s["state"] == "error":
        typer.secho(f"Failed: {s.get('error')}", fg="red")
        raise typer.Exit(1)
    for c in job.clips():
        typer.echo(f"  {c['id']}  {c.get('score', 0):5.1f}  {c.get('state'):<8} {c.get('hook_title')}")
    typer.echo(f"Done in {time.perf_counter() - t0:.0f}s → {job.dir}")


@app.command()
def prepare(source: str, count: int = typer.Option(4, "--clips", "-n"),
            preset: str = typer.Option("echo", "--style", help="echo | beast | solo | neon | karaoke | minimal"),
            topic: str = typer.Option("", "--topic", help="Only clip moments about this"),
            length: str = typer.Option("medium", "--length", help="short | medium | long"),
            aspect: str = typer.Option("9:16", "--aspect", help="9:16 | 4:5 | 1:1"),
            language: str = typer.Option("", "--lang", help="auto, or a code like en, hi, es")):
    """Step 1: read the video and write a judging brief for your AI agent."""
    from . import api
    from .util import EchoError
    try:
        res = api.prepare(source, count, preset, topic, length, aspect, language)
    except EchoError as e:
        typer.secho(f"Failed: {e}", fg="red")
        raise typer.Exit(1)
    typer.echo(f"JOB {res['job']}")
    typer.echo(f"BRIEF {res['brief']}")
    typer.echo(f"WRITE {res['write_judgments_to']}")
    typer.echo(f"THEN echoclip finish {res['job']} --detach")


@app.command()
def finish(job_id: str, detach: bool = typer.Option(False, "--detach", help="Run in the background and return now")):
    """Step 2: rank the judgments, then cut and render the clips."""
    from .pipeline import Job, run_job
    job = Job(job_id)
    if detach and job.status():
        import os
        import subprocess
        import sys
        kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
        log = open(job.dir / "finish.log", "w", encoding="utf-8")
        subprocess.Popen([sys.executable, "-m", "echoclip.cli", "finish", job_id], stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        job.update(state="running")
        typer.echo(f"Started in the background. Check progress with: echoclip status {job_id}")
        return
    if not job.status():
        typer.secho(f"No job called {job_id}", fg="red")
        raise typer.Exit(1)
    if not (job.dir / "judgments.json").exists() and not job.clips():
        typer.secho(f"Write {job.dir / 'judgments.json'} first (see brief.md).", fg="red")
        raise typer.Exit(1)
    run_job(job)
    s = job.status()
    if s["state"] == "error":
        typer.secho(f"Failed: {s.get('error')}", fg="red")
        raise typer.Exit(1)
    for c in job.clips():
        typer.echo(f"  {c['id']}  {c.get('score', 0):5.1f}  {c.get('state'):<8} {c.get('hook_title')}  "
                   f"{job.dir / c['id'] / (c['id'] + '.mp4')}")
    typer.echo(f"Done → {job.dir}")


def _job_or_exit(job_id: str):
    from .pipeline import Job
    job = Job(job_id)
    if not job.status():
        typer.secho(f"No job called {job_id}. Jobs live in ~/.echo/jobs", fg="red")
        raise typer.Exit(1)
    return job


@app.command()
def guide():
    """Print the step-by-step playbook for AI agents (Claude Code, Antigravity, Cursor, Codex...)."""
    from pathlib import Path
    typer.echo((Path(__file__).parent / "agent_guide.md").read_text("utf-8"))


@app.command()
def status(job_id: str):
    """Job progress as JSON."""
    import json
    job = _job_or_exit(job_id)
    s = job.status()
    out = {k: s.get(k) for k in ("id", "title", "state", "stage", "error", "stages")}
    out["clips"] = [{"id": c["id"], "state": c.get("state"), "score": c.get("score"), "error": c.get("error")}
                    for c in job.clips()]
    typer.echo(json.dumps(out, indent=2, ensure_ascii=False))


@app.command()
def clips(job_id: str):
    """Every clip as JSON: score, hook, caption, file paths, Instagram check and the exact words."""
    import json
    from . import api
    typer.echo(json.dumps(api.clips(job_id), indent=2, ensure_ascii=False))


@app.command()
def frames(job_id: str, clip: str):
    """Save opening, middle and ending frames of a clip and print their paths."""
    from .util import probe, run
    job = _job_or_exit(job_id)
    video = job.dir / clip / f"{clip}.mp4"
    if not video.exists():
        typer.secho("Clip isn't rendered yet.", fg="red")
        raise typer.Exit(1)
    dur = probe(video)["dur"]
    for name, t in (("open", 0.6), ("middle", dur / 2), ("end", max(0.0, dur - 1.0))):
        out = job.dir / clip / f"frame_{name}.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", "scale=540:-2", str(out)], timeout=60)
        typer.echo(str(out))


@app.command()
def fix(job_id: str, clip: str,
        start_at: int = typer.Option(None, "--start-at", help="Start the clip at word index i"),
        end_after: int = typer.Option(None, "--end-after", help="End the clip after word index i"),
        cut: str = typer.Option(None, "--cut", help='Remove word indices, e.g. "12-15,40"'),
        hook: str = typer.Option(None, "--hook", help="New on-screen hook title"),
        cold_open: bool = typer.Option(None, "--cold-open/--no-cold-open"),
        style: str = typer.Option(None, "--style", help="echo | beast | karaoke | minimal"),
        reset: bool = typer.Option(False, "--reset", help="Undo all word cuts")):
    """Apply editor fixes to one clip and re-render it (seconds)."""
    from .pipeline import render_clip
    from .util import read_json
    job = _job_or_exit(job_id)
    c = job.clip(clip)
    ov = dict(c.get("overrides") or {})
    removed = set() if reset else set(ov.get("removed") or [])
    words = read_json(job.dir / clip / "words.json", []) or []
    in_clip = [i for i, w in enumerate(words)
               if c.get("local_start", 0) - 0.05 <= (w["s"] + w["e"]) / 2 <= c.get("local_end", 0) + 0.05]
    if start_at is not None:
        removed |= {i for i in in_clip if i < start_at}
    if end_after is not None:
        removed |= {i for i in in_clip if i > end_after}
    if cut:
        for part in cut.replace(" ", "").split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                removed |= set(range(int(a), int(b) + 1))
            elif part:
                removed.add(int(part))
    ov["removed"] = sorted(removed)
    if cold_open is not None:
        ov["cold_open"] = cold_open
    if style:
        ov["preset"] = None if style in ("echo", "hookcut") else style
    patch = {"overrides": ov}
    if hook:
        patch["hook_title"] = hook
    job.patch_clip(clip, **patch)
    try:
        res = render_clip(job, clip)
    except Exception as e:
        job.patch_clip(clip, state="error", error=str(e)[:400])
        typer.secho(f"Re-render failed: {e}", fg="red")
        raise typer.Exit(1)
    typer.echo(f"Re-rendered {clip}: {res.get('duration')}s → {job.dir / clip / (clip + '.mp4')}")


@app.command()
def rerender(job_id: str, clip: str):
    """Re-render one clip after edits."""
    from .pipeline import Job, render_clip
    render_clip(Job(job_id), clip)
    typer.echo("Rendered.")


@app.command()
def publish(job_id: str, clip: str, trial: bool = typer.Option(None, "--trial/--no-trial")):
    """Post one clip to Instagram now."""
    from .publish import publish_clip
    typer.echo(publish_clip(job_id, clip, trial))


@app.command("post-next")
def post_next():
    """Post the next clip in the queue. Schedule this with cron or Task Scheduler."""
    from .publish import post_next as _pn
    res = _pn()
    typer.echo(res or "Queue is empty.")


@app.command()
def stats():
    """Pull Instagram insights and refresh the judge's feedback file."""
    from .publish import sync_stats
    for p in sync_stats():
        typer.echo(f"{p.get('hook_title')}: {p.get('stats')}")


@app.command()
def mcp():
    """Run Echo as an MCP server (used by Claude Code, Antigravity, Cursor, Codex, Gemini CLI...)."""
    from .mcp_server import main
    main()


@app.command()
def connect(agent: str = typer.Argument("all", help="claude-code | claude-desktop | cursor | antigravity | codex | gemini | windsurf | vscode | all"),
            write: bool = typer.Option(True, "--write/--print", help="Write the config for you, or only print it")):
    """Plug Echo into your AI agents as an MCP server, in one command."""
    from .connect import connect as do_connect
    for line in do_connect(agent, write):
        typer.echo(line)


@app.command()
def start():
    """The beginner wizard: paste a link, answer two questions, get your Reels."""
    from .wizard import start as wizard
    wizard()


@app.command()
def export(job_id: str, to: str = typer.Option("", "--to", help="Folder (default: Videos/Echo)")):
    """Copy finished clips, covers and captions into one easy folder."""
    from . import api
    res = api.export(job_id, to)
    typer.secho(f"{res['clips']} clips saved to {res['folder']}", fg="green")


@app.command()
def cover(job_id: str, clip: str):
    """Make a grid-safe cover image with the hook in big type."""
    from . import api
    typer.echo(api.make_cover(job_id, clip))


@app.command()
def besttime():
    """When your followers are online, from Instagram insights."""
    from . import api
    res = api.best_time()
    typer.echo(f"Best hours (UTC): {', '.join(f'{h}:00' for h in res['best_hours_utc'])}")
    typer.echo(res["note"])


@app.command("install-skill")
def install_skill(force: bool = typer.Option(False, "--force", help="Overwrite an existing copy")):
    """Install the /echo skill into Claude Code (~/.claude/skills/echo)."""
    import shutil
    from pathlib import Path
    src = Path(__file__).parent / "claude_skill" / "SKILL.md"
    dest_dir = Path.home() / ".claude" / "skills" / "echo"
    old = Path.home() / ".claude" / "skills" / "hookcut"
    if old.exists():
        shutil.rmtree(old, ignore_errors=True)
    dest = dest_dir / "SKILL.md"
    if dest.exists() and not force:
        typer.echo(f"Already installed at {dest}. Use --force to update it.")
        raise typer.Exit(0)
    dest_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    typer.secho(f"Installed /echo for Claude Code at {dest}", fg="green")
    typer.echo('Open Claude Code anywhere and type:  /echo https://youtube.com/watch?v=...')


@app.command()
def doctor(fix: bool = typer.Option(False, "--fix", help="Install what's missing")):
    """Check your setup (and fix it with --fix)."""
    from .doctor import checks, fix_all
    if fix:
        for line in fix_all():
            typer.echo(line)
    for c in checks():
        mark = typer.style("ok ", fg="green") if c["ok"] else typer.style("fix", fg="yellow")
        typer.echo(f"{mark} {c['name']:<18} {c['detail']}" + (f"\n    → {c['fix']}" if c["fix"] else ""))


if __name__ == "__main__":
    app()
