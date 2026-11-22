"""Finding the wild seconds of a long video so Claude reads 10-minute notes instead of watching it all.
Every second gets a 0-1 score from three signals: the voice gets loud (against the last minute around it), the words
sound hyped (laughs, "no way", "oh my god", shouting), and the picture changes hard. The notes go to scan/chunk_NN.md."""
from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import numpy as np

from . import hearing
from .frames import video_info
from .paths import clock, ffmpeg, load, log, save

CHUNK_S = 600                            # one notes file per 10 minutes
TOP2, TOP1 = 97, 92                      # percentiles: 🔥🔥 = top 3 %, 🔥 = top 8 %
MARKS = {2: "🔥🔥", 1: "🔥", 0: ""}
W_LOUD, W_WORDS, W_PICTURE = 0.5, 0.3, 0.2
PIC_W = 160                              # width of the grey frames compared for picture change

LAUGH = re.compile(r"^(?:a?h?a(?:ha)+h?|(?:he){2,}h?|lmf?a+o+|lo+l|rofl|laugh\w*|chuckl\w*|giggl\w*)$")
PHRASES = {                              # normalised words in a row -> how much hype they signal
    ("oh", "my", "god"): 0.9, ("oh", "my", "gosh"): 0.9, ("omg",): 0.9, ("no", "way"): 0.9,
    ("what", "the"): 0.8, ("let's", "go"): 0.8, ("lets", "go"): 0.8, ("oh", "shit"): 0.9,
    ("holy", "shit"): 1.0, ("holy",): 0.5, ("oh", "fuck"): 0.9, ("wtf",): 0.9, ("oh", "no"): 0.7,
    ("are", "you", "kidding"): 0.8, ("you're", "kidding"): 0.8, ("i'm", "dead"): 0.8, ("insane",): 0.5,
    ("bro",): 0.4, ("bruh",): 0.5, ("dude",): 0.4, ("yo",): 0.3, ("run",): 0.4, ("help",): 0.4,
}
MAX_PHRASE = max(len(k) for k in PHRASES)
ACRONYMS = {"GTA", "NPC", "LSPD", "BCSO", "EMS", "CEO", "FIB", "USA", "SUV", "ATM", "OOC", "PD", "RP"}


def _norm(word: str) -> str:
    return re.sub(r"[^\w']+", "", word.lower()).strip("'")


def _rolling(x: np.ndarray, win: int, fn) -> np.ndarray:
    half = win // 2
    padded = np.pad(x, (half, half), mode="edge")
    return fn(np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1), axis=1)


def _jump(x: np.ndarray, floor: float) -> np.ndarray:
    """How far each value stands above the minute around it, in robust standard deviations."""
    med = _rolling(x, 61, np.median)
    mad = _rolling(np.abs(x - med), 61, np.median)
    return (x - med) / np.maximum(1.4826 * mad, floor)


def _loud(wav: Path | None, n: int) -> np.ndarray:
    per = np.full(n, -60.0)
    if wav and wav.exists():
        half = np.array(hearing.loudness(wav), dtype=float)
        if len(half) % 2:
            half = np.append(half, -90.0)
        if len(half):
            whole = half.reshape(-1, 2).max(axis=1)        # the louder half of each second
            k = min(len(whole), n)
            per[:k] = whole[:k]
    return _jump(np.maximum(per, -60.0), 3.0)


def _picture(video: Path, info: dict, n: int, cache: Path) -> np.ndarray:
    """Biggest mean grey-level difference between frames 0.5 s apart, per second (cached per video)."""
    st = video.stat()
    old = load(cache, {}) or {}
    if old.get("video") == str(video) and old.get("mtime") == st.st_mtime and old.get("size") == st.st_size \
            and len(old.get("diff", [])) == n:
        return np.array(old["diff"], dtype=float)
    sw, sh = info["w"] or 16, info["h"] or 9
    ph = max(2, int(round(PIC_W * sh / sw / 2)) * 2)
    size = PIC_W * ph
    cmd = [ffmpeg(), "-v", "error", "-i", str(video), "-an", "-sn", "-dn",
           "-vf", f"fps=2,scale={PIC_W}:{ph},format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    diff = np.zeros(n)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    prev, i, shown = None, 0, 0
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            cur = np.frombuffer(buf, dtype=np.uint8).astype(np.int16)
            if prev is not None and i // 2 < n:
                diff[i // 2] = max(diff[i // 2], float(np.abs(cur - prev).mean()))
            prev, i = cur, i + 1
            pct = int(100 * (i / 2) / max(n, 1))
            if pct >= shown + 25 and pct < 100:
                shown = pct - pct % 25
                log(f"    picture {shown}%")
    finally:
        proc.stdout.close()
        proc.wait()
    if i < 2:
        log("! could not read the picture: hot spots use sound and words only")
    save(cache, {"video": str(video), "mtime": st.st_mtime, "size": st.st_size, "diff": [round(d, 2) for d in diff]})
    return diff


def _words(words: list[dict], n: int, loud_z: np.ndarray) -> tuple[np.ndarray, list[set]]:
    """Hype from what is said, per second, plus why (laugh / hype words / shouting)."""
    acc = np.zeros(n)
    why: list[set] = [set() for _ in range(n)]

    def add(start: float, end: float, weight: float, tag: str):
        s0 = int(max(0, min(n - 1, math.floor(start))))
        s1 = int(max(s0, min(n - 1, math.ceil(end) - 1)))
        acc[s0:s1 + 1] += weight
        for s in range(s0, s1 + 1):
            why[s].add(tag)
        if s0 > 0:
            acc[s0 - 1] += weight / 2
        if s1 + 1 < n:
            acc[s1 + 1] += weight / 2

    toks = [_norm(w.get("w", "")) for w in words]
    for i, w in enumerate(words):
        raw, tok = str(w.get("w", "")), toks[i]
        start, end = float(w.get("start", 0)), float(w.get("end", 0))
        if not tok:
            continue
        if LAUGH.match(tok):
            add(start, end, 1.0, "laugh")
        for k in range(MAX_PHRASE, 0, -1):
            key = tuple(toks[i:i + k])
            if len(key) == k and key in PHRASES:
                add(start, float(words[i + k - 1].get("end", end)), PHRASES[key], "hype words")
                break
        if i >= 2 and len(tok) >= 2 and toks[i - 1] == tok and toks[i - 2] == tok:
            add(float(words[i - 2].get("start", start)), end, 0.6, "hype words")       # "no no no", "go go go"
        letters = re.sub(r"[^A-Za-z]", "", raw)
        if "!" in raw:
            add(start, end, 0.3, "shouting")
        if len(letters) >= 3 and letters.isupper() and letters not in ACRONYMS:
            add(start, end, 0.4, "shouting")
        s = int(min(n - 1, max(0, start)))
        if loud_z[s] >= 2.0:
            add(start, end, 0.5, "shouting")
    return 1.0 - np.exp(-acc), why


def _silences(wav: Path | None, min_s: float = 2.0, quiet_db: float = -50.0) -> list[tuple[float, float]]:
    """(start, end) seconds of every stretch of min_s or more with no real sound (Twitch mutes, dead air)."""
    if wav is None:
        return []
    step = 0.5
    levels = hearing.loudness(wav, step)
    out, start = [], None
    for i, v in enumerate(list(levels) + [0.0]):
        if v <= quiet_db:
            start = i if start is None else start
        elif start is not None:
            if (i - start) * step >= min_s:
                out.append((start * step, i * step))
            start = None
    return out


def scan(video: Path, project: Path) -> list[Path]:
    """Score every second, write project/scan/chunk_NN.md (one per 10 min), return their paths."""
    video, project = Path(video), Path(project)
    out = project / "scan"
    out.mkdir(parents=True, exist_ok=True)
    info = video_info(video)
    title = (load(project / "plan.json", {}) or {}).get("title") or video.stem
    words = load(project / "words.json", []) or []
    segs = load(project / "segments.json", []) or []
    n = max(1, int(math.ceil(info["dur"] or (words[-1]["end"] if words else 1))))
    log("  finding the hot spots…")

    wav = None
    if info["has_audio"]:
        wav = hearing.extract_wav(video, project / "audio.wav")
    silences = _silences(wav)
    loud_z = _loud(wav, n)
    loud = np.clip(loud_z / 3.0, 0, 1)
    hype, why = _words(words, n, loud_z)
    pic_z = _jump(_picture(video, info, n, out / "picture.json"), 1.0)
    pic = np.clip(pic_z / 4.0, 0, 1)

    raw = W_LOUD * loud + W_WORDS * hype + W_PICTURE * pic
    raw = 0.7 * raw + 0.3 * _rolling(raw, 3, np.mean)
    score = raw / raw.max() if raw.max() > 0 else raw
    t2, t1 = np.percentile(score, TOP2), np.percentile(score, TOP1)
    level = np.where((score >= t2) & (score > 0), 2, np.where((score >= t1) & (score > 0), 1, 0))
    save(out / "scores.json", {"score": [round(float(v), 3) for v in score],
                               "loud": [round(float(v), 3) for v in loud],
                               "words": [round(float(v), 3) for v in hype],
                               "picture": [round(float(v), 3) for v in pic]})

    def reasons(s: int) -> str:
        r = []
        if loud[s] >= 0.5:
            r.append("loud")
        r += sorted(why[s])
        if pic[s] >= 0.5:
            r.append("picture change")
        return " + ".join(r) or "busy"

    def said(s: int) -> str:
        txt = re.sub(r" (?=[-'.,!?])", "", " ".join(w["w"] for w in words if s - 2 <= float(w["start"]) < s + 3))
        return f'"{txt[:70]}{"…" if len(txt) > 70 else ""}"' if txt else "(no speech)"

    for old in out.glob("chunk_*.md"):
        old.unlink()
    n_chunks = max(1, int(math.ceil(n / CHUNK_S)))
    paths = []
    for c in range(n_chunks):
        lo, hi = c * CHUNK_S, min(n, (c + 1) * CHUNK_S)
        lines = [f"# Chunk {c + 1:02d} of {n_chunks:02d} · {clock(lo)}-{clock(hi)} · {title}",
                 "Legend: 🔥🔥 = top 3% wildest seconds, 🔥 = top 8% (loud voice, laughs, hype words, big picture "
                 "change); each line = m:ss in the video, marks, what is said.", ""]
        here = [g for g in segs if lo <= float(g["start"]) < hi]
        for g in here:
            s0 = int(min(n - 1, math.floor(float(g["start"]))))
            s1 = int(max(s0, min(n - 1, math.ceil(float(g["end"])) - 1)))
            mark = MARKS[int(level[s0:s1 + 1].max())]
            lines.append(" ".join(p for p in (clock(math.floor(float(g["start"]))), mark, g["text"].strip()) if p))
        if not here:
            lines.append("(nobody talks in this part)")
        quiet_here = [(q0, q1) for q0, q1 in silences if lo <= q0 < hi]
        if quiet_here:
            lines += ["", "🔇 Silent stretches (muted audio or dead air): keep every Short clear of these:"]
            lines += [f"- {clock(q0)}-{clock(q1)} ({q1 - q0:.0f} s)" for q0, q1 in quiet_here]
        lines += ["", "Hot seconds (best first, score 0-1):"]
        order = sorted(range(lo, hi), key=lambda s: -score[s])
        best: list[int] = []
        for s in order:                                   # the 10 best, at least 4 s apart
            if all(abs(s - b) >= 4 for b in best):
                best.append(s)
            if len(best) == 10:
                break
        for s in best:
            lines.append(f"- {clock(s)} {MARKS[int(level[s])] or '·'} {score[s]:.2f} · {reasons(s)} · {said(s)}")
        p = out / f"chunk_{c + 1:02d}.md"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        paths.append(p)
    log(f"✓ hot spots: {int((level == 2).sum())} seconds 🔥🔥, {int((level == 1).sum())} seconds 🔥, "
        f"{len(paths)} notes file{'s' if len(paths) != 1 else ''}")
    return paths
