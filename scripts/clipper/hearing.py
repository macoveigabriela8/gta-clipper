"""The ears: what is said and when (faster-whisper on the CPU), and how loud each half second is.
Audio goes to faster-whisper as a numpy array we read ourselves: its own file decoder breaks with the newest PyAV."""
from __future__ import annotations

import math
import os
import re
import time
import warnings
import wave
from pathlib import Path

import numpy as np

from .frames import video_info
from .paths import MODELS, ffmpeg, load, log, run, save, setting

RATE = 16000
_models: dict = {}


def load_model(name: str | None = None):
    """The faster-whisper model (CPU, int8). Downloaded once into MODELS/whisper, then read from there."""
    name = name or setting("whisper_model")
    if name not in _models:
        from faster_whisper import WhisperModel
        root = MODELS / "whisper"
        root.mkdir(parents=True, exist_ok=True)
        _models[name] = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(root),
                                     cpu_threads=max(1, min(8, os.cpu_count() or 4)))
    return _models[name]


def extract_wav(video: Path, wav: Path) -> Path:
    """16 kHz mono 16-bit wav of the video's sound (reused when it is newer than the video)."""
    video, wav = Path(video), Path(wav)
    if wav.exists() and wav.stat().st_mtime >= video.stat().st_mtime and wav.stat().st_size > 1000:
        return wav
    wav.parent.mkdir(parents=True, exist_ok=True)
    tmp = wav.with_name(wav.stem + ".part.wav")
    run([ffmpeg(), "-y", "-v", "error", "-i", video, "-vn", "-sn", "-ac", "1", "-ar", str(RATE),
         "-c:a", "pcm_s16le", tmp])
    os.replace(tmp, wav)
    return wav


def read_wav(wav: Path) -> np.ndarray:
    """float32 samples in -1..1."""
    with wave.open(str(wav), "rb") as f:
        raw = f.readframes(f.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0


def loudness(wav: Path, step: float = 0.5) -> list[float]:
    """RMS level in dB (0 = full scale, floor -90) for every `step` seconds of the wav."""
    out = []
    with wave.open(str(wav), "rb") as f:
        rate, width = f.getframerate(), f.getsampwidth()
        block = max(1, int(round(rate * step)))
        while True:
            raw = f.readframes(block)
            if not raw:
                break
            x = np.frombuffer(raw, dtype="<i2" if width == 2 else np.uint8).astype(np.float64)
            x = x / 32768.0 if width == 2 else (x - 128) / 128.0
            rms = math.sqrt(float(np.mean(x * x))) if x.size else 0.0
            out.append(round(max(-90.0, 20 * math.log10(rms + 1e-12)), 2))
    return out


def _split(toks: list[tuple[str, float, float]]) -> list[list[tuple[str, float, float]]]:
    """One whisper segment can run 30 s or more: cut it into lines at pauses and sentence ends (max ~12 s each)."""
    groups, cur = [], []
    for tok in toks:
        if cur:
            gap, span = tok[1] - cur[-1][2], cur[-1][2] - cur[0][1]
            sentence_end = cur[-1][0].rstrip().endswith((".", "?", "!"))
            if gap > 1.5 or (sentence_end and span >= 5.0) or span >= 12.0:
                groups.append(cur)
                cur = []
        cur.append(tok)
    if cur:
        groups.append(cur)
    return groups


def _piece(group: list[tuple[str, float, float]]) -> tuple[dict, list[dict]]:
    line = {"start": round(group[0][1], 2), "end": round(group[-1][2], 2),
            "text": "".join(t for t, _, _ in group).strip()}
    return line, [{"w": t.strip(), "start": round(a, 2), "end": round(b, 2)} for t, a, b in group]


def _drop_loops(pieces: list[tuple[dict, list[dict]]]) -> list[tuple[dict, list[dict]]]:
    """Whisper sometimes loops on one line ("He left her." x7 in two seconds): keep at most two in a row."""
    out, keys, prev_end = [], [], -9.0
    for line, ws in pieces:
        key = re.sub(r"[^\w']+", " ", line["text"].lower()).strip()
        looping = len(keys) >= 2 and keys[-1] == key and keys[-2] == key and line["start"] - prev_end < 2.0
        prev_end = line["end"]
        if not looping:
            out.append((line, ws))
            keys.append(key)
    return out


def transcribe(video: Path, project: Path) -> list[dict]:
    """Every word with its time: project/words.json = [{"w", "start", "end"}] in seconds of the video, plus
    project/segments.json = [{"start", "end", "text"}]. Cached: skipped when words.json is newer than the video."""
    video, project = Path(video), Path(project)
    words_f, segs_f = project / "words.json", project / "segments.json"
    if words_f.exists() and words_f.stat().st_mtime >= video.stat().st_mtime:
        words = load(words_f, None)
        if isinstance(words, list):
            log(f"✓ words already written ({len(words)} words): {words_f}")
            return words
    info = video_info(video)
    if not info["has_audio"]:
        log("! this video has no sound: no words, no captions (the hot spots use the picture only)")
        save(words_f, [])
        save(segs_f, [])
        return []
    t0 = time.time()
    log("  listening: pulling the sound out…")
    audio = read_wav(extract_wav(video, project / "audio.wav"))
    total = len(audio) / RATE
    name = setting("whisper_model")
    model = load_model(name)
    log(f"  listening with {name} ({total / 60:.1f} min of sound)…")
    pieces, shown = [], 0
    with warnings.catch_warnings():         # numpy on Apple's Accelerate warns about harmless float flags
        warnings.simplefilter("ignore", RuntimeWarning)
        seg_iter, _ = model.transcribe(audio, beam_size=5, word_timestamps=True, vad_filter=True,
                                       vad_parameters={"min_silence_duration_ms": 500},
                                       condition_on_previous_text=False)
        for s in seg_iter:
            toks = [(w.word or "", float(w.start), float(w.end)) for w in (s.words or []) if (w.word or "").strip()]
            if toks:
                pieces += [_piece(group) for group in _split(toks)]
            elif (s.text or "").strip():
                pieces.append(({"start": round(float(s.start), 2), "end": round(float(s.end), 2),
                                "text": s.text.strip()}, []))
            pct = int(100 * min(1.0, float(s.end) / max(total, 1.0)))
            if pct >= shown + 20:
                shown = pct - pct % 20
                log(f"    {shown}%")
    pieces = _drop_loops(pieces)
    segs = [line for line, _ in pieces]
    words = [w for _, ws in pieces for w in ws]
    save(words_f, words)
    save(segs_f, segs)
    log(f"✓ heard {len(words)} words in {len(segs)} lines ({time.time() - t0:.0f} s): {words_f}")
    return words
