"""Big, animated, word-by-word captions rendered by libass (fast, no browser needed)."""
from __future__ import annotations

import re

PRESETS: dict[str, dict] = {
    "hookcut": {"label": "Hookcut", "font": "Anton", "size": 118, "color": "#FFFFFF", "active": "#4FB8FF",
                "keyword": "#FFD98A", "outline": 7, "shadow": 3, "words": 3, "chars": 18, "y": 0.64,
                "upper": True, "pop": True, "karaoke": False},
    "beast": {"label": "Beast", "font": "Anton", "size": 122, "color": "#FFFFFF", "active": "#4BE36A",
              "keyword": "#FFE14D", "outline": 9, "shadow": 4, "words": 2, "chars": 14, "y": 0.62,
              "upper": True, "pop": True, "karaoke": False},
    "karaoke": {"label": "Karaoke", "font": "Anton", "size": 94, "color": "#FFFFFF", "active": "#4FB8FF",
                "keyword": "#4FB8FF", "outline": 6, "shadow": 2, "words": 4, "chars": 22, "y": 0.66,
                "upper": True, "pop": False, "karaoke": True},
    "minimal": {"label": "Minimal", "font": "Anton", "size": 76, "color": "#FFFFFF", "active": "#FFFFFF",
                "keyword": "#FFFFFF", "outline": 5, "shadow": 0, "words": 4, "chars": 24, "y": 0.70,
                "upper": False, "pop": False, "karaoke": False},
}


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _tag_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return f"\\c&H{h[4:6]}{h[2:4]}{h[0:2]}&".upper().replace("\\C", "\\c")


def _t(sec: float) -> str:
    sec = max(0.0, sec)
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _esc(w: str) -> str:
    return w.replace("\\", "").replace("{", "(").replace("}", ")")


def _groups(words: list[dict], n: int, chars: int) -> list[list[dict]]:
    out, cur = [], []
    for i, w in enumerate(words):
        if cur and (len(cur) >= n or sum(len(x["w"]) + 1 for x in cur) + len(w["w"]) > chars
                    or w["s"] - cur[-1]["e"] > 0.45 or w.get("grp") != cur[-1].get("grp")):
            out.append(cur)
            cur = []
        cur.append(w)
        if re.search(r"[.?!]$", w["w"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def _wrap(text: str, width: int = 20) -> str:
    lines, cur = [], ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return "\\N".join(lines[:3])


def build_ass(words: list[dict], duration: float, preset: str = "hookcut", hook_title: str = "",
              hook_seconds: float = 2.6, watermark: str = "") -> str:
    p = PRESETS.get(preset, PRESETS["hookcut"])
    W, H = 1080, 1920
    y = int(H * p["y"])
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{p['font']},{p['size']},{ass_color(p['color'])},{ass_color(p['color'])},{ass_color('#000000')},{ass_color('#000000', 0x60)},0,0,0,0,100,100,1,0,1,{p['outline']},{p['shadow']},5,60,60,0,1
Style: Hook,{p['font']},76,{ass_color('#FFFFFF')},{ass_color('#FFFFFF')},{ass_color('#2F6BFF')},{ass_color('#2F6BFF')},0,0,0,0,100,100,0.5,0,3,18,0,8,80,80,0,1
Style: Mark,{p['font']},38,{ass_color('#FFFFFF', 0x50)},{ass_color('#FFFFFF')},{ass_color('#000000', 0x80)},{ass_color('#000000', 0x80)},0,0,0,0,100,100,1,0,1,2,0,2,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    ev = []
    groups = _groups(words, p["words"], p["chars"])
    for gi, g in enumerate(groups):
        g_end = g[-1]["e"] + 0.25
        if gi + 1 < len(groups):
            g_end = min(g_end, groups[gi + 1][0]["s"])
        for k, w in enumerate(g):
            s = g[0]["s"] if k == 0 else w["s"]
            e = g[k + 1]["s"] if k + 1 < len(g) else g_end
            if e - s < 0.02:
                continue
            parts = []
            for j, x in enumerate(g):
                text = _esc(x["w"].upper() if p["upper"] else x["w"])
                spoken = j <= k if p["karaoke"] else j == k
                if spoken:
                    scale = "\\fscx108\\fscy108" if x.get("kw") else ""
                    parts.append("{" + _tag_color(p["keyword"] if x.get("kw") else p["active"]) + scale + "}"
                                 + text + "{\\r}")
                elif x.get("kw"):
                    parts.append("{" + _tag_color(p["keyword"]) + "}" + text + "{\\r}")
                else:
                    parts.append(text)
            pre = f"{{\\an5\\pos({W // 2},{y})"
            if p["pop"] and k == 0:
                pre += "\\fscx78\\fscy78\\t(0,90,\\fscx106\\fscy106)\\t(90,160,\\fscx100\\fscy100)"
            pre += "}"
            ev.append(f"Dialogue: 1,{_t(s)},{_t(e)},Cap,,0,0,0,,{pre}{' '.join(parts)}")
    if hook_title:
        ev.append(f"Dialogue: 2,{_t(0)},{_t(min(hook_seconds, duration))},Hook,,0,0,0,,"
                  f"{{\\an8\\pos({W // 2},250)\\fad(80,180)}}{_wrap(_esc(hook_title.upper()))}")
    if watermark:
        ev.append(f"Dialogue: 0,{_t(0)},{_t(duration)},Mark,,0,0,0,,{{\\an2\\pos({W // 2},1800)}}{_esc(watermark)}")
    return head + "\n".join(ev) + "\n"
