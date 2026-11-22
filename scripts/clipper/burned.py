"""Text burned into the stream's own picture near the bottom: the game's subtitles, the streamer's caption overlay,
stream bars. Our captions would sit on top of them (double captions), so the gameplay crop stops above them.
Also used by the QA on the finished Short, to prove none is left."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .frames import grab_frames

LOOK_FROM = 0.68      # only the bottom 32% of the picture is searched (subtitles, caption bars, stream bars)
MAX_CUT = 0.30        # never cut more than the bottom 30% of the game
LINE_ROOM = 0.05      # subtitles grow upward to two lines: cut one more line above the highest one seen
SAME_ROW = 0.015      # two lines this close (share of the height) are the same row


def text_rows(img, x0: float = 0.0, x1: float = 1.0, top: float = LOOK_FROM) -> list[tuple[float, float]]:
    """(top, bottom) of every line of bright text in the lower part of one frame, as shares of its height,
    looking only between x0 and x1 (shares of the width). Only CENTRED lines count: subtitles and caption bars are
    centred; a sign in the game world (a shop name, "Wren's House" on a doghouse) usually isn't."""
    import cv2
    g = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    h, w = g.shape
    y0 = int(h * top)
    xa = max(0, int(w * x0))
    xb = min(w, max(xa + 16, int(w * x1)))
    band = g[y0:, xa:xb]
    if band.size == 0:
        return []
    k = max(5, int(w * 0.016)) | 1
    # thin bright strokes only (letters); big bright areas (sky, lights) drop out
    tophat = cv2.morphologyEx(band, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    ink = ((tophat > 70) & (band > 170)).astype(np.uint8) * 255
    joined = cv2.morphologyEx(ink, cv2.MORPH_CLOSE,
                              cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, int(w * 0.02)), 3)))
    n, _, st, _ = cv2.connectedComponentsWithStats(joined)
    rows = []
    for i in range(1, n):
        x, y, bw, bh, _ = (int(v) for v in st[i])
        centre = (xa + x + bw / 2) / w
        # 1.5-10% of the height: small game subtitles up to the big bold captions compilations burn in
        if h * 0.015 <= bh <= h * 0.10 and bw >= w * 0.05 and bw >= 2.5 * bh and abs(centre - 0.5) <= 0.12:
            fill = float(ink[y:y + bh, x:x + bw].mean()) / 255
            if 0.12 <= fill <= 0.6:                       # letters, not a solid bar or a lone speck
                rows.append(((y0 + y) / h, (y0 + y + bh) / h))
    return rows


def _steady_rows(per_frame: list[list[tuple[float, float]]]) -> tuple[list[float], int]:
    """Rows that sit at the same height in 2+ frames (snow, sparks and lights don't repeat): their tops, and how
    many frames show one."""
    tops = [(r[0], i) for i, rows in enumerate(per_frame) for r in rows]
    real, frames_with = [], set()
    for t, i in tops:
        same = {j for (u, j) in tops if abs(u - t) <= SAME_ROW}
        if len(same) >= 2:
            real.append(t)
            frames_with.add(i)
    return real, len(frames_with)


def sample_times(a: float, b: float, words: list[dict] | None, n: int = 16, most: int = 36) -> list[float]:
    """Even spread over a..b, plus one frame per spoken stretch (subtitles show while someone talks)."""
    times = [float(t) for t in np.linspace(a + 0.3, b - 0.3, n)] if b - a > 1 else [a]
    last = -9.0
    for w in words or []:
        try:
            mid = (float(w["start"]) + float(w["end"])) / 2
        except (KeyError, TypeError, ValueError):
            continue
        if a + 0.2 < mid < b - 0.2 and mid - last >= 0.8:
            times.append(mid + 0.15)
            last = mid
    times = sorted(set(round(t, 2) for t in times))
    if len(times) > most:
        times = [times[int(i)] for i in np.linspace(0, len(times) - 1, most)]
    return times


def find(video: Path, a: float, b: float, x0: float = 0.0, x1: float = 1.0, words: list[dict] | None = None,
         width: int = 960) -> dict | None:
    """Burned-in text at the bottom of the source between a and b: {"top", "frames", "of"} or None."""
    times = sample_times(a, b, words)
    frames = grab_frames(Path(video), times, width)
    if not frames:
        return None
    real, hit = _steady_rows([text_rows(f, x0, x1) for f in frames])
    if not real:
        return None
    return {"top": round(min(real), 3), "frames": hit, "of": len(frames)}


def keep_share(found: dict | None) -> float:
    """How much of the source's height the gameplay crop may use (1.0 = all of it)."""
    if not found:
        return 1.0
    return round(max(1.0 - MAX_CUT, found["top"] - LINE_ROOM), 3)


def left_in_short(mp4: Path, dur: float, skip: tuple[float, float] | None = None, top: float = 0.72) -> dict | None:
    """QA on the finished Short: text rows still showing low in the gameplay (under our captions)?"""
    times = [float(t) for t in np.linspace(0.4, max(0.5, dur - 0.4), 16)]
    times = [t for t in times if not (skip and skip[0] <= t <= skip[1])] or times   # not inside a reaction zoom
    frames = grab_frames(Path(mp4), times, 540)
    real, hit = _steady_rows([text_rows(f, 0.0, 1.0, top) for f in frames])
    if not real or hit < 2:
        return None
    return {"top": round(min(real), 3), "frames": hit, "of": len(frames)}
