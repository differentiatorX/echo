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


def _xexpr(track, t0, t1, W, cw):
    """Piecewise-linear crop x over the piece's local time, from the camera path keyframes."""
    pts = [(t - t0, x) for t, x in (track or []) if t0 - 1.0 <= t <= t1 + 1.0]
    if not pts:
        before = [x for t, x in (track or []) if t < t0]
        pts = [(0.0, before[-1] if before else (track[0][1] if track else .5))]
    px = [(round(max(0.0, t), 3), _clamp(x * W - cw / 2, 0, W - cw)) for t, x in pts]
    if len(px) > 16:                                   # keep the expression short
        step = len(px) / 16
        px = [px[int(i * step)] for i in range(16)] + [px[-1]]
    if max(v for _, v in px) - min(v for _, v in px) < 2:
        return str(_even(px[0][1]))
    expr = f"{px[-1][1]:.1f}"
    for (ta, xa), (tb, xb) in reversed(list(zip(px, px[1:]))):
        if tb - ta < 1e-3:
            continue
        expr = f"if(lt(t,{tb:.3f}),{xa:.1f}+({xb - xa:.1f})*(t-{ta:.3f})/{tb - ta:.3f},{expr})"
    return f"if(lt(t,{px[0][0]:.3f}),{px[0][1]:.1f},{expr})"


def _vchain(i: int, lay: dict, W: int, H: int, z: float, fps: int, accent: str, OW: int, OH: int,
            t0: float = 0.0, t1: float = 0.0) -> str:
    src = f"[{i}:v]"
    tail = f"setsar=1,fps={fps},format=yuv420p[v{i}]"
    mode = lay.get("mode", "blur")
    ar = OW / OH
    if mode == "crop":
        cw = min(W, H * ar) / z
        ch = cw / ar
        if ch > H:
            ch, cw = H, H * ar
        y = _clamp(lay.get("y", .42) * H - ch * 0.40, 0, H - ch)
        track = lay.get("track") or [[t0, lay.get("x", .5)]]
        x = _xexpr(track, t0, t1, W, cw)
        return (f"{src}crop=w={_even(cw)}:h={_even(ch)}:x='{x}':y={_even(y)},"
                f"scale={OW}:{OH}:flags=lanczos,{tail}")
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


def _directed(i: int, dv: dict, lay: dict, W: int, H: int, fps: int, accent: str, OW: int, OH: int,
              t0: float, t1: float):
    """Frame one piece as the agent directed its shot. Returns the filter chain and where the
    subject sits in the output frame (0..1), which anchors any zoom move."""
    src = f"[{i}:v]"
    tail = f"setsar=1,fps={fps},format=yuv420p[v{i}]"
    fx, fy = dv["focus"]
    if dv["layout"] == "split" and lay.get("mode") == "split":
        return _vchain(i, lay, W, H, 1.0, fps, accent, OW, OH, t0, t1), (.5, .5)
    if dv["layout"] == "wide":
        z = dv["zoom"]
        cw, ch = W / z, H / z
        x = _clamp(fx * W - cw / 2, 0, W - cw)
        y = _clamp(fy * H - ch / 2, 0, H - ch)
        band = OW * ch / cw
        focus = ((fx * W - x) / cw, .5 + ((fy * H - y) / ch - .5) * band / OH)
        return (f"{src}split=2[b{i}][f{i}];"
                f"[b{i}]scale={OW}:{OH}:force_original_aspect_ratio=increase,crop={OW}:{OH},"
                f"boxblur=28:2,eq=brightness=-0.12:saturation=1.15[bg{i}];"
                f"[f{i}]crop={_even(cw)}:{_even(ch)}:{_even(x)}:{_even(y)},"
                f"scale={OW}:-2:flags=lanczos[fg{i}];"
                f"[bg{i}][fg{i}]overlay=(W-w)/2:(H-h)/2,{tail}"), focus
    ar = OW / OH
    z = dv["zoom"]
    cw = min(W, H * ar) / z
    ch = cw / ar
    follow = dv.get("follow") and lay.get("mode") == "crop" and lay.get("track")
    track = lay["track"] if follow else [[t0, fx]]
    x = _xexpr(track, t0, t1, W, cw)
    y = _clamp(fy * H - ch * .40, 0, H - ch)
    if follow:
        fxo = .5
    else:
        x0 = _clamp(fx * W - cw / 2, 0, W - cw)
        fxo = (fx * W - x0) / cw
    fyo = (fy * H - y) / ch
    return (f"{src}crop=w={_even(cw)}:h={_even(ch)}:x='{x}':y={_even(y)},"
            f"scale={OW}:{OH}:flags=lanczos,{tail}"), (fxo, fyo)


def _to_out(t: float, pieces: list[dict]) -> float:
    """Map a source time to the edited timeline (a time inside a removed pause snaps to the next kept piece)."""
    for p in pieces:
        if p["src_s"] <= t < p["src_e"]:
            return p["out_s"] + (t - p["src_s"])
        if t < p["src_s"]:
            return p["out_s"]
    return pieces[-1]["out_s"] + pieces[-1]["src_e"] - pieces[-1]["src_s"]


def _zoom_pass(moves: list[tuple], focus: list[tuple], fps: int, OW: int, OH: int) -> str:
    """One smooth zoom over the edited clip. moves: (out_start, out_end, kind, rel_from, rel_to, hold).
    focus: (out_start, out_end, fx, fy) per piece, so each zoom is anchored on that shot's subject."""
    T = f"(on/{fps})"
    z = "1"
    for a, b, kind, r0, r1, hold in sorted(moves, key=lambda m: m[0]):
        if kind == "push":                       # hold = the shot's end: zoom returns to normal on the next shot
            z = (f"if(lt({T},{a:.3f}),{z},if(lt({T},{b:.3f}),{r0:.3f}+({r1 - r0:.3f})*({T}-{a:.3f})/{b - a:.3f},"
                 f"if(lt({T},{hold:.3f}),{r1:.3f},{z})))")
        else:
            e = .18
            z = (f"if(lt({T},{a - e:.3f}),{z},if(lt({T},{a + hold:.3f}),{r0:.3f}+({r1 - r0:.3f})*min(1,({T}-{a - e:.3f})/{e}),"
                 f"if(lt({T},{a + hold + e:.3f}),{r1:.3f}+({r0 - r1:.3f})*min(1,({T}-{a + hold:.3f})/{e}),{z})))")
    fxe, fye = ".5", ".42"
    for s0, s1, fx, fy in reversed(focus):
        fxe = f"if(lt({T},{s1:.3f}),{fx:.3f},{fxe})"
        fye = f"if(lt({T},{s1:.3f}),{fy:.3f},{fye})"
    return (f"scale={OW * 2}:{OH * 2}:flags=lanczos,"
            f"zoompan=z='{z}':x='({fxe})*iw*(1-1/zoom)':y='({fye})*ih*(1-1/zoom)':d=1:s={OW}x{OH}:fps={fps},setsar=1")


def _shot_index(analysis: dict, t: float) -> int:
    for k, sh in enumerate(analysis["shots"]):
        if sh["s"] <= t < sh["e"]:
            return k
    return len(analysis["shots"]) - 1


_NVENC_BAD = False


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
    global _NVENC_BAD
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
    focus = []
    for i, p in enumerate(pieces):
        dur = p["src_e"] - p["src_s"]
        cmd += ["-ss", f"{p['src_s']:.3f}", "-t", f"{dur:.3f}", "-i", str(seg.resolve())]
        mid = (p["src_s"] + p["src_e"]) / 2
        lay = layout_at(analysis, mid)
        dv = (analysis.get("directions") or {}).get(str(_shot_index(analysis, mid)))
        if dv:
            chain, fo = _directed(i, dv, lay, W, H, r["fps"], accent, OW, OH, p["src_s"], p["src_e"])
            graph.append(chain)
            focus.append((p["out_s"], p["out_s"] + dur, fo[0], fo[1]))
        else:
            focus.append((p["out_s"], p["out_s"] + dur, .5, .42))
            graph.append(_vchain(i, lay, W, H, p["zoom"] if lay.get("mode") == "crop" else 1.0, r["fps"], accent, OW, OH, p["src_s"], p["src_e"]))
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
    zmoves = []
    for k, dv in (analysis.get("directions") or {}).items():
        sh = analysis["shots"][int(k)]
        mine = [p for p in pieces if sh["s"] <= (p["src_s"] + p["src_e"]) / 2 < sh["e"]]
        if not mine:
            continue
        until = max(p["out_s"] + p["src_e"] - p["src_s"] for p in mine)
        base, level = dv["zoom"], 1.0
        for m in sorted(dv.get("moves") or [], key=lambda m: m.get("start", m.get("at", 0))):
            rel = m["to"] / base
            if m["type"] == "push":
                a, b = _to_out(m["start"], pieces), min(_to_out(m["end"], pieces), until)
                if b - a > .1:
                    zmoves.append((a, b, "push", level, rel, until))
                    level = rel
            else:
                a = _to_out(m["at"], pieces)
                if a < until:
                    zmoves.append((a, a, "punch", level, rel, min(m["hold"], until - a)))
    zmoves.sort(key=lambda m: m[0])
    if zmoves:
        graph.append(f"{vlast}{_zoom_pass(zmoves, focus, r['fps'], OW, OH)}[czm]")
        vlast = "[czm]"
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

    x264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(r["crf"]), "-profile:v", "high"]
    encoders = [x264]
    if r.get("encoder") in ("auto", "nvenc") and _has_nvenc() and not _NVENC_BAD:
        encoders.insert(0, ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", str(r["crf"] + 1), "-b:v", "0"])
    tail = ["-filter_complex", ";".join(graph), "-map", vlast, "-map", "[ao]"]
    post = ["-pix_fmt", "yuv420p", "-r", str(r["fps"]), "-c:a", "aac", "-b:a", "192k",
            "-t", f"{D:.3f}", "-movflags", "+faststart", out.name]
    log = work / "render.log"
    errors = []
    for attempt, enc in enumerate(encoders + [x264]):    # GPU encoder, then CPU, then CPU once more
        try:
            if out.exists():
                out.unlink()
            run(cmd + tail + enc + post, cwd=work, timeout=1800)
            if not out.exists() or out.stat().st_size < 10_000:
                raise EchoError("ffmpeg finished but wrote no video")
            break
        except EchoError as e:
            if enc[1] == "h264_nvenc":
                _NVENC_BAD = True                 # don't retry the GPU encoder for the rest of this run
            errors.append(f"attempt {attempt + 1} ({enc[1]}): {e}")
            log.write_text("\n\n".join(errors) + "\n\ncommand:\n" + " ".join(cmd + tail + enc + post), "utf-8")
    else:
        raise EchoError(f"Render failed after {len(errors)} tries. Details: {log}\n" + errors[-1][:300])
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
