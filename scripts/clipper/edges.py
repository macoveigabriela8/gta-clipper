"""Where a Short may start and end: never inside a spoken word. One definition of "cuts a word" (inside_start /
inside_end), used by the edge snap AND by the QA (qc.clean_start / clean_end, top5.item_edges), so the snap and the
check can't disagree. Every edge the tool moves (silence trim, word snap, moving off a hard cut) ends in snap()."""
from __future__ import annotations

PAD_BEFORE = 0.12     # air kept before the first word when the start moves out of a word
PAD_AFTER = 0.2       # air kept after the last word when the end moves out of a word
MAX_MOVE = 1.5        # an edge moves outward (keeping the whole word) at most this far, else it drops the word


def timed(words: list[dict] | None) -> list[tuple[float, float, str]]:
    """(start, end, text) of every word with usable times, in time order."""
    out = []
    for w in words or []:
        try:
            out.append((float(w["start"]), float(w["end"]), str(w.get("w", ""))))
        except (KeyError, TypeError, ValueError):
            continue
    return sorted(out)


def inside_start(a: float, ws: float, we: float) -> bool:
    """A start at `a` cuts this word (more than 0.03 s of it is lost and more than 0.05 s of it is heard)."""
    return ws < a - 0.03 and we > a + 0.05


def inside_end(b: float, ws: float, we: float) -> bool:
    """An end at `b` cuts this word."""
    return ws < b - 0.05 and we > b + 0.03


def _gap_before(ws: float, prev_end: float | None) -> float:
    """A point in the silence before a word: PAD_BEFORE of air, or the middle of a shorter gap (never inside the
    word before it)."""
    if prev_end is None:
        return max(0.0, ws - PAD_BEFORE)
    return max(ws - PAD_BEFORE, (prev_end + ws) / 2)


def _gap_after(we: float, next_start: float | None, dur: float) -> float:
    """A point in the silence after a word: PAD_AFTER of air, or the middle of a shorter gap (never inside the
    next word)."""
    x = we + PAD_AFTER if next_start is None else min(we + PAD_AFTER, (we + max(we, next_start)) / 2)
    return min(x, dur) if dur else x


def snap(words: list[dict] | None, a: float, b: float, dur: float = 0.0, lo: float | None = None,
         hi: float | None = None) -> tuple[float, float, str]:
    """(a, b, note). An edge inside a word moves to the silence next to it: outward (the whole word stays) when that
    is at most MAX_MOVE away and stays within [lo, hi], inward (the word is dropped) otherwise. lo / hi: limits the
    edges may not cross (a hard cut the edge was just moved off)."""
    t = timed(words)
    na, nb = float(a), float(b)
    for _ in range(3):                    # whisper words can overlap a little: settle in a few passes
        moved = False
        for i, (ws, we, _) in enumerate(t):
            prev_end = t[i - 1][1] if i else None
            next_start = t[i + 1][0] if i + 1 < len(t) else None
            if inside_start(na, ws, we):
                out, inn = _gap_before(ws, prev_end), _gap_after(we, next_start, dur)
                go = out if na - out <= MAX_MOVE and (lo is None or out >= lo - 1e-6) else inn
                if abs(go - na) > 1e-6:
                    na, moved = go, True
            if inside_end(nb, ws, we):
                out, inn = _gap_after(we, next_start, dur), _gap_before(ws, prev_end)
                fits = out - nb <= MAX_MOVE and (hi is None or out <= hi + 1e-6) and (not dur or out <= dur)
                go = out if fits else inn
                if abs(go - nb) > 1e-6:
                    nb, moved = go, True
        if not moved:
            break
    if nb - na < 1.0:                     # never snap a moment away
        return a, b, ""
    said = [x for x, d in (("start", abs(na - a)), ("end", abs(nb - b))) if d > 0.01]
    return na, nb, ("Moved the " + " and ".join(said) + " so no word is cut in half.") if said else ""


def cut_words(words: list[dict] | None, a: float, b: float) -> tuple[str | None, str | None]:
    """The word cut in half at the start and at the end (None when clean), by the same rule snap() uses."""
    t = timed(words)
    start = next((w for ws, we, w in t if inside_start(a, ws, we)), None)
    end = next((w for ws, we, w in t if inside_end(b, ws, we)), None)
    return start, end
