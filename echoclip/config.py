from __future__ import annotations

import copy
import json
import os
from pathlib import Path

PKG = Path(__file__).parent
FONTS = PKG / "assets" / "fonts"
PROMPTS = PKG / "prompts"
WEB = PKG / "web"

HOME = Path(os.environ.get("ECHO_HOME", Path.home() / ".echo"))
JOBS = HOME / "jobs"
UPLOADS = HOME / "uploads"
SETTINGS_PATH = HOME / "settings.json"
QUEUE_PATH = HOME / "queue.json"
POSTED_PATH = HOME / "posted.json"
FEEDBACK = HOME / "feedback"

DEFAULTS: dict = {
    "llm": {
        "backend": "claude-code",          # claude-code | api
        "cli_model": "sonnet",             # alias passed to `claude -p --model`
        "api_model": "claude-sonnet-5-5",  # used when backend = api (needs ANTHROPIC_API_KEY)
        "timeout": 600,
    },
    "clips": {"count": 4, "min_len": 25, "max_len": 55, "candidates": 12, "topic": ""},
    "language": "en",
    "whisper": {"model": "auto", "rough_model": "base", "device": "auto", "vad": False},
    "signals": {"heatmap": True, "comments": True, "max_comments": 400},
    "weights": {"llm": 0.6, "heat": 0.25, "crowd": 0.1, "pace": 0.05},
    "edit": {
        "remove_silence": True, "max_gap": 0.35, "keep_gap": 0.08, "quiet_ratio": 0.35,
        "remove_fillers": True, "cold_open": True, "cold_open_repeat": False,
        "punch": "cuts", "punch_zoom": 1.08, "punch_every": 3.2,
    },
    "style": {
        "preset": "echo", "hook_title": True, "hook_seconds": 2.6,
        "progress_bar": True, "watermark": "",
        "logo": "",                       # path to a PNG shown top-right (brand kit)
        "sfx": True,                      # whoosh on the cold-open cut, soft pops on keywords
        "sfx_volume": 0.35,
        "music": "",                      # an audio file or a folder of them; ducked under the voice
        "music_volume": 0.18,
    },
    "render": {"encoder": "auto", "crf": 20, "fps": 30, "aspect": "9:16"},
    "reframe": {"mode": "auto", "deadzone": .035, "max_pan": .25},   # auto = full-screen tracking; fit = letterbox
    "publish": {
        "mode": "facebook",               # facebook (local upload) | instagram (needs public_base_url)
        "ig_user_id": "", "access_token": "", "api_version": "v26.0",
        "public_base_url": "", "trial": False, "graduation": "SS_PERFORMANCE",
        "ai_label": False,
    },
    "server": {"host": "127.0.0.1", "port": 7777},
}


LENGTHS = {"short": (15, 30), "medium": (25, 55), "long": (45, 90)}
ASPECTS = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _migrate() -> None:
    """Move data from the old Hookcut folder the first time Echo runs."""
    old = Path.home() / ".hookcut"
    if "ECHO_HOME" not in os.environ and old.exists() and not HOME.exists():
        try:
            old.rename(HOME)
        except OSError:
            pass


def ensure_dirs() -> None:
    _migrate()
    for d in (HOME, JOBS, UPLOADS, FEEDBACK):
        d.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    ensure_dirs()
    user = {}
    if SETTINGS_PATH.exists():
        try:
            user = json.loads(SETTINGS_PATH.read_text("utf-8"))
        except json.JSONDecodeError:
            user = {}
    s = _merge(DEFAULTS, user)
    if os.environ.get("IG_ACCESS_TOKEN"):
        s["publish"]["access_token"] = os.environ["IG_ACCESS_TOKEN"]
    if os.environ.get("IG_USER_ID"):
        s["publish"]["ig_user_id"] = os.environ["IG_USER_ID"]
    return s


def save_settings(new: dict) -> dict:
    ensure_dirs()
    merged = _merge(load_settings(), new)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2), "utf-8")
    return merged
