"""Environment checks shown in the CLI and the Settings page."""
from __future__ import annotations

import importlib.util
import os

from .util import EchoError, run, which


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
        except EchoError:
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
        has = bool(which("claude"))
        out.append(_ok("Claude CLI", True, "found" if has else "not installed, only needed for the studio and `echoclip run`; agents don't need it"))
    p = cfg["publish"]
    out.append(_ok("Instagram", bool(p["ig_user_id"] and p["access_token"]),
                   f"{p['mode']} login", "Optional. Add your IG user ID and token in Settings to post."))
    return out


def fix_all() -> list[str]:
    """Install what's missing. Python packages via pip; ffmpeg via the OS package manager."""
    import platform
    import subprocess
    import sys
    out = []
    for mod, pkg in (("yt_dlp", "yt-dlp"), ("faster_whisper", "faster-whisper"), ("mcp", "mcp")):
        if importlib.util.find_spec(mod) is None:
            out.append(f"Installing {pkg}…")
            p = subprocess.run([sys.executable, "-m", "pip", "install", "-U", pkg], capture_output=True, text=True)
            out.append("  done" if p.returncode == 0 else f"  failed: {p.stderr.strip()[-200:]}")
    if not which("ffmpeg"):
        sysname = platform.system()
        cmd = (["winget", "install", "-e", "--id", "Gyan.FFmpeg", "--accept-source-agreements", "--accept-package-agreements"]
               if sysname == "Windows" else ["brew", "install", "ffmpeg"] if sysname == "Darwin"
               else ["sudo", "apt-get", "install", "-y", "ffmpeg"])
        if which(cmd[0]):
            out.append("Installing ffmpeg… (this can take a minute)")
            p = subprocess.run(cmd, capture_output=True, text=True)
            out.append("  done. Close and reopen your terminal so it's found." if p.returncode == 0
                       else f"  failed: {(p.stderr or p.stdout).strip()[-200:]}")
        else:
            out.append(f"Install ffmpeg yourself: {' '.join(cmd)}")
    if not out:
        out.append("Nothing to fix.")
    return out
