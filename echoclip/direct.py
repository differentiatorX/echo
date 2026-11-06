"""The agent as camera director.

`shots` shows the agent every shot in a clip: three frames each with position guides, where the
automatic framing would put the crop, and what is being said. The agent studies the scene and
sends one direction per shot; `direct` validates them and re-renders.

A direction:
  {"shot": 2, "layout": "fill" | "wide" | "split",
   "focus": [x, y],            where the subject is, 0..1 of the source frame (x left->right, y top->bottom)
   "follow": false,            fill only: track the moving subject instead of holding on focus
   "zoom": 1.0,                fill: 1.0 = full-height vertical crop, up to 2.2 tighter
                               wide: 1.0 = whole frame, up to 1.8 crops into the scene
   "moves": [{"type": "push", "to": 1.25, "start": 12.4, "end": 15.0},     slow push-in or pull-out
             {"type": "punch", "to": 1.45, "at": 13.2, "hold": 1.2}],      quick punch-in on a moment
   "why": "Gordon reacts; push in on his face for the punchline"}
Times are the clip's source seconds, as shown by `shots`.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import FONTS
from .util import EchoError, clean_env, read_json, write_json

LAYOUTS = {"fill", "wide", "split"}
FW, FH = 384, 216


def _font(size):
    try:
        return ImageFont.truetype(str(FONTS / "Anton-Regular.ttf"), size)
    except OSError:
        return ImageFont.load_default()


def _grab(seg: Path, t: float) -> Image.Image:
    p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0, t):.3f}", "-i", str(seg), "-frames:v", "1",
                        "-vf", f"scale={FW}:{FH}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, env=clean_env(), timeout=60)
    if len(p.stdout) < FW * FH * 3:
        return Image.new("RGB", (FW, FH), (20, 20, 30))
    return Image.frombytes("RGB", (FW, FH), p.stdout[: FW * FH * 3])


def _guides(im: Image.Image, crop: tuple | None):
    d = ImageDraw.Draw(im)
    f = _font(13)
    for gx in (.25, .5, .75):
        x = int(gx * FW)
        d.line([(x, 0), (x, FH)], fill=(255, 255, 255), width=1)
        d.text((x + 3, 2), f"{gx:.2f}", fill=(255, 255, 255), font=f)
    for gy in (.33, .66):
        y = int(gy * FH)
        d.line([(0, y), (FW, y)], fill=(160, 160, 160), width=1)
        d.text((3, y + 2), f"y {gy:.2f}", fill=(200, 200, 200), font=f)
    if crop:
        x0, x1 = int(crop[0] * FW), int(crop[1] * FW)
        d.rectangle([x0, 1, x1, FH - 2], outline=(255, 215, 0), width=3)


def shots(job, cid: str) -> dict:
    c = job.clip(cid)
    d = job.dir / cid
    seg = d / "source.mp4"
    lay = read_json(d / "layout.json")
    if not seg.exists() or not lay:
        raise EchoError("This clip hasn't been fetched and framed yet. Run finish first.")
    words = read_json(d / "words.json", []) or []
    ls, le = c.get("local_start", 0.0), c.get("local_end", 0.0)
    W, H = lay["w"], lay["h"]
    frac = min(1.0, (H * 9 / 16) / W)
    info, rows = [], []
    for i, sh in enumerate(lay["shots"]):
        s, e = max(sh["s"], ls - .3), min(sh["e"], le + .3)
        if e - s < .25:
            continue
        said = " ".join(w["w"] for w in words if s <= w["s"] < e)
        auto = {"layout": "fill" if sh["mode"] == "crop" else ("wide" if sh["mode"] in ("fit", "blur") else sh["mode"])}
        crop = None
        if sh["mode"] == "crop":
            tr = sh.get("track") or [[s, .5]]
            mid = [x for t, x in tr if t <= (s + e) / 2] or [tr[0][1]]
            cx = mid[-1]
            auto["focus"] = [round(cx, 3), round(sh.get("y", .42), 3)]
            crop = (max(0, cx - frac / 2), min(1, cx + frac / 2))
        info.append({"shot": i, "start": round(s, 2), "end": round(e, 2), "seconds": round(e - s, 1),
                     "auto": auto, "said": said})
        frames = []
        for k, t in enumerate((s + .15, (s + e) / 2, e - .15)):
            im = _grab(seg, t)
            _guides(im, crop if k == 1 else None)
            frames.append(im)
        rows.append((i, s, e, said, auto, frames))
    pages = []
    per = 5
    for p0 in range(0, len(rows), per):
        chunk = rows[p0:p0 + per]
        sheet = Image.new("RGB", (FW * 3 + 30, len(chunk) * (FH + 54) + 10), (12, 14, 24))
        dr = ImageDraw.Draw(sheet)
        for r, (i, s, e, said, auto, frames) in enumerate(chunk):
            y = 10 + r * (FH + 54)
            label = f"SHOT {i}   {s:.1f}s to {e:.1f}s   auto: {auto['layout']}"
            if "focus" in auto:
                label += f" at x {auto['focus'][0]:.2f} (gold box = auto crop)"
            dr.text((10, y), label, fill=(244, 211, 94), font=_font(18))
            dr.text((10, y + 24), (said[:150] + ("..." if len(said) > 150 else "")) or "(no speech)", fill=(220, 225, 240), font=_font(14))
            for k, im in enumerate(frames):
                sheet.paste(im, (10 + k * (FW + 5), y + 46))
        out = d / f"shots_{p0 // per + 1}.jpg"
        sheet.save(out, quality=85)
        pages.append(str(out))
    return {"clip": cid, "frame": f"{W}x{H}", "vertical_crop_width": round(frac, 3),
            "contact_sheets": pages, "shots": info,
            "next": "Look at every contact sheet, then send one direction per shot with `direct`."}


def _validate(d: dict, n_shots: int) -> dict:
    if not isinstance(d, dict) or "shot" not in d:
        raise EchoError("Each direction needs a \"shot\" number from `shots`.")
    i = int(d["shot"])
    if not 0 <= i < n_shots:
        raise EchoError(f"Shot {i} doesn't exist in this clip.")
    layout = d.get("layout", "fill")
    if layout not in LAYOUTS:
        raise EchoError(f"Layout must be one of {sorted(LAYOUTS)}.")
    focus = d.get("focus") or [.5, .42]
    fx, fy = float(focus[0]), float(focus[1])
    zoom = float(d.get("zoom", 1.0))
    zmax = 2.2 if layout == "fill" else 1.8
    moves = []
    for m in (d.get("moves") or [])[:4]:
        kind = m.get("type")
        if kind == "push":
            moves.append({"type": "push", "to": min(zmax, max(1.0, float(m["to"]))),
                          "start": float(m["start"]), "end": max(float(m["start"]) + .3, float(m["end"]))})
        elif kind == "punch":
            moves.append({"type": "punch", "to": min(zmax, max(1.0, float(m["to"]))),
                          "at": float(m["at"]), "hold": min(4.0, max(.3, float(m.get("hold", 1.0))))})
        else:
            raise EchoError("A move's type must be push or punch.")
    return {"shot": i, "layout": layout, "focus": [min(1, max(0, fx)), min(1, max(0, fy))],
            "follow": bool(d.get("follow", False)), "zoom": min(zmax, max(1.0, zoom)),
            "moves": sorted(moves, key=lambda m: m.get("start", m.get("at", 0))), "why": str(d.get("why", ""))[:200]}


def direct(job, cid: str, directions: list[dict], render: bool = True) -> dict:
    lay = read_json(job.dir / cid / "layout.json")
    if not lay:
        raise EchoError("Run finish first, then `shots`, then `direct`.")
    clean = {}
    for d in directions:
        v = _validate(d, len(lay["shots"]))
        clean[str(v["shot"])] = v
    c = job.clip(cid)
    ov = dict(c.get("overrides") or {})
    ov["framing"] = {**(ov.get("framing") or {}), **clean}
    job.patch_clip(cid, overrides=ov)
    if render:
        from .pipeline import render_clip
        render_clip(job, cid)
    return {"clip": cid, "directed_shots": sorted(int(k) for k in ov["framing"]),
            "video": str(job.dir / cid / f"{cid}.mp4"), "next": "Check frames again; adjust any shot that still looks wrong."}


def clear(job, cid: str) -> None:
    c = job.clip(cid)
    ov = dict(c.get("overrides") or {})
    ov.pop("framing", None)
    job.patch_clip(cid, overrides=ov)
