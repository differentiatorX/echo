"""One ffmpeg pass per clip: per-piece seek + crop/zoom, concat, captions, progress bar,
logo, sound effects, ducked background music, loudness normalization."""
from __future__ import annotations

import random
import shutil
from functools import lru_cache
from pathlib import Path

from .config import ASPECTS, FONTS, HOME
from .reframe import layout_at
from .util import EchoError, run

AUDIO_EXT = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".flac"}


def _even(x: float) -> int:
    return max(2, int(round(x / 2)) * 2)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _vchain(i: int, lay: dict, W: int, H: int, z: float, fps: int, accent: str, OW: int, OH: int) -> str:
    src = f"[{i}:v]"
    tail = f"setsar=1,fps={fps},format=yuv420p[v{i}]"
    mode = lay.get("mode", "blur")
    ar = OW / OH
    if mode == "crop":
        cw = min(W, H * ar) / z
        ch = cw / ar
        if ch > H:
            ch, cw = H, H * ar
        x = _clamp(lay["x"] * W - cw / 2, 0, W - cw)
        y = _clamp(lay["y"] * H - ch * 0.40, 0, H - ch)
        return f"{src}crop={_even(cw)}:{_even(ch)}:{_even(x)}:{_even(y)},scale={OW}:{OH}:flags=lanczos,{tail}"
    if mode == "split":
        half = OH // 2
        har = OW / half
        ch = H * 0.9 / z
        cw = ch * har
        if cw > W * 0.6:
            cw = W * 0.6
            ch = cw / har
        parts = []
        for tag, (fx, fy) in (("a", lay["a"]), ("b", lay["b"])):
            x = _clamp(fx * W - cw / 2, 0, W - cw)
            y = _clamp(fy * H - ch * 0.45, 0, H - ch)
            parts.append(f"[s{i}{tag}]crop={_even(cw)}:{_even(ch)}:{_even(x)}:{_even(y)},"
                         f"scale={OW}:{half}:flags=lanczos[t{i}{tag}]")
        col = "0x" + accent.lstrip("#")
        return (f"{src}split=2[s{i}a][s{i}b];" + ";".join(parts) +
                f";[t{i}a][t{i}b]vstack=2,drawbox=x=0:y={half - 3}:w={OW}:h=6:color={col}:t=fill,{tail}")
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
    except EchoError:
        return False


def sfx_files() -> dict:
    """Procedural sound effects, generated once. No samples, no licensing questions."""
    d = HOME / "sfx"
    d.mkdir(parents=True, exist_ok=True)
    recipes = {
        "whoosh": "anoisesrc=color=pink:d=0.5:a=0.9,highpass=f=350,lowpass=f=7000,"
                  "afade=t=in:d=0.22:curve=exp,afade=t=out:st=0.22:d=0.28,volume=0.9",
        "pop": "sine=f=1250:d=0.09,afade=t=out:st=0.0:d=0.09:curve=exp,volume=0.6",
    }
    out = {}
    for name, graph in recipes.items():
        f = d / f"{name}.wav"
        if not f.exists():
            run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", graph, "-ar", "48000", "-ac", "2", str(f)],
                timeout=30)
        out[name] = f
    return out


def pick_music(spec: str) -> Path | None:
    if not spec:
        return None
    p = Path(spec).expanduser()
    if p.is_file() and p.suffix.lower() in AUDIO_EXT:
        return p
    if p.is_dir():
        files = sorted(f for f in p.iterdir() if f.suffix.lower() in AUDIO_EXT)
        return random.choice(files) if files else None
    return None


def sfx_events(edit: dict) -> list[tuple[str, float]]:
    ev: list[tuple[str, float]] = []
    pieces = edit["pieces"]
    if edit.get("cold_open"):
        body = next((p for p in pieces if p["group"] == "body"), None)
        if body:
            ev.append(("whoosh", max(0.0, body["out_s"] - 0.22)))
    pops = [w["s"] for w in edit["words"] if w.get("kw")][:4]
    ev += [("pop", t) for t in pops]
    return ev


def render(seg: Path, edit: dict, analysis: dict, ass_text: str, out: Path, cfg: dict) -> Path:
    r, st = cfg["render"], cfg["style"]
    OW, OH = ASPECTS.get(r.get("aspect", "9:16"), ASPECTS["9:16"])
    accent = "#1E7BFF"
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
        graph.append(_vchain(i, lay, W, H, p["zoom"], r["fps"], accent, OW, OH))
        a = f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
        if p["cut_in"]:
            a += ",afade=t=in:d=0.012"
        if p["cut_out"]:
            a += f",afade=t=out:st={max(0.0, dur - 0.015):.3f}:d=0.015"
        graph.append(a + f",apad=whole_dur={dur:.3f},atrim=0:{dur:.3f}[a{i}]")
    n = len(pieces)
    nxt = n
    graph.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")

    # ---- video overlays
    vlast = "[cv]"
    logo = Path(st.get("logo") or "").expanduser()
    if st.get("logo") and logo.is_file():
        cmd += ["-i", str(logo.resolve())]
        lw = _even(OW * 0.11)
        graph.append(f"[{nxt}:v]scale={lw}:-2,format=rgba,colorchannelmixer=aa=0.92[lg]")
        graph.append(f"{vlast}[lg]overlay=x=W-w-{int(OW * 0.05)}:y={int(OH * 0.11)}[lv]")
        vlast = "[lv]"
        nxt += 1
    graph.append(f"{vlast}subtitles=filename={ass_name}:fontsdir=fonts[sv]")
    vlast = "[sv]"
    if st.get("progress_bar"):
        graph.append(f"color=c=0x48B4F0:s={OW}x10:r={r['fps']}[pb]")
        graph.append(f"{vlast}[pb]overlay=x='-w+W*t/{D:.3f}':y=H-10:shortest=1[pv]")
        vlast = "[pv]"

    # ---- audio: voice, ducked music, sfx
    alast = "[ca]"
    music = pick_music(st.get("music", ""))
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music.resolve())]
        mv = float(st.get("music_volume", 0.18))
        graph.append(f"{alast}asplit=2[vox][key]")
        graph.append(f"[{nxt}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
                     f"volume={mv:.3f},atrim=0:{D:.3f}[mraw]")
        graph.append("[mraw][key]sidechaincompress=threshold=0.02:ratio=9:attack=15:release=350[mduck]")
        graph.append("[vox][mduck]amix=inputs=2:duration=first:normalize=0[amix1]")
        alast = "[amix1]"
        nxt += 1
    if st.get("sfx"):
        events = sfx_events(edit)
        if events:
            files = sfx_files()
            vol = float(st.get("sfx_volume", 0.35))
            labels = []
            for k, (name, t) in enumerate(events):
                cmd += ["-i", str(files[name])]
                ms = int(t * 1000)
                v = vol * (1.0 if name == "whoosh" else 0.6)
                graph.append(f"[{nxt}:a]volume={v:.3f},adelay={ms}|{ms}[fx{k}]")
                labels.append(f"[fx{k}]")
                nxt += 1
            graph.append(f"{alast}{''.join(labels)}amix=inputs={len(labels) + 1}:duration=first:normalize=0[amix2]")
            alast = "[amix2]"
    graph.append(f"{alast}loudnorm=I=-14:TP=-1.0:LRA=11,aresample=48000[ao]")

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


def cover(video: Path, hook: str, out: Path, size: tuple[int, int]) -> Path:
    """A grid-safe cover: the hook in big type in the centre band, where Instagram's
    profile grid crop keeps it visible."""
    from .captions import _esc, _wrap, ass_color
    W, H = size
    work = video.parent
    fonts = work / "fonts"
    if not fonts.exists():
        shutil.copytree(FONTS, fonts)
    text = _wrap(_esc((hook or "").upper()), 14)
    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cover,Anton,{int(W * 0.13)},{ass_color('#FFFFFF')},{ass_color('#FFFFFF')},{ass_color('#1673D6')},{ass_color('#000000', 0x40)},0,0,0,0,100,100,1,0,3,{int(W * 0.022)},0,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:10.00,Cover,,0,0,0,,{{\\an5\\pos({W // 2},{H // 2})}}{text}
"""
    (work / "cover.ass").write_text(ass, "utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-ss", "0.8", "-i", video.name, "-frames:v", "1",
         "-vf", "gblur=sigma=16,eq=brightness=-0.14:saturation=1.1,subtitles=filename=cover.ass:fontsdir=fonts", "-q:v", "2", out.name],
        cwd=work, timeout=60)
    return out
