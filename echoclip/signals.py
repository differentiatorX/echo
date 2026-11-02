"""Real audience data. This is what the clipping SaaS tools don't use.

heat:  YouTube's "Most replayed" curve -> how much this window gets rewatched vs the rest of the video.
crowd: top comments that quote a timestamp ("12:34 had me dying") weighted by likes.
pace:  words per second vs the video's median. Dead air loses, energy wins (lightly weighted).
"""
from __future__ import annotations

import math
import re
from statistics import median, pstdev

TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?![\d:])")
INTRO_SKIP = 30.0  # the heatmap always spikes at 0:00; ignore it


class Signals:
    def __init__(self, info: dict, comments: list[dict] | None, words: list[dict]):
        self.duration = info.get("duration") or (words[-1]["e"] if words else 0)
        self.heat = [(h["start_time"], h["end_time"], float(h["value"])) for h in info.get("heatmap") or []]
        body = [v for s, e, v in self.heat if s >= INTRO_SKIP] or [v for *_, v in self.heat]
        self.heat_mu = sum(body) / len(body) if body else 0.0
        self.heat_sd = pstdev(body) if len(body) > 1 else 0.0
        self.mentions = self._mentions(comments or [])
        self.words = words
        rates = self._rates(words)
        self.pace_med = median(rates) if rates else 2.5

    # ---- raw helpers ----
    def _mentions(self, comments: list[dict]) -> list[tuple[float, float]]:
        out = []
        for c in comments:
            for m in TS_RE.finditer(c.get("text", "")):
                h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
                t = h * 3600 + mi * 60 + s
                if s < 60 and (not self.duration or t <= self.duration):
                    out.append((float(t), 1.0 + math.log1p(c.get("likes", 0))))
        return out

    @staticmethod
    def _rates(words: list[dict], win: float = 20.0) -> list[float]:
        if not words:
            return []
        out, i = [], 0
        t = words[0]["s"]
        while t < words[-1]["e"]:
            n = sum(1 for w in words if t <= w["s"] < t + win)
            if n:
                out.append(n / win)
            t += win
            i += 1
        return out

    def heat_at(self, t: float) -> float | None:
        for s, e, v in self.heat:
            if s <= t < e:
                return v
        return None

    # ---- scores 0..100 ----
    @property
    def has_heat(self) -> bool:
        return bool(self.heat) and self.heat_sd > 1e-6

    def heat_score(self, s: float, e: float) -> float | None:
        if not self.has_heat:
            return None
        vals = [v for hs, he, v in self.heat if he > s and hs < e]
        if not vals:
            return None
        z = (sum(vals) / len(vals) - self.heat_mu) / self.heat_sd
        peak = (max(vals) - self.heat_mu) / self.heat_sd
        return round(100 / (1 + math.exp(-(0.7 * z + 0.3 * peak))), 1)

    @property
    def has_crowd(self) -> bool:
        return len(self.mentions) >= 3

    def crowd_raw(self, s: float, e: float) -> float:
        return sum(w for t, w in self.mentions if s - 5 <= t <= e + 5)

    def pace_score(self, s: float, e: float) -> float | None:
        n = sum(1 for w in self.words if s <= w["s"] < e)
        if not n or e <= s:
            return None
        r = n / (e - s) / (self.pace_med or 2.5)
        return round(max(0.0, min(100.0, 50 + 60 * (r - 1))), 1)

    # ---- prompt annotations ----
    def line_tags(self, s: float, e: float) -> str:
        tags = []
        if self.has_heat:
            vals = [v for hs, he, v in self.heat if he > s and hs < e and hs >= INTRO_SKIP]
            if vals and max(vals) >= self.heat_mu + 1.2 * self.heat_sd:
                tags.append("REPLAYED")
        n = sum(1 for t, _ in self.mentions if s - 2 <= t <= e + 2)
        if n:
            tags.append(f"COMMENTED x{n}")
        return f"  <{' '.join(tags)}>" if tags else ""
