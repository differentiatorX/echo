"""Vertical reframing that behaves like a camera operator.

- One layout per clip. Shots never flip between full-screen and a letterboxed box.
- Finds the subject with frontal and profile face detection, a person detector for people
  turned away or far off, and motion as a last resort.
- The camera holds still while the subject stays near the centre, then pans smoothly
  when they move. No jitter, no random jumps.

Layouts:
  crop   full-screen vertical crop that follows the subject (default)
  split  two people side by side, stacked top and bottom (podcasts)
  fit    letterbox over a blurred copy (source already vertical, or reframe.mode = "fit")
"""
from __future__ import annotations

from pathlib import Path
from statistics import median

import cv2
import numpy as np

from .util import probe

_det = None


def _detectors():
    global _det
    if _det is not None:
        return _det
    d = {}
    if hasattr(cv2, "CascadeClassifier"):
        root = cv2.data.haarcascades
        d["front"] = cv2.CascadeClassifier(root + "haarcascade_frontalface_default.xml")
        d["profile"] = cv2.CascadeClassifier(root + "haarcascade_profileface.xml")
    if hasattr(cv2, "HOGDescriptor"):
        hog = cv2.HOGDescriptor()
        hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        d["hog"] = hog
    _det = d
    return d


def _faces(gray, d):
    """Faces as (cx, cy, size) in 0..1 units: frontal plus both profile directions."""
    h, w = gray.shape
    found = []
    if "front" in d:
        for x, y, fw, fh in d["front"].detectMultiScale(gray, 1.1, 6, minSize=(28, 28)):
            found.append(((x + fw / 2) / w, (y + fh / 2) / h, fw / w))
    if "profile" in d:
        for flip in (False, True):
            g = cv2.flip(gray, 1) if flip else gray
            for x, y, fw, fh in d["profile"].detectMultiScale(g, 1.1, 6, minSize=(28, 28)):
                cx = (x + fw / 2) / w
                found.append(((1 - cx) if flip else cx, (y + fh / 2) / h, fw / w))
    merged = []                                      # drop duplicates of the same face
    for f in sorted(found, key=lambda f: -f[2]):
        if all(abs(f[0] - m[0]) > m[2] * .6 or abs(f[1] - m[1]) > m[2] * .6 for m in merged):
            merged.append(f)
    return merged


def _people(small, d):
    if "hog" not in d:
        return []
    h, w = small.shape[:2]
    rects, weights = d["hog"].detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.06)
    out = []
    for (x, y, rw, rh), wt in zip(rects, np.ravel(weights) if len(rects) else []):
        if wt > .4:
            out.append(((x + rw / 2) / w, (y + rh * .16) / h, rw / w))   # aim at the head end of the box
    return sorted(out, key=lambda p: -p[2])


def _motion_x(prev, cur):
    """Where things are moving, ignoring grain and compression noise."""
    if prev is None:
        return None
    diff = cv2.absdiff(prev, cur)
    mask = (diff > 28).astype(np.float32)
    if mask.mean() < .002 or mask.mean() > .5:        # nothing moving, or the whole camera moved
        return None
    col = mask.sum(axis=0)
    xs = np.arange(col.size)
    return float((col * xs).sum() / col.sum() / col.size)


def _frames(path: Path, fps: float = 4.0, width: int = 640):
    """Exact frames at a fixed rate in one ffmpeg pass (seeking with OpenCV snaps to keyframes)."""
    import subprocess

    from .util import clean_env
    meta = probe(path)
    w = width
    h = int(round(meta["h"] * width / max(meta["w"], 1) / 2)) * 2
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"fps={fps},scale={w}:{h}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, env=clean_env(), timeout=600)
    n = len(p.stdout) // (w * h)
    frames = np.frombuffer(p.stdout[: n * w * h], dtype=np.uint8).reshape(n, h, w)
    times = [(i + .5) / fps for i in range(n)]
    return times, frames


def shots(path: Path) -> list[tuple[float, float]]:
    dur = probe(path)["dur"]
    try:
        from scenedetect import ContentDetector, detect
        scenes = detect(str(path), ContentDetector(threshold=27.0, min_scene_len=12))
        out = [(s.get_seconds(), e.get_seconds()) for s, e in scenes]
    except Exception:
        out = []
    if not out:
        return [(0.0, dur)]
    merged: list[list[float]] = []
    for s, e in out:
        if merged and e - s < 0.6:
            merged[-1][1] = e
        else:
            merged.append([s, e])
    return [(s, e) for s, e in merged]


def _operator(times, targets, crop_frac, dead=.05, max_pan=.22):
    """Camera path: hold while the subject is inside the dead zone, otherwise ease toward it
    at a limited speed. Returns keyframes [(t, x)] in 0..1 frame units."""
    lo, hi = crop_frac / 2, 1 - crop_frac / 2
    pts = [(t, min(hi, max(lo, x))) for t, x in zip(times, targets)]
    if not pts:
        return []
    xs = [x for _, x in pts]
    k = 2                                                # median filter kills single-frame misdetections
    sm = [median(xs[max(0, i - k):i + k + 1]) for i in range(len(xs))]
    cam = median(sm[:min(len(sm), 4)])
    keys = [(pts[0][0], cam)]
    for i in range(1, len(pts)):
        t, target = pts[i][0], sm[i]
        dt = t - pts[i - 1][0]
        if abs(target - cam) > dead:
            step = max(-max_pan * dt, min(max_pan * dt, target - cam))
            cam += step
        keys.append((t, cam))
    out = [keys[0]]                                      # keep only keyframes where the camera moves
    for kf in keys[1:]:
        if abs(kf[1] - out[-1][1]) > .004:
            out.append(kf)
    if out[-1][0] != keys[-1][0]:
        out.append((keys[-1][0], out[-1][1]))
    return [(round(t, 3), round(x, 4)) for t, x in out]


def analyze(path: Path, settings: dict | None = None) -> dict:
    settings = settings or {}
    meta = probe(path)
    W, H = meta["w"], meta["h"]
    mode = settings.get("mode", "auto")
    shot_list = shots(path)
    if W / max(H, 1) < 0.7 or mode == "fit":
        return {"w": W, "h": H, "version": 2, "shots": [{"s": s, "e": e, "mode": "fit"} for s, e in shot_list]}
    d = _detectors()
    crop_frac = min(1.0, (H * 9 / 16) / W)
    all_t, all_f = _frames(path)
    result = []
    for s, e in shot_list:
        idx = [i for i, t in enumerate(all_t) if s <= t < e] or [min(range(len(all_t)), key=lambda i: abs(all_t[i] - (s + e) / 2))] if all_t else []
        if len(idx) > 48:
            idx = idx[:: max(1, len(idx) // 48)]
        times, faces_per, people_per, motion_per = [], [], [], []
        prev = None
        for i in idx:
            raw = all_f[i]
            gray = cv2.equalizeHist(raw)
            fs = _faces(gray, d)
            ps = _people(raw, d) if not fs else []
            tiny = cv2.GaussianBlur(cv2.resize(raw, (160, int(160 * raw.shape[0] / raw.shape[1]))), (5, 5), 0)
            times.append(all_t[i])
            faces_per.append(fs)
            people_per.append(ps)
            motion_per.append(_motion_x(prev, tiny))
            prev = tiny
        if not times:
            result.append({"s": s, "e": e, "mode": "crop", "y": .42, "track": [[s, .5]]})
            continue
        # two people side by side for most of the shot: split screen
        pairs = [f for f in faces_per if len(f) >= 2 and abs(f[0][0] - f[1][0]) > .3
                 and min(f[0][2], f[1][2]) / max(f[0][2], f[1][2]) > .55]
        if len(pairs) / len(times) >= .6:
            left = [min(p[:2], key=lambda x: x[0]) for p in pairs]
            right = [max(p[:2], key=lambda x: x[0]) for p in pairs]
            result.append({"s": s, "e": e, "mode": "split",
                           "a": [median(f[0] for f in left), median(f[1] for f in left)],
                           "b": [median(f[0] for f in right), median(f[1] for f in right)]})
            continue
        # follow one subject: the biggest face, preferring whoever we were already following
        targets, ys, last = [], [], None
        for fs, ps, mx in zip(faces_per, people_per, motion_per):
            cand = fs or ps
            if cand:
                if last is None:
                    pick = max(cand, key=lambda c: c[2])
                else:
                    pick = max(cand, key=lambda c: c[2] * 3 - abs(c[0] - last))
                x = pick[0]
                ys.append(pick[1])
            elif mx is not None:
                x = mx
            else:
                x = last if last is not None else .5
            targets.append(x)
            last = x
        track = _operator(times, targets, crop_frac, settings.get("deadzone", .05), settings.get("max_pan", .22))
        result.append({"s": s, "e": e, "mode": "crop", "y": median(ys) if ys else .42, "track": track})
    return {"w": W, "h": H, "version": 2, "shots": result}


def layout_at(analysis: dict, t: float) -> dict:
    for sh in analysis["shots"]:
        if sh["s"] <= t < sh["e"]:
            return sh
    return analysis["shots"][-1]
