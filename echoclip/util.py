from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


class EchoError(RuntimeError):
    pass


def clean_env() -> dict:
    """PATH without Python package folders (torch, nvidia, ...). Their DLLs can crash ffmpeg on Windows."""
    env = dict(os.environ)
    parts = env.get("PATH", "").split(os.pathsep)
    bad = ("site-packages", "torch", "\\nvidia\\", "/nvidia/")
    env["PATH"] = os.pathsep.join(p for p in parts if not any(b in p.lower() for b in bad))
    return env


def run(cmd: list[str], cwd: Path | None = None, timeout: int | None = None, input: str | None = None) -> str:
    name = Path(cmd[0]).name.lower()
    env = clean_env() if name.startswith(("ffmpeg", "ffprobe")) else None
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                           input=input, encoding="utf-8", errors="replace", env=env)
    except FileNotFoundError as e:
        raise EchoError(f"Command not found: {cmd[0]}. Run `echoclip doctor`.") from e
    except subprocess.TimeoutExpired as e:
        raise EchoError(f"Timed out after {timeout}s: {' '.join(cmd[:3])}") from e
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()[-12:]
        if not tail:
            raise EchoError(f"{Path(cmd[0]).name} exited with code {p.returncode} and no message. It most likely "
                            "crashed loading a conflicting library. Run `echoclip doctor`.")
        raise EchoError(f"{Path(cmd[0]).name} failed:\n" + "\n".join(tail))
    return p.stdout


_JS: list[str] | None = None


def ytdlp() -> list[str]:
    """yt-dlp with a JavaScript runtime: YouTube now needs one to hand out video formats."""
    global _JS
    if _JS is None:
        _JS = []
        if not shutil.which("deno") and shutil.which("node"):
            try:
                helptext = subprocess.run([sys.executable, "-m", "yt_dlp", "--help"], capture_output=True,
                                          text=True, timeout=30).stdout
                if "--js-runtimes" in helptext:
                    _JS = ["--js-runtimes", "node"]
            except Exception:
                pass
    return [sys.executable, "-m", "yt_dlp", *_JS]


def probe(path: Path) -> dict:
    out = run(["ffprobe", "-v", "error", "-show_entries",
               "stream=codec_type,width,height,r_frame_rate:format=duration",
               "-of", "json", str(path)])
    d = json.loads(out)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    num, _, den = (v.get("r_frame_rate") or "30/1").partition("/")
    try:
        fps = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    return {"w": int(v.get("width", 0)), "h": int(v.get("height", 0)),
            "fps": fps, "dur": float(d.get("format", {}).get("duration", 0) or 0),
            "audio": any(s.get("codec_type") == "audio" for s in d.get("streams", []))}


def which(name: str) -> str | None:
    return shutil.which(name)


def ts(t: float) -> str:
    t = max(0.0, t)
    h, r = divmod(int(t), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def read_json(p: Path, default=None):
    try:
        return json.loads(Path(p).read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(p: Path, data) -> None:
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")
    tmp.replace(p)


class Timer:
    def __init__(self):
        self.t0 = time.perf_counter()

    def secs(self) -> float:
        return round(time.perf_counter() - self.t0, 1)
