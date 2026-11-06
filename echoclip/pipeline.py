"""Job runner. Every artifact lives in ~/.echo/jobs/<id>/ and every stage is idempotent,
so re-running a job resumes where it stopped and re-rendering a clip takes seconds."""
from __future__ import annotations

import os
import re
import threading
import time
import traceback
from pathlib import Path

from . import captions, edit, ingest, reframe, render, select
from .config import ASPECTS, JOBS, LENGTHS, load_settings
from .signals import Signals
from .util import EchoError, Timer, read_json, write_json

STAGES = [
    ("info", "Read video"),
    ("captions", "Rough transcript"),
    ("scout", "Scout moments"),
    ("judge", "Judge and rank"),
    ("cut", "Cut and render"),
]


def video_id(source: str) -> str:
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})", source)
    if m:
        return m.group(1)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(source).stem)[:40].strip("-") or "video"
    return f"{stem}-{int(time.time()) % 100000}"


class Job:
    def __init__(self, job_id: str):
        self.id = job_id
        self.dir = JOBS / job_id
        self._lock = threading.Lock()

    # ---- persistence ----
    @property
    def status_path(self) -> Path:
        return self.dir / "status.json"

    def status(self) -> dict:
        return read_json(self.status_path, {}) or {}

    def update(self, **kw) -> dict:
        with self._lock:
            s = self.status()
            s.update(kw)
            s["updated"] = time.time()
            write_json(self.status_path, s)
            return s

    def clips(self) -> list[dict]:
        return read_json(self.dir / "clips.json", []) or []

    def save_clips(self, clips: list[dict]) -> None:
        with self._lock:
            write_json(self.dir / "clips.json", clips)

    def patch_clip(self, cid: str, **kw) -> dict:
        with self._lock:
            clips = read_json(self.dir / "clips.json", []) or []
            for c in clips:
                if c["id"] == cid:
                    c.update(kw)
                    write_json(self.dir / "clips.json", clips)
                    return c
        raise KeyError(cid)

    def clip(self, cid: str) -> dict:
        for c in self.clips():
            if c["id"] == cid:
                return c
        raise KeyError(cid)

    def stage(self, key: str, state: str, secs: float | None = None) -> None:
        s = self.status()
        stages = s.get("stages", {})
        stages[key] = {"status": state, "secs": secs}
        self.update(stages=stages, stage=key if state == "running" else s.get("stage"))

    @classmethod
    def create(cls, source: str, opts: dict | None = None) -> "Job":
        job = cls(video_id(source))
        job.dir.mkdir(parents=True, exist_ok=True)
        if not job.status():
            job.update(id=job.id, source=source, kind="url" if ingest.is_url(source) else "file",
                       title=source, created=time.time(), state="queued", stages={}, opts=opts or {},
                       error=None)
        else:
            job.update(state="queued", error=None, opts=opts or job.status().get("opts", {}))
        return job


def list_jobs() -> list[dict]:
    out = []
    if JOBS.exists():
        for d in JOBS.iterdir():
            s = read_json(d / "status.json")
            if s:
                clips = read_json(d / "clips.json", []) or []
                s["clip_count"] = len(clips)
                s["best"] = max((c.get("score", 0) for c in clips), default=None)
                out.append(s)
    return sorted(out, key=lambda s: s.get("created", 0), reverse=True)


def _cfg(job: Job) -> dict:
    cfg = load_settings()
    o = job.status().get("opts") or {}
    for k in ("count", "min_len", "max_len"):
        if o.get(k):
            cfg["clips"][k] = int(o[k])
    if o.get("preset"):
        cfg["style"]["preset"] = o["preset"]
    if "cold_open" in o:
        cfg["edit"]["cold_open"] = bool(o["cold_open"])
    if o.get("judge") == "agent":
        cfg["llm"]["backend"] = "agent"
    if o.get("length") in LENGTHS:
        cfg["clips"]["min_len"], cfg["clips"]["max_len"] = LENGTHS[o["length"]]
    if o.get("topic"):
        cfg["clips"]["topic"] = o["topic"]
    if o.get("aspect") in ASPECTS:
        cfg["render"]["aspect"] = o["aspect"]
    for k in ("sfx", "music", "logo", "watermark"):
        if k in o and o[k] is not None:
            cfg["style"][k] = o[k]
    if o.get("language"):
        cfg["language"] = o["language"]
    return cfg


BRIEF = """# Echo judging brief

You are judging clips for: "{title}" by {channel}

Read the timestamped transcript below. Lines may carry audience tags:
- <REPLAYED> = YouTube's Most replayed graph spikes here. Real viewers rewound this.
- <COMMENTED xN> = N top comments quote this timestamp.
Treat tags as strong evidence, but a tagged line still has to work as a standalone clip.

{topic}Find about {cands} candidate clips, each {min_len}-{max_len} seconds, starting and ending on sentence boundaries,
no two overlapping by more than 10 s.

These are for Instagram Reels. Instagram's head has said the signals that matter most are watch time,
sends per reach (people DMing the Reel to a friend) and likes per reach, with sends weighing most for
reaching non-followers, and that original content is favored. So prefer moments that are rewatchable,
that someone would send to a specific friend ("this is so you"), and that feel raw and real.

Then score every candidate harshly, 0-10 each (a 7 is good, 9-10 is rare):
- hook: do the first 3 seconds alone stop a stranger scrolling?
- flow: complete arc, no missing context, no dead stretches
- value: insight, emotion, humor or tension a viewer actually feels
- share: would someone DM it to a specific friend, or argue in the comments? (Instagram's strongest reach signal)
- loop: does the ending land so hard people rewatch?
Skip intros, outros, sponsor reads, housekeeping and inside jokes.

For each candidate also write:
- hook_title: 3-7 words for the screen, specific and true, no emojis or hashtags
- cold_open: an EXACT 4-14 word quote from inside the clip to tease first, or null
- keywords: 2-4 single words to highlight in captions
- ig_caption: under 150 characters. Start with a keyword-rich first line people would search for
  (Instagram search reads captions), end with a question or a "send this to someone who..." line
- hashtags: 3-5 niche hashtags (no #fyp #viral #reels)
- why: one honest sentence

{feedback}

Write ONLY a JSON array to this exact file:
{out}

Format:
[{{"start": 0.0, "end": 0.0, "hook": 0, "flow": 0, "value": 0, "share": 0, "loop": 0, "hook_title": "", "cold_open": null, "keywords": [], "ig_caption": "", "hashtags": [], "why": ""}}]

Then run: echoclip finish {job} --detach

TRANSCRIPT (times in seconds)
{transcript}
"""


class JobBusy(EchoError):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259                                      # STILL_ACTIVE
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def acquire(job: Job) -> None:
    """One worker per job. A second `finish` on a running job used to race the first one's ffmpeg."""
    lock = job.dir / ".running"
    if lock.exists():
        try:
            pid = int(lock.read_text().strip() or 0)
        except ValueError:
            pid = 0
        if pid != os.getpid() and _pid_alive(pid):
            raise JobBusy(f"Job {job.id} is already running (process {pid}). Poll `echoclip status {job.id}` instead.")
    lock.write_text(str(os.getpid()))


def release(job: Job) -> None:
    try:
        lock = job.dir / ".running"
        if lock.exists() and lock.read_text().strip() == str(os.getpid()):
            lock.unlink()
    except OSError:
        pass


def run_job(job: Job) -> None:
    try:
        acquire(job)
    except JobBusy as e:
        print(str(e))
        return
    try:
        _run_job(job)
    finally:
        release(job)


def _run_job(job: Job) -> None:
    cfg = _cfg(job)
    src = job.status()["source"]
    is_url = ingest.is_url(src)
    job.update(state="running", error=None)
    try:
        # 1. info + comments (comments in parallel; they're slow and optional)
        t = Timer()
        job.stage("info", "running")
        info = read_json(job.dir / "info.json")
        comments: list[dict] = read_json(job.dir / "comments.json", []) or []
        th = None
        if not info:
            info = ingest.fetch_info(src) if is_url else ingest.local_info(Path(src))
            write_json(job.dir / "info.json", info)
        if is_url and cfg["signals"]["comments"] and not (job.dir / "comments.json").exists():
            box: dict = {}

            def _get():
                try:
                    box["c"] = ingest.fetch_comments(src, cfg["signals"]["max_comments"])
                except Exception:
                    box["c"] = []
            th = threading.Thread(target=_get, daemon=True)
            th.start()
            job._comments_box = box
        job.update(title=info["title"], channel=info.get("channel"), duration=info.get("duration"),
                   thumbnail=info.get("thumbnail"))
        job.stage("info", "done", t.secs())

        # 2. rough transcript: YouTube captions (seconds) or fast whisper fallback
        t = Timer()
        job.stage("captions", "running")
        rough = read_json(job.dir / "rough_words.json")
        if not rough:
            lang = cfg["language"] if cfg["language"] != "auto" else (info.get("language") or "en")
            rough = ingest.fetch_captions(src, job.dir, lang) if is_url else []
            if not rough:
                from .transcribe import transcribe_words
                audio = Path(src)
                if is_url:
                    audio = job.dir / "audio.m4a"
                    if not audio.exists():
                        from .util import run, ytdlp
                        run(ytdlp() + ["--no-playlist", "-f", "ba[ext=m4a]/ba", "-o", str(audio), src], timeout=900)
                rough = transcribe_words(audio, cfg["whisper"]["rough_model"],
                                         None if cfg["language"] == "auto" else cfg["language"])
            write_json(job.dir / "rough_words.json", rough)
        lines = ingest.words_to_lines(rough)
        job.stage("captions", "done", t.secs())

        if th is not None:
            th.join(timeout=90)
            comments = getattr(job, "_comments_box", {}).get("c", [])
            write_json(job.dir / "comments.json", comments)
        sig = Signals(info, comments, rough)
        job.update(signals={"heatmap": sig.has_heat, "comments": len(sig.mentions)})

        # 3-4. scout + judge + fuse + pick
        clips = job.clips()
        if not clips and cfg["llm"]["backend"] == "agent":
            judged = read_json(job.dir / "judgments.json")
            if not judged:
                c = cfg["clips"]
                transcript = "\n".join(f"[{l['s']:.1f}] {l['text']}{sig.line_tags(l['s'], l['e'])}" for l in lines)
                topic = (f"The user only wants clips about: {c['topic']}. Ignore moments that are not about this.\n\n"
                         if c.get("topic") else "")
                (job.dir / "brief.md").write_text(BRIEF.format(
                    topic=topic, title=info["title"], channel=info.get("channel") or "unknown", cands=c["candidates"],
                    min_len=c["min_len"], max_len=c["max_len"], feedback=select._feedback_block(),
                    out=str(job.dir / "judgments.json"), job=job.id, transcript=transcript), "utf-8")
                job.stage("scout", "waiting")
                job.update(state="awaiting_judge", stage="scout")
                return
            job.stage("scout", "done", 0)
            t = Timer()
            job.stage("judge", "running")
            verdicts = []
            for i, item in enumerate(judged):
                try:
                    v = select.Verdict(**{**item, "id": item.get("id") or f"k{i + 1:02d}"})
                except (select.ValidationError, TypeError):
                    continue
                s_, e_ = select._snap(lines, v.start, v.end)
                verdicts.append({**v.model_dump(), "start": s_, "end": e_, "hook_line": ""})
            if not verdicts:
                raise EchoError("judgments.json had no valid clips. Check its format against brief.md.")
            ranked = select.fuse(verdicts, sig, cfg)
        elif not clips:
            t = Timer()
            job.stage("scout", "running")
            cands = read_json(job.dir / "candidates.json")
            if not cands:
                cands = select.scout(info, lines, sig, cfg)
                write_json(job.dir / "candidates.json", cands)
            if not cands:
                raise EchoError("No usable moments found. Try a longer or more conversational video.")
            job.stage("scout", "done", t.secs())

            t = Timer()
            job.stage("judge", "running")
            ranked = select.fuse(select.judge(info, cands, rough, sig, cfg), sig, cfg)
        if not clips:
            write_json(job.dir / "ranked.json", ranked)
            chosen = select.pick(ranked, cfg["clips"]["count"])
            clips = []
            for i, v in enumerate(chosen):
                clips.append({**{k: v.get(k) for k in ("start", "end", "score", "breakdown", "hook_title",
                                                       "cold_open", "keywords", "ig_caption", "hashtags",
                                                       "why", "hook_line")},
                              "id": f"c{i + 1:02d}", "rank": i + 1, "state": "queued",
                              "approved": False, "overrides": {"removed": [], "cold_open": True}})
            job.save_clips(clips)
            job.stage("judge", "done", t.secs())

        # 5. cut + render: downloads in parallel, transcription in order (one GPU), two renders at a time
        t = Timer()
        job.stage("cut", "running")
        from concurrent.futures import ThreadPoolExecutor
        todo = [c for c in clips if not (c.get("state") == "ready" and (job.dir / c["id"] / f"{c['id']}.mp4").exists())]

        def guard(fn, cid, *a):
            try:
                fn(job, cid, *a)
                return True
            except Exception as e:  # one bad clip shouldn't sink the job
                job.patch_clip(cid, state="error", error=str(e)[:400])
                return False

        with ThreadPoolExecutor(3) as pool:
            ok = dict(zip([c["id"] for c in todo], pool.map(lambda c: guard(fetch_clip, c["id"], info), todo)))
        ready = [cid for cid, good in ok.items() if good and guard(analyze_clip, cid, cfg)]
        from .transcribe import unload
        unload()                                   # free GPU memory before ffmpeg/NVENC needs it
        for cid in ready:                          # one render at a time: no encoder or VRAM contention
            guard(render_clip, cid, cfg)
        job.stage("cut", "done", t.secs())
        job.update(state="done", stage=None)
    except Exception as e:
        traceback.print_exc()
        s = job.status()
        if s.get("stage"):
            job.stage(s["stage"], "error")
        job.update(state="error", error=str(e)[:600])


def _energy(seg: Path) -> dict:
    """Loudness every 50 ms, so trimming can tell dead air from crowd noise and action."""
    import subprocess

    import numpy as np

    from .util import clean_env
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(seg), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                       capture_output=True, env=clean_env(), timeout=300)
    x = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768
    hop = 400
    n = len(x) // hop
    rms = np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(axis=1)) if n else np.zeros(1)
    return {"hop": .05, "rms": [round(float(v), 5) for v in rms]}


def _splice(words: list[dict], rough: list[dict], start: float, end: float, min_gap: float = 2.0) -> list[dict]:
    """Fill holes where the precise pass heard nothing but YouTube's captions did (speech over music)."""
    if not rough:
        return words
    edges = [start] + [x for w in words for x in (w["s"], w["e"])] + [end]
    gaps = [(edges[i], edges[i + 1]) for i in range(0, len(edges) - 1, 2) if edges[i + 1] - edges[i] >= min_gap]
    added = [dict(r, src="captions") for r in rough for g0, g1 in gaps
             if g0 + .1 <= (r["s"] + r["e"]) / 2 <= g1 - .1 and start <= r["s"] <= end]
    return sorted(words + added, key=lambda w: w["s"]) if added else words


def fetch_clip(job: Job, cid: str, info: dict) -> None:
    c = job.clip(cid)
    d = job.dir / cid
    d.mkdir(exist_ok=True)
    src = job.status()["source"]
    pad = 3.0
    sec_s = max(0.0, c["start"] - pad)
    sec_e = min(info.get("duration") or c["end"] + pad, c["end"] + pad)
    job.patch_clip(cid, state="fetching", sec_s=round(sec_s, 3),
                   local_start=round(c["start"] - sec_s, 3), local_end=round(c["end"] - sec_s, 3))
    seg = d / "source.mp4"
    if ingest.is_url(src):
        ingest.download_section(src, sec_s, sec_e, seg)
    else:
        ingest.cut_local(Path(src), sec_s, sec_e, seg)


def analyze_clip(job: Job, cid: str, cfg: dict) -> None:
    c = job.clip(cid)
    d = job.dir / cid
    seg = d / "source.mp4"
    if not (d / "words.json").exists():
        job.patch_clip(cid, state="transcribing")
        from .transcribe import transcribe_words
        w = cfg["whisper"]
        title = (read_json(job.dir / "info.json", {}) or {}).get("title", "")
        words = transcribe_words(seg, w["model"], None if cfg["language"] == "auto" else cfg["language"],
                                 w.get("device", "auto"), w.get("vad", False), prompt=title)
        rough = read_json(job.dir / "rough_words.json", []) or []
        off = c.get("sec_s", 0.0)
        rough_local = [{"w": r["w"], "s": round(r["s"] - off, 3), "e": round(r["e"] - off, 3)} for r in rough
                       if c["start"] - 1 <= r["s"] <= c["end"] + 1]
        write_json(d / "words.json", _splice(words, rough_local, c["local_start"], c["local_end"]))
    if not (d / "energy.json").exists():
        write_json(d / "energy.json", _energy(seg))
    lay = read_json(d / "layout.json")
    if not lay or lay.get("version") != 2:          # re-frame clips made by the old per-shot framer
        job.patch_clip(cid, state="framing")
        write_json(d / "layout.json", reframe.analyze(seg, cfg.get("reframe")))


def prepare_clip(job: Job, cid: str, cfg: dict, info: dict) -> None:
    fetch_clip(job, cid, info)
    analyze_clip(job, cid, cfg)


def render_clip(job: Job, cid: str, cfg: dict | None = None) -> dict:
    cfg = cfg or _cfg(job)
    c = job.clip(cid)
    d = job.dir / cid
    words = read_json(d / "words.json", [])
    analysis = read_json(d / "layout.json")
    if not analysis or analysis.get("version") != 2:   # clips framed by the old per-shot framer
        analysis = reframe.analyze(d / "source.mp4", cfg.get("reframe"))
        write_json(d / "layout.json", analysis)
    ov = c.get("overrides") or {}
    job.patch_clip(cid, state="rendering", error=None)
    energy = read_json(d / "energy.json")
    if energy and energy.get("rms"):
        import statistics
        hop = energy["hop"]
        speech = [energy["rms"][int(w["s"] / hop)] for w in words if int(w["s"] / hop) < len(energy["rms"])]
        energy["speech"] = statistics.median(speech) if speech else max(energy["rms"])
    plan = edit.plan(words, c["local_start"], c["local_end"], cfg,
                     cold_open=c.get("cold_open") if ov.get("cold_open", True) else None,
                     removed=set(ov.get("removed") or []), keywords=c.get("keywords"),
                     energy=energy, trim=ov.get("trim"))
    for w in plan["words"]:                      # fix misheard names, e.g. Holland -> Haaland
        for wrong, right in (ov.get("replace") or {}).items():
            core = w["w"].strip(".,!?;:\"'")
            if core.lower() == wrong.lower():
                w["w"] = w["w"].replace(core, right)
    st = cfg["style"]
    ass = captions.build_ass(plan["words"], plan["duration"], ov.get("preset") or st["preset"],
                             c.get("hook_title", "") if st["hook_title"] else "", st["hook_seconds"],
                             st.get("watermark", ""), size=ASPECTS.get(cfg["render"].get("aspect"), ASPECTS["9:16"]))
    write_json(d / "edit.json", plan)
    if ov.get("framing"):
        analysis = {**analysis, "directions": ov["framing"]}     # the agent's camera directions
    render.render(d / "source.mp4", plan, analysis, ass, d / f"{cid}.mp4", cfg)
    modes = sorted({s.get("mode") for s in analysis.get("shots", [])})
    return job.patch_clip(cid, state="ready", duration=plan["duration"], layout=modes,
                          rendered=time.time(), video=f"{cid}/{cid}.mp4", cover=f"{cid}/{cid}.jpg")
