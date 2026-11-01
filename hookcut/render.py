"""One ffmpeg pass per clip: per-piece seek + crop/zoom, concat, captions, progress bar, loudnorm."""
from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from .config import FONTS
from .reframe import layout_at
from .util import HookcutError, run

OW, OH = 1080, 1920


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _vchain(i: int, lay: dict, W: int, H: int, z: float, fps: int, accent: str) -> str:
    src = f"[{i}:v]"
    tail = f"setsar=1,fps={fps},format=yuv420p[v{i}]"
    mode = lay.get("mode", "blur")
    if mode == "crop":
        cw = min(W, H * 9 / 16) / z
        ch = cw * 16 / 9
        if ch > H:
            ch, cw = H, H * 9 / 16
        x = _clamp(lay["x"] * W - cw / 2, 0, W - cw)
        y = _clamp(lay["y"] * H - ch * 0.40, 0, H - ch)
        return f"{src}crop={_even(cw)}:{_even(ch)}:{_even(x)}:{_even(y)},scale={OW}:{OH}:flags=lanczos,{tail}"
    if mode == "split":
        ch = H * 0.9 / z
        cw = ch * 9 / 8
        if cw > W * 0.6:
            cw = W * 0.6
            ch = cw * 8 / 9
        parts = []
        for tag, (fx, fy) in (("a", lay["a"]), ("b", lay["b"])):
            x = _clamp(fx * W - cw / 2, 0, W - cw)
            y = _clamp(fy * H - ch * 0.45, 0, H - ch)
            parts.append(f"[s{i}{tag}]crop={_even(cw)}:{_even(ch)}:{_even(x)}:{_even(y)},"
                         f"scale={OW}:{OH // 2}:flags=lanczos[t{i}{tag}]")
        col = "0x" + accent.lstrip("#")
        return (f"{src}split=2[s{i}a][s{i}b];" + ";".join(parts) +
                f";[t{i}a][t{i}b]vstack=2,drawbox=x=0:y={OH // 2 - 3}:w={OW}:h=6:color={col}:t=fill,{tail}")
    # blur / fit
    fw, fh = _even(OW * z), _even(OH * z)
    return (f"{src}split=2[b{i}][f{i}];"
            f"[b{i}]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
            f"boxblur=28:2,eq=brightness=-0.10:saturation=1.15[bg{i}];"
            f"[f{i}]scale={fw}:{fh}:force_original_aspect_ratio=decrease:force_divisible_by=2[fg{i}];"
            f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,{tail}")


@lru_cache(maxsize=1)
def _has_nvenc() -> bool:
    try:
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"], timeout=20)
        return True
    except HookcutError:
        return False


def render(seg: Path, edit: dict, analysis: dict, ass_text: str, out: Path, cfg: dict) -> Path:
    r, st = cfg["render"], cfg["style"]
    accent = "#2F6BFF"
    W, H = analysis["w"], analysis["h"]
    pieces = edit["pieces"]
    D = edit["duration"]
    work = out.parent
    work.mkdir(parents=True, exist_ok=True)
    fonts = work / "fonts"
    if not fonts.exists():
        shutil.copytree(FONTS, fonts)
    ass_name = out.stem + ".ass"
    (work / ass_name).write_text(ass_text, "utf-8")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-v", "error"]
    graph = []
    for i, p in enumerate(pieces):
        dur = p["src_e"] - p["src_s"]
        cmd += ["-ss", f"{p['src_s']:.3f}", "-t", f"{dur:.3f}", "-i", str(seg.resolve())]
        lay = layout_at(analysis, (p["src_s"] + p["src_e"]) / 2)
        graph.append(_vchain(i, lay, W, H, p["zoom"], r["fps"], accent))
        a = f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if p["cut_in"]:
            a += ",afade=t=in:d=0.012"
        if p["cut_out"]:
            a += f",afade=t=out:st={max(0.0, dur - 0.015):.3f}:d=0.015"
        graph.append(a + f",apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}[a{i}]")
    n = len(pieces)
    graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")
    vlast = "[cv]"
    graph.append(f"{vlast}subtitles=filename={ass_name}:fontsdir=fonts[sv]")
    vlast = "[sv]"
    if st.get("progress_bar"):
        graph.append(f"color=c=0x2F6BFF:s={OW}x10:r={r['fps']}[pb]")
        graph.append(f"{vlast}[pb]overlay=x='-w+W*t/{D:.3f}':y=H-10:shortest=1[pv]")
        vlast = "[pv]"
    graph.append("[ca]loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000[ao]")

    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(r["crf"]), "-profile:v", "high"]
    if r.get("encoder") in ("auto", "nvenc") and _has_nvenc():
        enc = ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", str(r["crf"] + 1), "-b:v", "0"]
    cmd += ["-filter_complex", ";".join(graph), "-map", vlast, "-map", "[ao]", *enc,
            "-pix_fmt", "yuv420p", "-r", str(r["fps"]), "-c:a", "aac", "-b:a", "192k",
            "-t", f"{D:.3f}", "-movflags", "+faststart", out.name]
    run(cmd, cwd=work, timeout=1800)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{min(1.2, D / 2):.2f}", "-i", out.name,
         "-frames:v", "1", "-q:v", "3", out.with_suffix(".jpg").name], cwd=work, timeout=60)
    return out
