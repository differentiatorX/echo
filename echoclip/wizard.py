"""`echoclip start`: the no-experience-needed path. Paste a link, answer two questions, get Reels."""
from __future__ import annotations

import os
import platform
import subprocess
import threading
import time

import typer

from .captions import PRESETS
from .config import LENGTHS
from .pipeline import STAGES, Job, run_job
from .util import which

STYLES = ["echo", "beast", "solo", "neon", "karaoke", "minimal"]
STYLE_HINT = {"echo": "clean, ice-blue highlight", "beast": "loud and bold", "solo": "one huge word at a time",
              "neon": "glowing electric", "karaoke": "words fill as they're spoken", "minimal": "calm and small"}


def _open(path: str) -> None:
    try:
        if platform.system() == "Windows":
            os.startfile(path)  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", path])
        else:
            subprocess.run(["xdg-open", path])
    except Exception:
        pass


def start() -> None:
    typer.secho("\n  echo  ·  the few Reels that echo\n", fg="cyan", bold=True)
    from .doctor import checks
    missing = [c for c in checks() if not c["ok"] and c["name"] in ("ffmpeg", "yt-dlp", "faster-whisper")]
    if missing:
        typer.secho("A few things need installing first:", fg="yellow")
        for c in missing:
            typer.echo(f"  - {c['name']}: {c['fix']}")
        if typer.confirm("Install them now?", default=True):
            from .doctor import fix_all
            for line in fix_all():
                typer.echo(line)
        else:
            raise typer.Exit(1)

    source = typer.prompt("Paste a YouTube link (or drag a video file here)").strip().strip('"')
    count = typer.prompt("How many Reels", default=4, type=int)
    typer.echo("Caption style:")
    for i, s in enumerate(STYLES, 1):
        typer.echo(f"  {i}. {PRESETS[s]['label']:<8} {STYLE_HINT[s]}")
    pick = typer.prompt("Pick a number", default=1, type=int)
    style = STYLES[max(1, min(len(STYLES), pick)) - 1]
    length = typer.prompt("Length: short, medium or long", default="medium")
    topic = typer.prompt("Only clips about a topic? (press Enter to skip)", default="", show_default=False)

    opts = {"count": count, "preset": style, "length": length if length in LENGTHS else "medium"}
    if topic:
        opts["topic"] = topic
    has_judge = bool(which("claude")) or bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not has_judge:
        opts["judge"] = "agent"

    job = Job.create(source, opts)
    done = threading.Event()

    def watch():
        last = None
        while not done.is_set():
            s = job.status()
            stage = s.get("stage")
            if stage and stage != last:
                label = dict(STAGES).get(stage, stage)
                typer.echo(f"  … {label}")
                last = stage
            time.sleep(1.5)

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    run_job(job)
    done.set()
    s = job.status()

    if s["state"] == "error":
        typer.secho(f"\nSomething went wrong: {s.get('error')}", fg="red")
        typer.echo("Run `echoclip doctor` and try again. Finished steps are kept.")
        raise typer.Exit(1)
    if s["state"] == "awaiting_judge":
        typer.secho("\nThe video is read. Now your AI agent picks the best moments.", fg="cyan")
        typer.echo("Open Claude Code, Antigravity, Cursor or Codex and say:\n")
        typer.secho(f'  Use echo to finish job {job.id}. Read its brief, judge the moments, then cut, review and fix the clips.\n',
                    fg="green")
        typer.echo("Not connected yet? Run `echoclip connect` first.")
        return

    from . import api
    res = api.export(job.id)
    typer.secho(f"\nDone. {res['clips']} Reels with covers and captions are in:", fg="green")
    typer.echo(f"  {res['folder']}")
    _open(res["folder"])
