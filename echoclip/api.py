"""One set of functions behind every interface: CLI, MCP server (any agent), studio."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import ASPECTS, JOBS, LENGTHS
from .pipeline import Job, _cfg, render_clip, run_job
from .util import EchoError, probe, read_json, run, write_json


def _job(job_id: str) -> Job:
    job = Job(job_id)
    if not job.status():
        raise EchoError(f"No job called {job_id}. Run prepare first.")
    return job


def guide() -> str:
    return (Path(__file__).parent / "agent_guide.md").read_text("utf-8")


def prepare(source: str, clips: int = 4, style: str = "echo", topic: str = "", length: str = "medium",
            aspect: str = "9:16", language: str = "", sfx: bool | None = None, music: str | None = None) -> dict:
    opts = {"count": clips, "preset": style, "judge": "agent"}
    if topic:
        opts["topic"] = topic
    if length in LENGTHS:
        opts["length"] = length
    if aspect in ASPECTS:
        opts["aspect"] = aspect
    if language:
        opts["language"] = language
    if sfx is not None:
        opts["sfx"] = sfx
    if music is not None:
        opts["music"] = music
    job = Job.create(source, opts)
    run_job(job)
    s = job.status()
    if s["state"] == "error":
        raise EchoError(s.get("error") or "prepare failed")
    return {"job": job.id, "title": s.get("title"), "state": s["state"],
            "brief": str(job.dir / "brief.md"), "write_judgments_to": str(job.dir / "judgments.json"),
            "signals": s.get("signals"),
            "next": f"Read the brief, then call submit_judgments (or write the JSON file) and finish {job.id}."}


def read_brief(job_id: str) -> str:
    return (_job(job_id).dir / "brief.md").read_text("utf-8")


def submit_judgments(job_id: str, judgments: list[dict]) -> dict:
    job = _job(job_id)
    if not isinstance(judgments, list) or not judgments:
        raise EchoError("judgments must be a non-empty list of clip objects")
    write_json(job.dir / "judgments.json", judgments)
    return {"job": job_id, "saved": len(judgments), "next": f"finish {job_id}"}


def finish(job_id: str, background: bool = True) -> dict:
    job = _job(job_id)
    if not (job.dir / "judgments.json").exists() and not job.clips():
        raise EchoError("No judgments yet. Submit them first.")
    if background:
        kw = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
        log = open(job.dir / "finish.log", "w", encoding="utf-8")
        subprocess.Popen([sys.executable, "-m", "echoclip.cli", "finish", job_id], stdout=log,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kw)
        job.update(state="running")
        return {"job": job_id, "state": "running", "next": f"poll status {job_id} every 20-30 seconds"}
    run_job(job)
    return status(job_id)


def status(job_id: str) -> dict:
    job = _job(job_id)
    s = job.status()
    out = {k: s.get(k) for k in ("id", "title", "state", "stage", "error", "stages")}
    out["clips"] = [{"id": c["id"], "state": c.get("state"), "score": c.get("score"), "error": c.get("error")}
                    for c in job.clips()]
    return out


def instagram_check(c: dict, plan: dict) -> list[str]:
    """Plain-language warnings tied to what Instagram rewards: watch time, sends, likes."""
    notes = []
    words = plan.get("words", [])
    dur = plan.get("duration") or 0
    if words and words[0]["s"] > 0.6:
        notes.append("Speech starts late. Viewers decide in the first second; trim the opening.")
    if dur > 90:
        notes.append("Over 90 s. Long Reels lose completion rate; consider ending earlier.")
    if dur and dur < 12:
        notes.append("Under 12 s. Fine for replays, but make sure the payoff lands.")
    if not c.get("hook_title"):
        notes.append("No on-screen hook. Add one for sound-off viewers.")
    tags = c.get("hashtags") or []
    if len(tags) > 5:
        notes.append("More than 5 hashtags. Keep 3-5 specific ones.")
    cap = (c.get("ig_caption") or "").lower()
    if cap and not ("?" in cap or "send" in cap or "tag" in cap):
        notes.append("Caption has no question or send prompt. Sends are Instagram's strongest reach signal.")
    return notes


def clips(job_id: str) -> list[dict]:
    job = _job(job_id)
    out = []
    for c in job.clips():
        d = job.dir / c["id"]
        plan = read_json(d / "edit.json", {}) or {}
        out.append({
            "id": c["id"], "state": c.get("state"), "score": c.get("score"), "breakdown": c.get("breakdown"),
            "hook_title": c.get("hook_title"), "why": c.get("why"), "duration": c.get("duration"),
            "cold_open": plan.get("cold_open"), "style": (c.get("overrides") or {}).get("preset") or "default",
            "ig_caption": c.get("ig_caption"), "hashtags": c.get("hashtags"),
            "video": str(d / f"{c['id']}.mp4") if c.get("state") == "ready" else None,
            "cover": str(d / "cover.jpg") if (d / "cover.jpg").exists() else None,
            "instagram_check": instagram_check(c, plan) if plan else [],
            "words": [{"i": w["_i"], "w": w["w"], "t": w["s"]} for w in plan.get("words", [])],
            "error": c.get("error"),
        })
    return out


def frames(job_id: str, clip: str) -> list[str]:
    job = _job(job_id)
    video = job.dir / clip / f"{clip}.mp4"
    if not video.exists():
        raise EchoError("Clip isn't rendered yet.")
    dur = probe(video)["dur"]
    paths = []
    for name, t in (("open", 0.6), ("middle", dur / 2), ("end", max(0.0, dur - 1.0))):
        out = job.dir / clip / f"frame_{name}.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", "scale=540:-2", str(out)], timeout=60)
        paths.append(str(out))
    return paths


def fix(job_id: str, clip: str, start_at: int | None = None, end_after: int | None = None, cut: str = "",
        hook: str = "", cold_open: bool | None = None, style: str = "", reset: bool = False,
        trim: bool | None = None, replace: str = "") -> dict:
    job = _job(job_id)
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
    for part in (cut or "").replace(" ", "").split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            removed |= set(range(int(a), int(b) + 1))
        elif part:
            removed.add(int(part))
    ov["removed"] = sorted(removed)
    if cold_open is not None:
        ov["cold_open"] = cold_open
    if style:
        ov["preset"] = None if style in ("echo", "hookcut", "default") else style
    if trim is not None:
        ov["trim"] = trim
    if replace:
        rep = dict(ov.get("replace") or {})
        for pair in replace.split(","):
            if "=" in pair:
                a, b = pair.split("=", 1)
                rep[a.strip()] = b.strip()
        ov["replace"] = rep
    patch = {"overrides": ov}
    if hook:
        patch["hook_title"] = hook
    job.patch_clip(clip, **patch)
    res = render_clip(job, clip)
    return {"clip": clip, "duration": res.get("duration"), "video": str(job.dir / clip / f"{clip}.mp4")}


def make_cover(job_id: str, clip: str) -> str:
    from .render import cover
    job = _job(job_id)
    c = job.clip(clip)
    size = ASPECTS.get(_cfg(job)["render"].get("aspect"), ASPECTS["9:16"])
    out = cover(job.dir / clip / f"{clip}.mp4", c.get("hook_title", ""), job.dir / clip / "cover.jpg", size)
    return str(out)


def export(job_id: str, dest: str = "") -> dict:
    """Copy finished clips, covers and ready-to-paste captions into one easy folder."""
    job = _job(job_id)
    title = "".join(ch for ch in (job.status().get("title") or job_id) if ch.isalnum() or ch in " -_").strip()[:60]
    base = Path(dest).expanduser() if dest else Path.home() / "Videos" / "Echo"
    folder = base / (title or job_id)
    folder.mkdir(parents=True, exist_ok=True)
    n = 0
    for c in job.clips():
        if c.get("state") != "ready":
            continue
        d = job.dir / c["id"]
        stem = f"{c.get('rank', 0):02d} {''.join(ch for ch in (c.get('hook_title') or c['id']) if ch.isalnum() or ch in ' -_').strip()[:50]}"
        shutil.copy2(d / f"{c['id']}.mp4", folder / f"{stem}.mp4")
        if not (d / "cover.jpg").exists():
            try:
                make_cover(job_id, c["id"])
            except EchoError:
                pass
        if (d / "cover.jpg").exists():
            shutil.copy2(d / "cover.jpg", folder / f"{stem} cover.jpg")
        (folder / f"{stem} caption.txt").write_text(
            f"{c.get('ig_caption', '')}\n\n{' '.join(c.get('hashtags') or [])}\n", "utf-8")
        n += 1
    return {"folder": str(folder), "clips": n}


def best_time() -> dict:
    """When your followers are online, from Instagram insights (needs the Instagram connection)."""
    import requests
    from .config import load_settings
    p = load_settings()["publish"]
    if not p["ig_user_id"] or not p["access_token"]:
        raise EchoError("Connect Instagram in Settings first.")
    host = "https://graph.instagram.com" if p["mode"] == "instagram" else "https://graph.facebook.com"
    r = requests.get(f"{host}/{p['api_version']}/{p['ig_user_id']}/insights",
                     params={"metric": "online_followers", "period": "lifetime"},
                     headers={"Authorization": f"Bearer {p['access_token']}"}, timeout=30)
    data = r.json().get("data") or []
    if not data:
        raise EchoError(f"Instagram returned no follower activity: {r.text[:200]}")
    totals = [0] * 24
    for v in data[0].get("values", []):
        for hour, count in (v.get("value") or {}).items():
            totals[int(hour)] += int(count or 0)
    ranked = sorted(range(24), key=lambda h: -totals[h])
    return {"best_hours_utc": ranked[:3], "by_hour_utc": totals,
            "note": "Hours are in UTC as Instagram reports them. Post 30-60 minutes before the peak."}


def publish(job_id: str, clip: str, trial: bool | None = None) -> dict:
    from .publish import publish_clip
    return publish_clip(job_id, clip, trial)


def list_jobs() -> list[dict]:
    from .pipeline import list_jobs as lj
    return [{"id": j["id"], "title": j.get("title"), "state": j.get("state"), "clips": j.get("clip_count")}
            for j in lj()]


def shots(job_id: str, clip: str) -> dict:
    from .direct import shots as _shots
    return _shots(_job(job_id), clip)


def direct(job_id: str, clip: str, directions: list[dict]) -> dict:
    from .direct import direct as _direct
    from .transcribe import unload
    unload()
    return _direct(_job(job_id), clip, directions)
