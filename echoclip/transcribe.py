"""Word-level timestamps with faster-whisper. Loaded lazily and cached per model."""
from __future__ import annotations

from pathlib import Path

_models: dict = {}


def _device() -> str:
    try:
        import ctranslate2
        return "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except Exception:
        return "cpu"


def _pick(name: str) -> str:
    if name != "auto":
        return name
    return "large-v3" if _device() == "cuda" else "small"


def _model(name: str):
    name = _pick(name)
    if name not in _models:
        from faster_whisper import WhisperModel
        dev = _device()
        _models[name] = WhisperModel(name, device=dev, compute_type="float16" if dev == "cuda" else "int8")
    return _models[name]


def transcribe_words(path: Path, model: str = "auto", language: str | None = "en") -> list[dict]:
    segments, _ = _model(model).transcribe(
        str(path), language=language or None, word_timestamps=True, vad_filter=True,
        beam_size=5, condition_on_previous_text=False)
    words = []
    for seg in segments:
        for w in seg.words or []:
            tok = w.word.strip()
            if tok:
                words.append({"w": tok, "s": round(w.start, 3), "e": round(w.end, 3)})
    return words
