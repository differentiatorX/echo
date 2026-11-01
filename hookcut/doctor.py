"""Environment checks shown in the CLI and the Settings page."""
from __future__ import annotations

import importlib.util
import os

from .util import HookcutError, run, which


def _ok(name: str, ok: bool, detail: str, fix: str = "") -> dict:
    return {"name": name, "ok": ok, "detail": detail, "fix": "" if ok else fix}


def checks() -> list[dict]:
    from .config import load_settings
    cfg = load_settings()
    out = []
    ff = which("ffmpeg")
    libass = False
    if ff:
        try:
            libass = " subtitles " in run(["ffmpeg", "-hide_banner", "-filters"], timeout=20)
        except HookcutError:
            pass
    out.append(_ok("ffmpeg", bool(ff and libass), "found with libass" if libass else "missing or no libass",
                   "Install a full ffmpeg build (Windows: `winget install Gyan.FFmpeg`, macOS: `brew install ffmpeg`)."))
    out.append(_ok("yt-dlp", importlib.util.find_spec("yt_dlp") is not None, "python module",
                   "pip install -U yt-dlp"))
    out.append(_ok("faster-whisper", importlib.util.find_spec("faster_whisper") is not None, "python module",
                   "pip install faster-whisper"))
    gpu = False
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    out.append(_ok("GPU transcription", True, "CUDA detected (large-v3)" if gpu else "CPU mode (small model)"))
    if cfg["llm"]["backend"] == "api":
        out.append(_ok("Claude", bool(os.environ.get("ANTHROPIC_API_KEY")), "Anthropic API",
                       "Set the ANTHROPIC_API_KEY environment variable."))
    else:
        out.append(_ok("Claude", bool(which("claude")), "Claude Code CLI (your subscription)",
                       "Install Claude Code, then run `claude` once to log in."))
    p = cfg["publish"]
    out.append(_ok("Instagram", bool(p["ig_user_id"] and p["access_token"]),
                   f"{p['mode']} login", "Optional. Add your IG user ID and token in Settings to post."))
    return out
