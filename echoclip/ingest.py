"""Everything that touches the source: metadata, replay heatmap, comments,
rough captions, and full-quality downloads of only the chosen ranges."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .util import EchoError, probe, run, ytdlp

FILLERS = {"um", "uh", "uhm", "umm", "erm", "er", "ah", "hmm", "mm", "mhm"}


def is_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def fetch_info(url: str) -> dict:
    raw = json.loads(run(ytdlp() + ["-J", "--no-playlist", "--skip-download", url], timeout=180))
    return {
        "id": raw.get("id"),
        "title": raw.get("title") or "Untitled",
        "channel": raw.get("channel") or raw.get("uploader") or "",
        "duration": float(raw.get("duration") or 0),
        "url": raw.get("webpage_url") or url,
        "thumbnail": raw.get("thumbnail"),
        "language": raw.get("language"),
        "heatmap": raw.get("heatmap") or [],
        "chapters": [{"s": c.get("start_time", 0), "e": c.get("end_time", 0), "title": c.get("title", "")}
                     for c in (raw.get("chapters") or [])],
    }


def fetch_comments(url: str, max_comments: int = 400) -> list[dict]:
    """Top comments. Only the ones that mention a timestamp matter to us."""
    args = f"youtube:comment_sort=top;max_comments={max_comments},{max_comments},0,0"
    raw = json.loads(run(ytdlp() + ["-J", "--no-playlist", "--skip-download", "--write-comments",
                                    "--extractor-args", args, url], timeout=240))
    return [{"text": c.get("text", ""), "likes": int(c.get("like_count") or 0)}
            for c in (raw.get("comments") or [])]


# ---------- captions ----------

def _json3_words(data: dict) -> list[dict]:
    words: list[dict] = []
    for ev in data.get("events", []):
        segs = ev.get("segs")
        if not segs or "tStartMs" not in ev:
            continue
        t0 = ev["tStartMs"] / 1000
        dur = (ev.get("dDurationMs") or 0) / 1000
        timed = [(t0 + s.get("tOffsetMs", 0) / 1000, s.get("utf8", "")) for s in segs]
        for i, (t, text) in enumerate(timed):
            toks = text.replace("\n", " ").split()
            if not toks:
                continue
            nxt = timed[i + 1][0] if i + 1 < len(timed) else t0 + max(dur, 0.3)
            span = max(nxt - t, 0.15 * len(toks))
            for j, tok in enumerate(toks):
                s = t + span * j / len(toks)
                words.append({"w": tok, "s": round(s, 3), "e": round(s + span / len(toks), 3)})
    words.sort(key=lambda w: w["s"])
    for a, b in zip(words, words[1:]):  # rolling auto-captions overlap; clip ends to the next start
        a["e"] = round(min(a["e"], b["s"]) if b["s"] > a["s"] else a["e"], 3)
    dedup = []
    for w in words:
        if dedup and w["w"] == dedup[-1]["w"] and abs(w["s"] - dedup[-1]["s"]) < 0.05:
            continue
        dedup.append(w)
    return dedup


def fetch_captions(url: str, workdir: Path, lang: str = "en") -> list[dict]:
    """YouTube captions as rough words. Seconds instead of minutes of transcription."""
    subdir = workdir / "subs"
    subdir.mkdir(parents=True, exist_ok=True)
    try:
        run(ytdlp() + ["--skip-download", "--no-playlist", "--write-subs", "--write-auto-subs",
                       "--sub-langs", f"{lang}-orig,{lang},{lang}.*", "--sub-format", "json3",
                       "-o", str(subdir / "%(id)s.%(ext)s"), url], timeout=120)
    except EchoError:
        return []
    files = sorted(subdir.glob("*.json3"))
    if not files:
        return []
    files.sort(key=lambda p: (0 if f".{lang}-orig." in p.name else 1 if f".{lang}." in p.name else 2))
    return _json3_words(json.loads(files[0].read_text("utf-8")))


def words_to_lines(words: list[dict], max_words: int = 28, gap: float = 0.7) -> list[dict]:
    lines, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end = (re.search(r"[.?!]$", w["w"]) or len(cur) >= max_words or nxt is None
               or nxt["s"] - w["e"] > gap)
        if end:
            lines.append({"s": cur[0]["s"], "e": cur[-1]["e"], "text": " ".join(x["w"] for x in cur)})
            cur = []
    return lines


# ---------- media ----------

def download_section(url: str, start: float, end: float, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0 and probe(out)["w"] > 0:
        return out
    formats = ["bv*[height<=1080][ext=mp4]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
               "bv*+ba/b"]
    last = ""
    for fmt in formats:
        for f in out.parent.glob(out.stem + "*"):
            f.unlink(missing_ok=True)
        try:
            run(ytdlp() + ["--no-playlist", "-N", "4", "-f", fmt, "--merge-output-format", "mp4",
                           "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
                           "-o", str(out), url], timeout=900)
        except EchoError as e:
            last = str(e)
            continue
        if not out.exists():
            cands = sorted(out.parent.glob(out.stem + "*"))
            if cands:
                cands[0].rename(out)
        if out.exists() and probe(out)["w"] > 0:
            return out
        last = "YouTube returned audio only"
    raise EchoError(f"Couldn't download video for {start:.0f}-{end:.0f}s ({last[-200:]}). YouTube needs a "
                    "JavaScript runtime: install Node.js (winget install OpenJS.NodeJS.LTS) and update yt-dlp "
                    "(pip install -U \"yt-dlp[default]\").")


def cut_local(src: Path, start: float, end: float, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0:
        return out
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "192k",
         str(out)], timeout=900)
    return out


def local_info(path: Path) -> dict:
    p = probe(path)
    return {"id": None, "title": path.stem, "channel": "", "duration": p["dur"], "url": str(path),
            "thumbnail": None, "language": None, "heatmap": [], "chapters": []}
