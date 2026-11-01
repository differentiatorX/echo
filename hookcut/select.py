"""The ranking engine.

1. Scout:  Claude reads the annotated transcript and proposes candidates.
2. Judge:  Claude grades every candidate on a strict rubric, tightens boundaries, writes the hook.
3. Fuse:   final = Claude's grade + replay heatmap + comment mentions + pace (weights in settings).
4. Pick:   greedy top-N with overlap and topic de-duplication.
"""
from __future__ import annotations

import re

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import llm
from .config import FEEDBACK, PROMPTS
from .signals import Signals
from .util import ts

RUBRIC = {"hook": 0.35, "flow": 0.2, "value": 0.2, "share": 0.15, "loop": 0.1}


class Candidate(BaseModel):
    start: float
    end: float
    hook_line: str = ""
    why: str = ""


class Verdict(BaseModel):
    id: str
    start: float
    end: float
    hook: float = Field(ge=0, le=10)
    flow: float = Field(ge=0, le=10)
    value: float = Field(ge=0, le=10)
    share: float = Field(ge=0, le=10)
    loop: float = Field(ge=0, le=10)
    hook_title: str = ""
    cold_open: str | None = None
    keywords: list[str] = []
    ig_caption: str = ""
    hashtags: list[str] = []
    why: str = ""

    @field_validator("hashtags")
    @classmethod
    def _tags(cls, v):
        return [t if t.startswith("#") else f"#{t}" for t in v][:5]


def _prompt(name: str, **kw) -> str:
    text = (PROMPTS / f"{name}.md").read_text("utf-8")
    for k, v in kw.items():
        text = text.replace("{{" + k + "}}", str(v))
    return text


def _snap(lines: list[dict], s: float, e: float) -> tuple[float, float]:
    """Snap to the nearest line start / line end."""
    if not lines:
        return s, e
    start = min(lines, key=lambda l: abs(l["s"] - s))["s"]
    ends = [l for l in lines if l["e"] > start]
    end = min(ends, key=lambda l: abs(l["e"] - e))["e"] if ends else e
    return start, end


def _chunks(lines: list[dict], span: float = 45 * 60, overlap: float = 120) -> list[list[dict]]:
    if not lines:
        return []
    out, t, last = [], lines[0]["s"], lines[-1]["e"]
    while t < last:
        out.append([l for l in lines if t <= l["s"] < t + span])
        t += span - overlap
    return [c for c in out if c]


def _overlap(a: dict, b: dict) -> float:
    inter = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return inter / max(1e-6, min(a["end"] - a["start"], b["end"] - b["start"]))


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z']+", s.lower()) if len(w) > 3}


def scout(info: dict, lines: list[dict], sig: Signals, cfg: dict) -> list[dict]:
    c = cfg["clips"]
    found: list[dict] = []
    chunks = _chunks(lines)
    per = max(4, c["candidates"] // max(1, len(chunks)) + 2)
    for chunk in chunks:
        transcript = "\n".join(f"[{l['s']:.1f}] {l['text']}{sig.line_tags(l['s'], l['e'])}" for l in chunk)
        p = _prompt("scout", TITLE=info["title"], CHANNEL=info.get("channel") or "unknown",
                    RANGE=f"{ts(chunk[0]['s'])}-{ts(chunk[-1]['e'])}, times in seconds",
                    N=per, MIN=c["min_len"], MAX=c["max_len"], TRANSCRIPT=transcript)
        for item in llm.complete_json(p, cfg["llm"]) or []:
            try:
                cand = Candidate(**item)
            except (ValidationError, TypeError):
                continue
            s, e = _snap(lines, cand.start, cand.end)
            if e - s < c["min_len"] * 0.6 or e - s > c["max_len"] * 1.6:
                continue
            found.append({"start": s, "end": e, "hook_line": cand.hook_line, "why": cand.why})
    # drop near-duplicates from chunk overlaps
    uniq: list[dict] = []
    for f in found:
        if all(_overlap(f, u) < 0.6 for u in uniq):
            uniq.append(f)
    return uniq[: c["candidates"] + 4]


def _feedback_block() -> str:
    p = FEEDBACK / "winners.md"
    if p.exists():
        return ("What has actually performed on this Instagram account (learn the pattern, don't copy):\n"
                + p.read_text("utf-8")[:4000])
    return ""


def judge(info: dict, cands: list[dict], words: list[dict], sig: Signals, cfg: dict) -> list[dict]:
    blocks = []
    for i, cd in enumerate(cands):
        cd["id"] = f"k{i + 1:02d}"
        text = " ".join(w["w"] for w in words if cd["start"] <= w["s"] < cd["end"])
        h = sig.heat_score(cd["start"], cd["end"])
        aud = []
        if h is not None:
            aud.append(f"replay heat {h:.0f}/100")
        n = sum(1 for t, _ in sig.mentions if cd["start"] - 5 <= t <= cd["end"] + 5)
        if n:
            aud.append(f"{n} timestamped comments")
        blocks.append(f"### {cd['id']}  range {cd['start']:.1f}-{cd['end']:.1f}s "
                      f"({cd['end'] - cd['start']:.0f}s){'  audience: ' + ', '.join(aud) if aud else ''}\n{text}")
    p = _prompt("judge", TITLE=info["title"], CHANNEL=info.get("channel") or "unknown",
                FEEDBACK=_feedback_block(), CANDIDATES="\n\n".join(blocks))
    by_id = {c["id"]: c for c in cands}
    out = []
    for item in llm.complete_json(p, cfg["llm"]) or []:
        try:
            v = Verdict(**item)
        except (ValidationError, TypeError):
            continue
        base = by_id.get(v.id)
        if not base:
            continue
        s = max(base["start"] - 2, min(v.start, base["end"]))
        e = min(base["end"] + 2, max(v.end, s + 5))
        out.append({**base, **v.model_dump(), "start": s, "end": e})
    return out


def fuse(verdicts: list[dict], sig: Signals, cfg: dict) -> list[dict]:
    w = dict(cfg["weights"])
    if not sig.has_heat:
        w["heat"] = 0
    if not sig.has_crowd:
        w["crowd"] = 0
    raws = [sig.crowd_raw(v["start"], v["end"]) for v in verdicts]
    top = max(raws) if raws and max(raws) > 0 else 1.0
    for v, raw in zip(verdicts, raws):
        llm_score = sum(v[k] * wt for k, wt in RUBRIC.items()) * 10
        parts = {"llm": llm_score, "heat": sig.heat_score(v["start"], v["end"]),
                 "crowd": 100 * raw / top if sig.has_crowd else None,
                 "pace": sig.pace_score(v["start"], v["end"])}
        num = den = 0.0
        for k, val in parts.items():
            if val is not None and w.get(k):
                num += w[k] * val
                den += w[k]
        v["score"] = round(num / den if den else llm_score, 1)
        v["breakdown"] = {**{k: v[k] for k in RUBRIC},
                          **{k: (round(x, 1) if x is not None else None) for k, x in parts.items()}}
    return sorted(verdicts, key=lambda v: v["score"], reverse=True)


def pick(ranked: list[dict], n: int) -> list[dict]:
    chosen: list[dict] = []
    for v in ranked:
        if any(_overlap(v, c) > 0.25 for c in chosen):
            continue
        tv = _tokens(v.get("hook_title", "") + " " + v.get("why", ""))
        if any(len(tv & _tokens(c.get("hook_title", "") + " " + c.get("why", ""))) / max(1, len(tv)) > 0.6
               for c in chosen):
            continue
        chosen.append(v)
        if len(chosen) == n:
            break
    return chosen
