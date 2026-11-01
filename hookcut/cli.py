"""hookcut command line."""
from __future__ import annotations

import time
import webbrowser

import typer

from .config import load_settings

app = typer.Typer(add_completion=False, help="Long video in, the few Reels that hit out.")


@app.command()
def ui(port: int = typer.Option(None), open_browser: bool = typer.Option(True, "--open/--no-open")):
    """Start the studio at http://127.0.0.1:7777"""
    import uvicorn
    cfg = load_settings()["server"]
    port = port or cfg["port"]
    url = f"http://{cfg['host']}:{port}"
    typer.echo(f"Hookcut studio → {url}")
    if open_browser:
        webbrowser.open(url)
    uvicorn.run("hookcut.server:app", host=cfg["host"], port=port, log_level="warning")


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
def doctor():
    """Check your setup."""
    from .doctor import checks
    for c in checks():
        mark = typer.style("ok ", fg="green") if c["ok"] else typer.style("fix", fg="yellow")
        typer.echo(f"{mark} {c['name']:<18} {c['detail']}" + (f"\n    → {c['fix']}" if c["fix"] else ""))


if __name__ == "__main__":
    app()
