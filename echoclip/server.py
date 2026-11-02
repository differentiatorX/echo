"""Local web studio. Runs on your machine; nothing leaves it except calls you trigger."""
from __future__ import annotations

import queue
import shutil
import threading
import traceback
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, publish
from .captions import PRESETS
from .config import JOBS, POSTED_PATH, UPLOADS, WEB, load_settings, save_settings
from .doctor import checks
from .pipeline import Job, list_jobs, render_clip, run_job
from .util import read_json

app = FastAPI(title="Echo", version=__version__)
_tasks: "queue.Queue[tuple]" = queue.Queue()


def _worker():
    while True:
        kind, args = _tasks.get()
        try:
            if kind == "job":
                run_job(Job(args[0]))
            elif kind == "render":
                job = Job(args[0])
                try:
                    render_clip(job, args[1])
                except Exception as e:
                    job.patch_clip(args[1], state="error", error=str(e)[:400])
            elif kind == "publish":
                job = Job(args[0])
                try:
                    res = publish.publish_clip(args[0], args[1], args[2])
                    job.patch_clip(args[1], posted=res["posted"], media_id=res["media_id"], publish_error=None)
                except Exception as e:
                    job.patch_clip(args[1], publish_error=str(e)[:400])
        except Exception:
            traceback.print_exc()
        finally:
            _tasks.task_done()


threading.Thread(target=_worker, daemon=True).start()


def _job(job_id: str) -> Job:
    job = Job(job_id)
    if not job.status():
        raise HTTPException(404, "No such job")
    return job


@app.get("/api/meta")
def meta():
    return {"version": __version__, "presets": {k: {"label": v["label"], "active": v["active"],
                                                     "keyword": v["keyword"]} for k, v in PRESETS.items()}}


@app.get("/api/doctor")
def doctor():
    return checks()


@app.get("/api/jobs")
def jobs():
    return list_jobs()


@app.post("/api/jobs")
def create_job(body: dict = Body(...)):
    source = (body.get("source") or "").strip()
    if not source:
        raise HTTPException(400, "Paste a YouTube link or upload a video.")
    if not source.startswith(("http://", "https://")) and not Path(source).exists():
        raise HTTPException(400, "That file doesn't exist on this machine.")
    opts = {k: body[k] for k in ("count", "min_len", "max_len", "preset", "cold_open", "length", "aspect", "topic")
            if k in body and body[k] not in ("", None)}
    job = Job.create(source, opts)
    _tasks.put(("job", (job.id,)))
    return job.status()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = UPLOADS / Path(file.filename or "video.mp4").name
    with dest.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    return {"path": str(dest)}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str):
    job = _job(job_id)
    info = read_json(job.dir / "info.json", {}) or {}
    ranked = read_json(job.dir / "ranked.json", []) or []
    return {**job.status(), "clips": job.clips(), "heatmap": info.get("heatmap", []),
            "chapters": info.get("chapters", []),
            "candidates": [{"start": r["start"], "end": r["end"], "score": r.get("score"),
                            "hook_title": r.get("hook_title")} for r in ranked]}


@app.post("/api/jobs/{job_id}/retry")
def retry(job_id: str):
    job = _job(job_id)
    job.update(state="queued", error=None)
    _tasks.put(("job", (job.id,)))
    return job.status()


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    job = _job(job_id)
    shutil.rmtree(job.dir, ignore_errors=True)
    return {"ok": True}


@app.get("/api/jobs/{job_id}/clips/{cid}/words")
def clip_words(job_id: str, cid: str):
    job = _job(job_id)
    c = job.clip(cid)
    words = read_json(job.dir / cid / "words.json", []) or []
    s, e = c.get("local_start", 0), c.get("local_end", 0)
    return {"words": [{"i": i, "w": w["w"], "s": w["s"]} for i, w in enumerate(words)
                      if s - 0.05 <= (w["s"] + w["e"]) / 2 <= e + 0.05],
            "removed": (c.get("overrides") or {}).get("removed", [])}


@app.patch("/api/jobs/{job_id}/clips/{cid}")
def patch_clip(job_id: str, cid: str, body: dict = Body(...)):
    job = _job(job_id)
    allowed = {k: v for k, v in body.items() if k in ("hook_title", "ig_caption", "hashtags", "approved")}
    if "overrides" in body:
        ov = {**(job.clip(cid).get("overrides") or {}), **body["overrides"]}
        allowed["overrides"] = ov
    return job.patch_clip(cid, **allowed)


@app.post("/api/jobs/{job_id}/clips/{cid}/render")
def rerender(job_id: str, cid: str):
    job = _job(job_id)
    job.patch_clip(cid, state="queued")
    _tasks.put(("render", (job_id, cid)))
    return job.clip(cid)


@app.post("/api/jobs/{job_id}/clips/{cid}/publish")
def publish_now(job_id: str, cid: str, body: dict = Body(default={})):
    job = _job(job_id)
    job.patch_clip(cid, publish_error=None, publishing=True)
    _tasks.put(("publish", (job_id, cid, body.get("trial"))))
    return job.clip(cid)


@app.get("/api/queue")
def get_queue():
    q = publish.queue()
    out = []
    for item in q:
        try:
            c = Job(item["job"]).clip(item["clip"])
            out.append({**item, "hook_title": c.get("hook_title"), "cover": c.get("cover"), "score": c.get("score")})
        except (KeyError, FileNotFoundError):
            continue
    return {"queue": out, "posted": list(reversed(read_json(POSTED_PATH, []) or []))}


@app.post("/api/queue")
def add_queue(body: dict = Body(...)):
    return publish.enqueue(body["job"], body["clip"])


@app.delete("/api/queue/{job_id}/{cid}")
def remove_queue(job_id: str, cid: str):
    return publish.dequeue(job_id, cid)


@app.post("/api/stats/sync")
def stats_sync():
    return publish.sync_stats()


@app.get("/api/settings")
def get_settings():
    s = load_settings()
    tok = s["publish"]["access_token"]
    s["publish"]["access_token"] = ("•" * 8 + tok[-4:]) if tok else ""
    return s


@app.put("/api/settings")
def put_settings(body: dict = Body(...)):
    tok = body.get("publish", {}).get("access_token", "")
    if tok.startswith("•"):
        body["publish"].pop("access_token")
    save_settings(body)
    return get_settings()


@app.get("/media/{job_id}/{cid}/{name}")
def media(job_id: str, cid: str, name: str):
    p = (JOBS / job_id / cid / name).resolve()
    if JOBS.resolve() not in p.parents or not p.exists():
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "no-cache"})


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
