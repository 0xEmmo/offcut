import os, subprocess, threading
import numpy as np
from pathlib import Path

_model = None
_lock = threading.Lock()  # whisper model isn't safe to share across concurrent jobs


def transcribe(path: Path, on_progress=None) -> list[dict]:
    with _lock:
        return _transcribe(path, on_progress)


def _transcribe(path, on_progress):
    """Word-level transcript: [{w, s, e}]"""
    global _model
    from faster_whisper import WhisperModel
    if _model is None:
        _model = WhisperModel(os.getenv("OFFCUT_WHISPER", "base"), device="cpu", compute_type="int8")
    # decode with ffmpeg ourselves; sidesteps PyAV version breakage
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         check=True, capture_output=True).stdout
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    segments, info = _model.transcribe(audio, word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            words.append({"w": w.word.strip(), "s": round(w.start, 2), "e": round(w.end, 2)})
        if on_progress and info.duration:
            on_progress(min(seg.end / info.duration, 1.0))
    return words
