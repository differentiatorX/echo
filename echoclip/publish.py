"""Instagram Reels publishing (official Graph API), a posting queue for cron, and the feedback loop
that feeds real performance back into the judge."""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

from .config import FEEDBACK, JOBS, POSTED_PATH, QUEUE_PATH, load_settings
from .util import EchoError, read_json, write_json


def _caption(clip: dict) -> str:
    tags = " ".join(clip.get("hashtags") or [])
    return f"{clip.get('ig_caption', '').strip()}\n\n{tags}".strip()


def _check(r: requests.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        data = {"raw": r.text[:300]}
    if r.status_code >= 400 or "error" in data:
        msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else data
        raise EchoError(f"Instagram API: {msg}")
    return data


def publish_clip(job_id: str, cid: str, trial: bool | None = None) -> dict:
    cfg = load_settings()["publish"]
    if not cfg["ig_user_id"] or not cfg["access_token"]:
        raise EchoError("Add your Instagram user ID and access token in Settings first.")
    clips = read_json(JOBS / job_id / "clips.json", []) or []
    clip = next((c for c in clips if c["id"] == cid), None)
    if not clip or clip.get("state") != "ready":
        raise EchoError("Clip isn't rendered yet.")
    video = JOBS / job_id / cid / f"{cid}.mp4"
    ver, ig, tok = cfg["api_version"], cfg["ig_user_id"], cfg["access_token"]
    trial = cfg["trial"] if trial is None else trial
    params = {"media_type": "REELS", "caption": _caption(clip), "thumb_offset": 1200}
    if trial:
        params["trial_params"] = json.dumps({"graduation_strategy": cfg["graduation"]})
    if cfg.get("ai_label"):
        params["is_ai_generated"] = "true"

    if cfg["mode"] == "instagram":
        if not cfg["public_base_url"]:
            raise EchoError("Instagram Login mode needs a public URL for the video (see README: tunnels).")
        host = "https://graph.instagram.com"
        params["video_url"] = f"{cfg['public_base_url'].rstrip('/')}/media/{job_id}/{cid}/{cid}.mp4"
        cont = _check(requests.post(f"{host}/{ver}/{ig}/media", data=params,
                                    headers={"Authorization": f"Bearer {tok}"}, timeout=60))
    else:
        host = "https://graph.facebook.com"
        params["upload_type"] = "resumable"
        cont = _check(requests.post(f"{host}/{ver}/{ig}/media", data=params,
                                    headers={"Authorization": f"Bearer {tok}"}, timeout=60))
        size = video.stat().st_size
        with open(video, "rb") as fh:
            _check(requests.post(f"https://rupload.facebook.com/ig-api-upload/{ver}/{cont['id']}",
                                 headers={"Authorization": f"OAuth {tok}", "offset": "0",
                                          "file_size": str(size)}, data=fh, timeout=600))
    cid_ = cont["id"]
    for _ in range(30):  # up to ~7.5 min
        st = _check(requests.get(f"{host}/{ver}/{cid_}", params={"fields": "status_code"},
                                 headers={"Authorization": f"Bearer {tok}"}, timeout=30))
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise EchoError(f"Instagram processing failed: {st}")
        time.sleep(15)
    pub = _check(requests.post(f"{host}/{ver}/{ig}/media_publish", data={"creation_id": cid_},
                               headers={"Authorization": f"Bearer {tok}"}, timeout=60))
    posted = read_json(POSTED_PATH, []) or []
    posted.append({"job": job_id, "clip": cid, "media_id": pub["id"], "posted": time.time(), "trial": trial,
                   "hook_title": clip.get("hook_title"), "score": clip.get("score"),
                   "text": " ".join(w["w"] for w in (read_json(JOBS / job_id / cid / "edit.json", {}) or {})
                                    .get("words", []))[:600]})
    write_json(POSTED_PATH, posted)
    return posted[-1]


# ---------- queue (drain with cron / Task Scheduler: `echoclip post-next`) ----------

def queue() -> list[dict]:
    return read_json(QUEUE_PATH, []) or []


def enqueue(job_id: str, cid: str) -> list[dict]:
    q = [x for x in queue() if not (x["job"] == job_id and x["clip"] == cid)]
    q.append({"job": job_id, "clip": cid, "added": time.time()})
    write_json(QUEUE_PATH, q)
    return q


def dequeue(job_id: str, cid: str) -> list[dict]:
    q = [x for x in queue() if not (x["job"] == job_id and x["clip"] == cid)]
    write_json(QUEUE_PATH, q)
    return q


def post_next() -> dict | None:
    q = queue()
    if not q:
        return None
    item = q[0]
    res = publish_clip(item["job"], item["clip"])
    write_json(QUEUE_PATH, q[1:])
    return res


# ---------- feedback loop ----------

METRICS = ["views", "reach", "saved", "shares", "total_interactions", "ig_reels_avg_watch_time"]


def sync_stats() -> list[dict]:
    cfg = load_settings()["publish"]
    host = "https://graph.instagram.com" if cfg["mode"] == "instagram" else "https://graph.facebook.com"
    posted = read_json(POSTED_PATH, []) or []
    for p in posted:
        stats = {}
        for m in METRICS:
            try:
                r = requests.get(f"{host}/{cfg['api_version']}/{p['media_id']}/insights",
                                 params={"metric": m},
                                 headers={"Authorization": f"Bearer {cfg['access_token']}"}, timeout=30)
                for item in r.json().get("data", []):
                    stats[item["name"]] = (item.get("values") or [{}])[0].get("value")
            except (requests.RequestException, ValueError, KeyError):
                continue
        if stats:
            p["stats"] = stats
    write_json(POSTED_PATH, posted)
    write_winners(posted)
    return posted


def _perf(p: dict) -> float:
    s = p.get("stats") or {}
    return ((s.get("views") or 0) / 1000 + (s.get("shares") or 0) * 3 + (s.get("saved") or 0) * 2
            + (s.get("ig_reels_avg_watch_time") or 0) / 1000)


def write_winners(posted: list[dict]) -> None:
    rated = [p for p in posted if p.get("stats")]
    if len(rated) < 4:
        return
    rated.sort(key=_perf, reverse=True)
    k = min(5, len(rated) // 2)

    def fmt(p):
        s = p["stats"]
        return (f"- \"{p.get('hook_title')}\" | views {s.get('views')}, shares {s.get('shares')}, "
                f"saves {s.get('saved')}, avg watch {round((s.get('ig_reels_avg_watch_time') or 0) / 1000, 1)}s\n"
                f"  opens: {p.get('text', '')[:180]}")
    FEEDBACK.mkdir(parents=True, exist_ok=True)
    (FEEDBACK / "winners.md").write_text(
        "TOP PERFORMERS\n" + "\n".join(fmt(p) for p in rated[:k]) +
        "\n\nFLOPS\n" + "\n".join(fmt(p) for p in rated[-k:]) + "\n", "utf-8")
