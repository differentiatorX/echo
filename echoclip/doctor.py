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
    cv_ok, cv_detail = False, "not installed"
    try:
        import cv2
        cv_ok = hasattr(cv2, "CascadeClassifier") and int(cv2.__version__.split(".")[0]) < 5
        cv_detail = f"OpenCV {cv2.__version__}" + ("" if cv_ok else ", face framing needs OpenCV 4")
    except Exception:
        pass
    out.append(_ok("opencv", cv_ok, cv_detail, 'pip install "opencv-python-headless<5" "opencv-python<5"'))
    js = which("deno") or which("node")
    out.append(_ok("JS runtime", bool(js), "found" if js else "missing: YouTube may send audio only",
                   "winget install OpenJS.NodeJS.LTS   (macOS: brew install node)"))
    gpu = False
    try:
        import ctranslate2
        gpu = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        pass
    libs = importlib.util.find_spec("nvidia") is not None
    detail = "CPU mode (small model)"
    if gpu:
        detail = "CUDA detected (turbo model)" + ("" if libs or os.name != "nt" else
                 "; if it errors about cublas, Echo falls back to CPU. For speed: pip install -e .[gpu]")
    out.append(_ok("GPU transcription", True, detail))
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
    def pip(*pkgs):
        out.append(f"Installing {' '.join(pkgs)}…")
        p = subprocess.run([sys.executable, "-m", "pip", "install", "-U", *pkgs], capture_output=True, text=True)
        out.append("  done" if p.returncode == 0 else f"  failed: {p.stderr.strip()[-200:]}")

    for mod, pkg in (("yt_dlp", "yt-dlp[default]"), ("faster_whisper", "faster-whisper"), ("mcp", "mcp")):
        if importlib.util.find_spec(mod) is None:
            pip(pkg)
    if importlib.util.find_spec("yt_dlp") and importlib.util.find_spec("yt_dlp_ejs") is None:
        pip("yt-dlp[default]")                    # adds yt-dlp-ejs, which solves YouTube's player challenges
    if which("nvidia-smi") and importlib.util.find_spec("nvidia.cublas") is None:
        pip("nvidia-cublas-cu12", "nvidia-cudnn-cu12<10")   # CUDA 12 libraries faster-whisper needs on the GPU
    try:
        import cv2
        if not hasattr(cv2, "CascadeClassifier") or int(cv2.__version__.split(".")[0]) >= 5:
            pip("opencv-python-headless<5", "opencv-python<5")
    except Exception:
        pip("opencv-python-headless<5")
    if not (which("deno") or which("node")):
        if platform.system() == "Windows" and which("winget"):
            out.append("Installing Node.js for YouTube downloads…")
            p = subprocess.run(["winget", "install", "-e", "--id", "OpenJS.NodeJS.LTS", "--accept-source-agreements",
                                "--accept-package-agreements"], capture_output=True, text=True)
            out.append("  done. Reopen the terminal." if p.returncode == 0 else "  failed; install Node.js from nodejs.org")
        else:
            out.append("Install Node.js (nodejs.org) so yt-dlp can download YouTube video.")
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
