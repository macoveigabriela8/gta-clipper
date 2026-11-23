"""What the gameplay crop must leave out of the stream's own picture:
- text burned in near the bottom: the game's subtitles, the streamer's caption overlay, stream bars. Our captions
  would sit on top of them (double captions), so the crop stops above them;
- black letterbox bars (cutscenes), at the top and the bottom.
Also used by the QA on the finished Short, to prove none is left (qc: no_double_captions, no_black_bars)."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .frames import grab_frames

LOOK_FROM = 0.68      # only the bottom 32% of the picture is searched (subtitles, caption bars, stream bars)
MAX_CUT = 0.30        # never cut more than the bottom 30% of the game
MAX_LINES = 3         # game subtitles grow UPWARD from a fixed bottom line, up to 3 lines (GTA cutscenes)
PITCH = 2.4           # line pitch is at least 2.4 x a line's letter height (GTA subtitles: 0.044 / 0.0185 = 2.38)
EDGE_ROOM = 0.012     # and a little air above the highest line
SAME_ROW = 0.015      # two lines this close (share of the height) are the same row
MIN_LETTER = 0.012    # letters at least this tall (share of the stream's height; small GTA subtitles measured 1.48%)
QA_LETTER = 0.008     # in the finished Short the same letters are smaller (9:16, scaled): 0.8% of its height
BAR_SHARE = (0.02, 0.20)   # a letterbox bar covers 2-20% of the height
BAR_DARK = 22         # dark pixels (0-255)
BAR_BLACK = (10, 24)  # a letterbox row is pure black: median under 10, 95% of its pixels under 24 (a night sky or a
                      # shadow has brighter specks: measured p95 52-101)
BAR_FILL = 0.7        # inside a bar, rows with subtitles drawn on it stay a bar: 70%+ dark, median black
BAR_EDGE = 12         # the picture right next to a bar is this much brighter...
BAR_OPEN = 0.5        # ...and starts across the whole width at once (at most 50% dark there: a fixed HUD or webcam
                      # under a black night sky does not)
BAR_SEEN = 0.25       # the same bar height (±0.004) in 25%+ of the frames: a cutscene's bars never move
# What counts as text (measured on real streams): letter strokes per letter-height of length, and how long the
# piece is for its height. A subtitle line (7.8-36x as long as tall) has 1.35-2.05 strokes; a bold caption word like
# "HEY" (2-5.6x) 1.9-2.1 (a plain-font "HEY" 1.43). Things that are not text: a jet's wing (2-2.8x) 0.5-1.2, an
# airboat's cage bar (~3.4x) ~0.3, a car's window trim (~10x) 0.1.
TEXT_MIN_ASPECT = 1.8      # a word is at least this long for its height
LONG_LINE = 5.0            # a piece this long counts as a line of text with TEXT_STROKES...
TEXT_STROKES = 0.8
WORD_STROKES = 1.35        # ...a shorter piece (one bold word) needs more strokes (between the wing and a word)
BIG_LETTER = 0.035         # letters this tall (share of the height) are a streamer's / editor's bold caption: one line,
                           # it doesn't grow upward like the game's small subtitles, so no room for extra lines
QA_EVERY = 0.5        # the QA on the finished Short looks at a frame every 0.5 s (a 2 s subtitle can't slip by)
QA_MOST = 90


def text_rows(img, x0: float = 0.0, x1: float = 1.0, top: float = LOOK_FROM, min_h: float = MIN_LETTER
              ) -> list[tuple[float, float]]:
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
        # min_h-10% of the height: small game subtitles up to the big bold captions compilations burn in
        if h * min_h <= bh <= h * 0.10 and bw >= w * 0.03 and bw >= TEXT_MIN_ASPECT * bh and abs(centre - 0.5) <= 0.12:
            box = ink[y:y + bh, x:x + bw] > 0
            fill = float(box.mean())
            need = TEXT_STROKES if bw >= LONG_LINE * bh else WORD_STROKES
            if 0.12 <= fill <= 0.6 and strokes(box) >= need:   # letters, not a bar, a speck, a wing or a long edge
                rows.append(((y0 + y) / h, (y0 + y + bh) / h))
    return rows


def strokes(box: np.ndarray) -> float:
    """Letter strokes along a line of ink: the typical number of separate ink runs in a row, per letter-height of
    length. Text crosses many short strokes; a wing, a bar or a window edge is one long one."""
    bh, bw = box.shape
    rises = (np.diff(box.astype(np.int8), axis=1) == 1).sum(axis=1) + box[:, 0]
    busy = rises[rises > 0]
    return float(np.median(busy)) / (bw / bh) if busy.size and bw else 0.0


def _lines(per_frame: list[list[tuple[float, float]]]) -> list[dict]:
    """Rows that sit at the same height in 2+ frames (snow, sparks and lights don't repeat), top to bottom:
    {"top", "h" (letter height), "frames"}."""
    clusters: list[dict] = []
    for top, bottom, i in sorted((r[0], r[1], i) for i, rows in enumerate(per_frame) for r in rows):
        for c in clusters:
            if abs(c["top"] - top) <= SAME_ROW:
                c["hs"].append(bottom - top)
                c["frames"].add(i)
                break
        else:
            clusters.append({"top": top, "hs": [bottom - top], "frames": {i}})
    return [{"top": c["top"], "h": float(np.median(c["hs"])), "frames": c["frames"]}
            for c in clusters if len(c["frames"]) >= 2]


def _steady_rows(per_frame: list[list[tuple[float, float]]]) -> tuple[list[float], int]:
    """(tops of the steady rows, how many frames show one)."""
    lines = _lines(per_frame)
    return [l["top"] for l in lines], len(set().union(*(l["frames"] for l in lines))) if lines else 0


def subtitle_cut(lines: list[dict]) -> float:
    """Where the crop must stop (share of the height) so a subtitle block of up to MAX_LINES lines stays out.
    The bottom line is fixed; the upper ones are often missed (white letters over bright water or sky), so room is
    made for MAX_LINES lines above the bottom one, at the measured line pitch (or PITCH x the letter height)."""
    tops = [l["top"] for l in lines]
    h = float(np.median([l["h"] for l in lines]))
    if h >= BIG_LETTER:                                           # a bold caption: one line, cut just above it
        return min(tops) - EDGE_ROOM
    gaps = [b - a for a, b in zip(tops, tops[1:]) if 1.3 * h <= b - a <= 3.5 * h]
    pitch = max(float(np.median(gaps)) if gaps else 0.0, PITCH * h)      # the safer (bigger) of the two
    return min(min(tops), max(tops) - (MAX_LINES - 1) * pitch) - EDGE_ROOM


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


def _subs(frames, x0: float, x1: float) -> dict | None:
    lines = _lines([text_rows(f, x0, x1) for f in frames])
    if not lines:
        return None
    return {"top": round(min(l["top"] for l in lines), 3), "cut": round(subtitle_cut(lines), 3), "lines": len(lines),
            "frames": len(set().union(*(l["frames"] for l in lines))), "of": len(frames)}


def bar_rows(img, x0: float = 0.0, x1: float = 1.0) -> tuple[float, float] | None:
    """(top, bottom) letterbox bar of one frame as shares of its height (0.0 = none), looking only between x0 and x1
    (the game's columns, off the webcam), or None for a frame that is black or fading (it says nothing about bars).
    A bar: pure black rows from the picture's edge (subtitles may be drawn on it), then a hard straight edge where the
    picture starts across the whole width."""
    import cv2
    g = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY).astype(np.float32)
    h, w = g.shape
    g = g[:, max(0, int(w * x0)):max(int(w * x0) + 16, int(w * x1))]
    if g.mean() < 25:
        return None
    med, p95 = np.median(g, axis=1), np.percentile(g, 95, axis=1)
    black = (med < BAR_BLACK[0]) & (p95 < BAR_BLACK[1])
    dark = (g < BAR_DARK).mean(axis=1)
    inked = (med < BAR_BLACK[0]) & (dark >= BAR_FILL)          # a bar row with subtitle letters on it
    edge_rows = max(2, int(h * BAR_SHARE[0] * 0.75))

    def run(rows: list[int]) -> int:
        if not all(black[y] for y in rows[:edge_rows]):          # the edge itself must be pure black
            return 0
        n = 0
        for y in rows:
            if not (black[y] or inked[y]):
                break
            n += 1
        return n

    def bar(n: int, top: bool) -> float:
        if not BAR_SHARE[0] * h <= n <= BAR_SHARE[1] * h:
            return 0.0
        inner = g[n:n + 4] if top else g[h - n - 4:h - n]
        bars = g[:n] if top else g[h - n:]
        hard = inner.mean() - bars.mean() >= BAR_EDGE and float((inner < BAR_DARK).mean()) <= BAR_OPEN
        return n / h if hard else 0.0

    return bar(run(list(range(h))), True), bar(run(list(range(h - 1, -1, -1))), False)


def _steady_bar(values: list[float], of: int) -> float:
    """The bar seen at the same height (±0.004) in BAR_SEEN of the frames (2 at least), else 0."""
    seen = sorted(v for v in values if v > 0)
    best: list[float] = []
    for v in seen:
        group = [u for u in seen if abs(u - v) <= 0.004]
        if len(group) > len(best):
            best = group
    return round(max(best), 3) if len(best) >= max(2, BAR_SEEN * of) else 0.0


def _bars(frames, x0: float = 0.0, x1: float = 1.0) -> dict:
    per = [r for r in (bar_rows(f, x0, x1) for f in frames) if r is not None]
    return {"top": _steady_bar([t for t, _ in per], len(per)), "bottom": _steady_bar([b for _, b in per], len(per)),
            "frames": sum(1 for t, b in per if t or b), "of": len(frames)}


def look(video: Path, a: float, b: float, x0: float = 0.0, x1: float = 1.0, words: list[dict] | None = None,
         width: int = 960) -> dict:
    """One read of the moment's frames: {"subs": burned-in text at the bottom (or None), "bars": letterbox bars}."""
    frames = grab_frames(Path(video), sample_times(a, b, words), width)
    if not frames:
        return {"subs": None, "bars": {"top": 0.0, "bottom": 0.0, "frames": 0, "of": 0}}
    return {"subs": _subs(frames, x0, x1), "bars": _bars(frames, x0, x1)}


def find(video: Path, a: float, b: float, x0: float = 0.0, x1: float = 1.0, words: list[dict] | None = None,
         width: int = 960) -> dict | None:
    """Burned-in text at the bottom of the source between a and b: {"top", "cut", "lines", "frames", "of"} or None."""
    return look(video, a, b, x0, x1, words, width)["subs"]


def keep_share(found: dict | None) -> float:
    """How much of the source's height the gameplay crop may use (1.0 = all of it)."""
    if not found:
        return 1.0
    return round(max(1.0 - MAX_CUT, found["cut"]), 3)


def _qa_times(dur: float, skip: tuple[float, float] | None) -> list[float]:
    times = [float(t) for t in np.arange(0.4, max(0.5, dur - 0.4), QA_EVERY)]
    if len(times) > QA_MOST:
        times = [times[int(i)] for i in np.linspace(0, len(times) - 1, QA_MOST)]
    return [t for t in times if not (skip and skip[0] <= t <= skip[1])] or times   # not inside a reaction zoom


def left_in_short(mp4: Path, dur: float, skip: tuple[float, float] | None = None, top: float = 0.72) -> dict | None:
    """QA on the finished Short: text rows still showing low in the gameplay (under our captions)? A frame every
    QA_EVERY s, so a subtitle that shows for 2 s is seen in 2+ frames."""
    frames = grab_frames(Path(mp4), _qa_times(dur, skip), 720)
    real, hit = _steady_rows([text_rows(f, 0.0, 1.0, top, QA_LETTER) for f in frames])
    if not real or hit < 2:
        return None
    return {"top": round(min(real), 3), "frames": hit, "of": len(frames)}


def bars_in_short(mp4: Path, dur: float, band_top: int, skip: tuple[float, float] | None = None) -> dict | None:
    """QA on the finished Short: a black letterbox bar at the top or bottom of the gameplay (below the webcam band
    when there is one)? {"top", "bottom" (shares of the gameplay's height), "frames", "of"} or None."""
    times = [float(t) for t in np.linspace(0.4, max(0.5, dur - 0.4), 16)]
    times = [t for t in times if not (skip and skip[0] <= t <= skip[1])] or times
    frames = grab_frames(Path(mp4), times, 540)
    if not frames:
        return None
    y0 = round(frames[0].height * band_top / 1920)
    found = _bars([f.crop((0, y0, f.width, f.height)) for f in frames])
    return found if found["top"] or found["bottom"] else None
