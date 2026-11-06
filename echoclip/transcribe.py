"""Word-level timestamps with faster-whisper.

GPU when it works, CPU when it doesn't: a missing CUDA library falls back instead of failing.
No voice-activity filter: it drops speech over music, which is exactly where punchlines live."""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

_models: dict = {}
_cpu_only = False


def _nvidia_dlls() -> None:
    """pip's nvidia-cublas-cu12 / nvidia-cudnn-cu12 ship the DLLs; expose them to this process only."""
    if os.name != "nt":
        return
    for mod in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime", "nvidia.cuda_nvrtc"):
        try:
            spec = importlib.util.find_spec(mod)
        except (ImportError, ValueError):
            continue
        for base in (spec.submodule_search_locations or []) if spec else []:
            b = os.path.join(base, "bin")
            if os.path.isdir(b):
                try:
                    os.add_dll_directory(b)
                except OSError:
                    pass


def _device(pref: str = "auto") -> str:
    if _cpu_only or pref == "cpu":
        return "cpu"
    if pref == "cuda":
        return "cuda"
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def _pick(name: str, device: str) -> str:
    if name != "auto":
        return name
    return "turbo" if device == "cuda" else "small"


def _model(name: str, device: str):
    key = (name, device)
    if key not in _models:
        from faster_whisper import WhisperModel
        _models[key] = WhisperModel(name, device=device, compute_type="float16" if device == "cuda" else "int8")
    return _models[key]


def unload() -> None:
    """Drop loaded models so the GPU memory is free for the video encoder."""
    _models.clear()
    import gc
    gc.collect()


def _run(path: Path, name: str, device: str, language: str | None, vad: bool, prompt: str = "") -> list[dict]:
    segments, _ = _model(name, device).transcribe(
        str(path), language=language or None, word_timestamps=True, vad_filter=vad,
        beam_size=5, condition_on_previous_text=False,
        initial_prompt=prompt[:200] or None)    # the video title primes names like "Haaland"
    words = []
    for seg in segments:                       # the GPU actually runs here, so keep it inside the try
        for w in seg.words or []:
            tok = w.word.strip()
            if tok:
                words.append({"w": tok, "s": round(w.start, 3), "e": round(w.end, 3)})
    return words


def transcribe_words(path: Path, model: str = "auto", language: str | None = "en", device: str = "auto",
                     vad: bool = False, prompt: str = "") -> list[dict]:
    global _cpu_only
    _nvidia_dlls()
    dev = _device(device)
    name = _pick(model, dev)
    try:
        return _run(path, name, dev, language, vad, prompt)
    except Exception as e:
        if dev != "cuda":
            raise
        _cpu_only = True
        print(f"echo: GPU transcription failed ({str(e)[:120]}). Using the CPU instead. "
              "To use the GPU, run: pip install nvidia-cublas-cu12 nvidia-cudnn-cu12", file=sys.stderr)
        return _run(path, _pick("auto" if model in ("auto", "turbo", "large-v3", "large-v3-turbo") else model, "cpu"),
                    "cpu", language, vad, prompt)
