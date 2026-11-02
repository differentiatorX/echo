"""Turns precise word timings into an edit decision list.

- Jump-cuts dead air (gap > max_gap) down to a breath.
- Drops isolated filler words (um, uh...).
- Cold open: the judge's strongest line plays first as a teaser, then a hard cut to the start.
- Punch zoom: every real cut and every ~3 s alternates framing so the eye never settles.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from .ingest import FILLERS

PRE, POST = 0.06, 0.12


@dataclass
class Piece:
    src_s: float
    src_e: float
    zoom: float = 1.0
    cut_in: bool = True     # hard cut at the start (fade audio a hair)
    cut_out: bool = True
    out_s: float = 0.0
    group: str = "body"

    @property
    def dur(self) -> float:
        return self.src_e - self.src_s


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", w.lower())


def find_quote(words: list[dict], quote: str | None) -> tuple[int, int] | None:
    if not quote:
        return None
    q = [_norm(x) for x in quote.split() if _norm(x)]
    if len(q) < 3:
        return None
    toks = [_norm(w["w"]) for w in words]
    best, best_i = 0.0, -1
    for i in range(0, max(0, len(toks) - len(q)) + 1):
        win = toks[i:i + len(q)]
        hit = sum(1 for a, b in zip(win, q) if a == b) / len(q)
        if hit > best:
            best, best_i = hit, i
    return (best_i, best_i + len(q) - 1) if best >= 0.7 else None


def _runs(words: list[dict], max_gap: float, keep: float, cut_silence: bool) -> list[Piece]:
    pieces: list[Piece] = []
    for i, w in enumerate(words):
        if pieces and (not cut_silence or w["s"] - words[i - 1]["e"] <= max_gap) \
                and w.get("_i", i) == words[i - 1].get("_i", i - 1) + 1:
            pieces[-1].src_e = w["e"] + POST
            continue
        if pieces:
            prev = words[i - 1]
            pieces[-1].src_e = min(pieces[-1].src_e, prev["e"] + max(keep, 0.04))
        pieces.append(Piece(src_s=max(0.0, w["s"] - PRE), src_e=w["e"] + POST))
    for a, b in zip(pieces, pieces[1:]):  # never overlap in source time
        if a.src_e > b.src_s and b.src_s > a.src_s:
            mid = (a.src_e + b.src_s) / 2
            a.src_e, b.src_s = mid, mid
    return pieces


def _split_long(p: Piece, words: list[dict], every: float) -> list[Piece]:
    if p.dur <= every * 1.5:
        return [p]
    inside = [w for w in words if p.src_s <= w["s"] and w["e"] <= p.src_e]
    out, cur_s = [], p.src_s
    for w in inside:
        if w["s"] - cur_s >= every and p.src_e - w["s"] >= every * 0.5:
            out.append(Piece(cur_s, w["s"], cut_in=not out and p.cut_in, cut_out=False, group=p.group))
            cur_s = w["s"]
    out.append(Piece(cur_s, p.src_e, cut_in=not out and p.cut_in, cut_out=p.cut_out, group=p.group))
    return out


def plan(words: list[dict], start: float, end: float, cfg: dict, cold_open: str | None = None,
         removed: set[int] | None = None, keywords: list[str] | None = None) -> dict:
    """words: precise words in the clip file's local time. start/end: clip bounds in local time."""
    e = cfg["edit"]
    removed = removed or set()
    body = []
    for i, w in enumerate(words):
        mid = (w["s"] + w["e"]) / 2
        if start - 0.05 <= mid <= end + 0.05 and i not in removed:
            if e["remove_fillers"] and _norm(w["w"]) in FILLERS:
                continue
            body.append({**w, "_i": i})
    if not body:
        raise ValueError("clip has no words left after editing")

    groups: list[tuple[str, list[dict]]] = []
    span = find_quote(body, cold_open) if (e["cold_open"] and cold_open) else None
    if span and span[0] > 2:
        cold = body[span[0]:span[1] + 1]
        if 1.0 <= cold[-1]["e"] - cold[0]["s"] <= 7.0:
            groups.append(("cold", cold))
            if not e["cold_open_repeat"]:
                body = body[:span[0]] + body[span[1] + 1:]
    groups.append(("body", body))

    pieces: list[Piece] = []
    for name, ws in groups:
        runs = _runs(ws, e["max_gap"], e["keep_gap"], e["remove_silence"])
        for r in runs:
            r.group = name
            pieces.extend(_split_long(r, ws, e["punch_every"]))
    zoom_hi = float(e["punch_zoom"])
    t, j = 0.0, 0
    for p in pieces:
        if p.group == "cold":
            p.zoom = round(zoom_hi * 1.04, 3)
        else:
            p.zoom = 1.0 if j % 2 == 0 else zoom_hi
            j += 1
        p.out_s = t
        t += p.dur

    kw = {_norm(k) for k in (keywords or [])}
    out_words = []
    for name, ws in groups:
        gp = [p for p in pieces if p.group == name]
        for w in ws:
            mid = (w["s"] + w["e"]) / 2
            p = next((p for p in gp if p.src_s <= mid < p.src_e), None)
            if p is None:
                continue
            out_words.append({"w": w["w"], "_i": w["_i"], "grp": name,
                              "s": round(p.out_s + max(0.0, w["s"] - p.src_s), 3),
                              "e": round(p.out_s + min(p.dur, w["e"] - p.src_s), 3),
                              "kw": _norm(w["w"]) in kw})
    return {"pieces": [asdict(p) for p in pieces], "words": out_words, "duration": round(t, 3),
            "cold_open": groups[0][0] == "cold"}
