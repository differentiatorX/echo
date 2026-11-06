"""Turns precise word timings into an edit decision list.

- Jump-cuts dead air (gap > max_gap) down to a breath.
- Drops isolated filler words (um, uh...).
- Cold open: the judge's strongest line plays first as a teaser, then a hard cut to the start.
- Punch zoom: only across real jump cuts, centred on the subject, so cuts read as intentional.
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


def _quiet(energy: dict | None, a: float, b: float, ratio: float) -> bool:
    """True when the audio between a and b is near-silent compared with the speech around it.
    A gap full of crowd noise, music or a ball hitting a bat is not silence."""
    if not energy:
        return True
    hop, rms = energy["hop"], energy["rms"]
    i0, i1 = int(a / hop), max(int(b / hop), int(a / hop) + 1)
    seg = rms[i0:i1]
    if not seg:
        return True
    return (sum(seg) / len(seg)) < ratio * energy["speech"]


def _runs(words: list[dict], max_gap: float, keep: float, cut_silence: bool,
          energy: dict | None = None, ratio: float = .35) -> list[Piece]:
    pieces: list[Piece] = []
    for i, w in enumerate(words):
        gap_ok = (not cut_silence or w["s"] - words[i - 1]["e"] <= max_gap
                  or not _quiet(energy, words[i - 1]["e"], w["s"], ratio)) if i else True
        if pieces and gap_ok \
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
         removed: set[int] | None = None, keywords: list[str] | None = None,
         energy: dict | None = None, trim: bool | None = None) -> dict:
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
        cut = e["remove_silence"] if trim is None else trim
        runs = _runs(ws, e["max_gap"], e["keep_gap"], cut, energy, e.get("quiet_ratio", .35))
        for r in runs:
            r.group = name
            if e.get("punch", "cuts") == "rhythm":
                pieces.extend(_split_long(r, ws, e["punch_every"]))
            else:
                pieces.append(r)
    # Zoom only where it does a job: a punch-in hides a jump cut. No zooms on a timer.
    zoom_hi = float(e["punch_zoom"])
    mode = e.get("punch", "cuts")
    t, level = 0.0, 0
    for i, p in enumerate(pieces):
        if mode == "off":
            p.zoom = 1.0
        elif p.group == "cold":
            p.zoom = zoom_hi
        elif mode == "rhythm":
            p.zoom = 1.0 if level % 2 == 0 else zoom_hi
            level += 1
        else:
            prev = pieces[i - 1] if i else None
            jump = prev is not None and prev.group == p.group and p.src_s - prev.src_e > .05
            if jump:
                level += 1                     # alternate only across a real cut, so the cut reads as intentional
            p.zoom = zoom_hi if level % 2 else 1.0
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
