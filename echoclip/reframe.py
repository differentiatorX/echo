"""Decide a 9:16 layout per shot.

crop   one face: static crop centered on the speaker (static per shot looks cleaner than jittery tracking)
split  two people side by side (podcasts): stacked top/bottom with a thin accent divider
blur   no face (screen recordings, b-roll): full frame over a blurred zoomed copy
fit    source is already vertical
"""
from __future__ import annotations

from pathlib import Path
from statistics import median

import cv2

from .util import probe

_haar = None


def _detector():
    global _haar
    try:
        import mediapipe as mp  # optional, more accurate
        fd = mp.solutions.face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.55)

        def detect(frame):
            res = fd.process(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            out = []
            for d in res.detections or []:
                b = d.location_data.relative_bounding_box
                out.append((b.xmin + b.width / 2, b.ymin + b.height / 2, b.width))
            return out
        return detect
    except Exception:
        pass
    if _haar is None:
        _haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    def detect(frame):
        h, w = frame.shape[:2]
        scale = 640 / w
        small = cv2.cvtColor(cv2.resize(frame, (640, int(h * scale))), cv2.COLOR_BGR2GRAY)
        faces = _haar.detectMultiScale(small, scaleFactor=1.1, minNeighbors=6, minSize=(36, 36))
        sw, sh = small.shape[1], small.shape[0]
        return [((x + fw / 2) / sw, (y + fh / 2) / sh, fw / sw) for x, y, fw, fh in faces]
    return detect


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


def analyze(path: Path) -> dict:
    meta = probe(path)
    W, H = meta["w"], meta["h"]
    if W / max(H, 1) < 0.7:
        return {"w": W, "h": H, "shots": [{"s": 0, "e": meta["dur"], "mode": "fit"}]}
    detect = _detector()
    cap = cv2.VideoCapture(str(path))
    result = []
    for s, e in shots(path):
        n = int(min(24, max(4, (e - s) * 4)))
        samples = []
        for k in range(n):
            t = s + (e - s) * (k + 0.5) / n
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if ok:
                samples.append(sorted(detect(frame), key=lambda f: -f[2])[:3])
        result.append({"s": s, "e": e, **_layout(samples)})
    cap.release()
    return {"w": W, "h": H, "shots": result}


def _layout(samples: list[list[tuple]]) -> dict:
    if not samples:
        return {"mode": "blur"}
    with_face = [f for f in samples if f]
    if len(with_face) / len(samples) < 0.3:
        return {"mode": "blur"}
    pairs = [f for f in samples if len(f) >= 2 and abs(f[0][0] - f[1][0]) > 0.25
             and min(f[0][2], f[1][2]) / max(f[0][2], f[1][2]) > 0.5]
    if len(pairs) / len(samples) >= 0.5:
        left = [min(p[:2], key=lambda x: x[0]) for p in pairs]
        right = [max(p[:2], key=lambda x: x[0]) for p in pairs]
        return {"mode": "split",
                "a": [median(f[0] for f in left), median(f[1] for f in left)],
                "b": [median(f[0] for f in right), median(f[1] for f in right)]}
    main = [f[0] for f in with_face]
    return {"mode": "crop", "x": median(f[0] for f in main), "y": median(f[1] for f in main)}


def layout_at(analysis: dict, t: float) -> dict:
    for sh in analysis["shots"]:
        if sh["s"] <= t < sh["e"]:
            return sh
    return analysis["shots"][-1]
