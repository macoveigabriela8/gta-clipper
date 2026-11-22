"""Everything drawn on top of a Short, made with Pillow as transparent PNGs (no libass / drawtext needed, so the
same code works with any ffmpeg build on Mac and Windows): the black hook pill and the word-by-word captions."""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .paths import CAPTION_FONT, FPS, HOOK_FONT, W

YELLOW = (255, 214, 10, 255)        # #FFD60A, the word being spoken
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)

# hook pill
HOOK_SIZE = 70
HOOK_MIN_SIZE = 40
HOOK_MAX_W = 980                     # the pill never gets wider than this
HOOK_PAD_X = 46
HOOK_PAD_Y = 30
HOOK_TOP = 60

# captions
CAP_SIZE = 105
CAP_MIN_SIZE = 64
CAP_STROKE = 9
CAP_MAX_W = 1000                     # widest a caption line may be (stroke included)
CAP_BAND_H = 300                     # every caption PNG is W x CAP_BAND_H, text centred in it
MAX_GROUP_WORDS = 3
MAX_GROUP_CHARS = 16
PAUSE_BREAK = 0.35                   # a pause longer than this starts a new group
HOLD_GAP = 0.7                       # keep a group up until the next one if the silence between is shorter
TAIL = 0.3                           # otherwise it stays this long after its last word


# ---------- fonts ----------

@lru_cache(maxsize=64)
def _font(path: str, size: int, black: bool = False) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size)
    if black:                         # Montserrat ships as a variable font whose default weight is Thin
        try:
            f.set_variation_by_name("Black")
        except Exception:
            try:
                f.set_variation_by_axes([900])
            except Exception:
                pass
    return f


def hook_font(size: int) -> ImageFont.FreeTypeFont:
    return _font(str(HOOK_FONT), size, True)


def caption_font(size: int) -> ImageFont.FreeTypeFont:
    return _font(str(CAPTION_FONT), size)


def _cap_height(font: ImageFont.FreeTypeFont) -> int:
    box = font.getbbox("H")
    return box[3] - box[1]


# ---------- hook pill ----------

def _fit_hook(words: list[str], size: int, room: float) -> list[str] | None:
    """The words on 1 line, else on 2 balanced lines, at this size; None when they don't fit."""
    f = hook_font(size)
    text = " ".join(words)
    if f.getlength(text) <= room:
        return [text]
    best = None
    for i in range(1, len(words)):
        l1, l2 = " ".join(words[:i]), " ".join(words[i:])
        widest = max(f.getlength(l1), f.getlength(l2))
        if best is None or widest < best[0]:
            best = (widest, [l1, l2])
    return best[1] if best and best[0] <= room else None


def _hook_lines(text: str) -> tuple[list[str], int]:
    """Biggest size (<= HOOK_SIZE) at which the hook fits in 1 line, else 2 balanced lines, inside the pill.
    A hook too long even at the smallest size loses words from the end (with "...") rather than spill out."""
    words = text.split()
    room = HOOK_MAX_W - 2 * HOOK_PAD_X
    for size in range(HOOK_SIZE, HOOK_MIN_SIZE - 1, -2):
        lines = _fit_hook(words, size, room)
        if lines:
            return lines, size
    for n in range(len(words) - 1, 0, -1):
        lines = _fit_hook(words[:n - 1] + [words[n - 1].rstrip(",.;:!?") + "..."], HOOK_MIN_SIZE, room)
        if lines:
            return lines, HOOK_MIN_SIZE
    return [text], HOOK_MIN_SIZE


def hook_png(text: str, out: Path) -> tuple[Path, int, int]:
    """Draw the hook pill. Returns (png, x, y) where to place it on the 1080 x 1920 frame."""
    text = " ".join(str(text).upper().split()) or " "
    lines, size = _hook_lines(text)
    f = hook_font(size)
    cap = _cap_height(f)
    gap = round(cap * 1.55)                       # baseline to baseline
    text_w = max(f.getlength(l) for l in lines)
    pill_w = min(W - 40, int(text_w + 2 * HOOK_PAD_X))
    pill_h = int(2 * HOOK_PAD_Y + cap + gap * (len(lines) - 1))
    pill_w += pill_w % 2
    pill_h += pill_h % 2
    radius = pill_h // 2 if len(lines) == 1 else round(cap * 0.85)
    ss = 2                                        # draw at 2x and shrink: smooth pill edges
    big = Image.new("RGBA", (pill_w * ss, pill_h * ss), (0, 0, 0, 0))
    ImageDraw.Draw(big).rounded_rectangle([0, 0, pill_w * ss - 1, pill_h * ss - 1], radius=radius * ss, fill=BLACK)
    img = big.resize((pill_w, pill_h), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    top_of_caps = f.getbbox("H")[1]
    for i, line in enumerate(lines):
        y_caps = HOOK_PAD_Y + i * gap               # where this line's capitals start
        d.text((pill_w / 2, y_caps - top_of_caps), line, font=f, fill=WHITE, anchor="ma")
    out = Path(out)
    img.save(out)
    return out, (W - pill_w) // 2, HOOK_TOP


# ---------- captions ----------

_PUNCT_END = re.compile(r"[.,!?;:…]+[\"')\]]*$")


def clean_word(w: str) -> str:
    """All caps, punctuation removed except apostrophes (curly ones become straight)."""
    w = str(w).replace("’", "'").replace("‘", "'")
    w = re.sub(r"[^\w']", "", w, flags=re.UNICODE).strip("'")
    return w.upper()


def clip_words(words: list[dict], a: float, b: float) -> list[dict]:
    """The words spoken inside [a, b], shifted to the clip's own time, cleaned; ends_phrase marks punctuation."""
    dur = b - a
    out = []
    for wd in words or []:
        try:
            s, e = float(wd["start"]), float(wd["end"])
        except (KeyError, TypeError, ValueError):
            continue
        mid = (s + e) / 2
        if mid < a or mid > b:
            continue
        text = clean_word(wd.get("w", ""))
        if not text:
            continue
        s, e = max(0.0, s - a), min(dur, e - a)
        if e <= s:
            e = min(dur, s + 0.08)
        out.append({"w": text, "start": s, "end": e, "ends_phrase": bool(_PUNCT_END.search(str(wd.get("w", ""))))})
    out.sort(key=lambda x: x["start"])
    for i in range(1, len(out)):                  # no overlaps: a word ends no later than the next one starts
        if out[i]["start"] < out[i - 1]["start"] + 1 / FPS:
            out[i]["start"] = out[i - 1]["start"] + 1 / FPS
        out[i - 1]["end"] = min(out[i - 1]["end"], out[i]["start"])
    return [w for w in out if w["start"] < dur]


def group_words(words: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            prev = cur[-1]
            text_len = len(" ".join(x["w"] for x in cur + [w]))
            if (len(cur) >= MAX_GROUP_WORDS or text_len > MAX_GROUP_CHARS or prev["ends_phrase"]
                    or w["start"] - prev["end"] > PAUSE_BREAK):
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def caption_states(words: list[dict], dur: float) -> list[tuple[float, float, list[str], int]]:
    """[(t0, t1, words_of_group, index_of_spoken_word)] in clip time, in order, not overlapping."""
    groups = group_words(words)
    states = []
    for gi, g in enumerate(groups):
        last_end = g[-1]["end"]
        nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else None
        if nxt is not None and nxt - last_end <= HOLD_GAP:
            g_end = nxt
        else:
            g_end = last_end + TAIL
            if nxt is not None:
                g_end = min(g_end, nxt)
        g_end = min(g_end, dur)
        texts = [w["w"] for w in g]
        for wi, w in enumerate(g):
            t0 = w["start"]
            t1 = g[wi + 1]["start"] if wi + 1 < len(g) else g_end
            if t1 > t0:
                states.append((t0, t1, texts, wi))
    return states


def caption_png(texts: list[str], spoken: int, out: Path) -> Path:
    """One caption state: the group's words on one line, the spoken one yellow, thick outline, soft shadow."""
    size = CAP_SIZE
    while True:
        f = caption_font(size)
        space = f.getlength(" ")
        widths = [f.getlength(t) for t in texts]
        line_w = sum(widths) + space * (len(texts) - 1) + 2 * CAP_STROKE
        if line_w <= CAP_MAX_W or size <= CAP_MIN_SIZE:
            break
        size -= 4
    cap_top, cap_bottom = f.getbbox("H")[1], f.getbbox("H")[3]
    y = CAP_BAND_H / 2 - (cap_top + cap_bottom) / 2   # capitals centred in the band
    x0 = (W - (line_w - 2 * CAP_STROKE)) / 2
    xs, x = [], x0
    for wd in widths:
        xs.append(x)
        x += wd + space

    shadow = Image.new("RGBA", (W, CAP_BAND_H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    for t, xx in zip(texts, xs):
        sd.text((xx, y + 7), t, font=f, fill=(0, 0, 0, 150), stroke_width=CAP_STROKE + 3, stroke_fill=(0, 0, 0, 150))
    shadow = shadow.filter(ImageFilter.GaussianBlur(8))

    ink = Image.new("RGBA", (W, CAP_BAND_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(ink)
    for t, xx in zip(texts, xs):                  # outlines first so a word's stroke never covers its neighbour
        d.text((xx, y), t, font=f, fill=BLACK, stroke_width=CAP_STROKE, stroke_fill=BLACK)
    for i, (t, xx) in enumerate(zip(texts, xs)):
        d.text((xx, y), t, font=f, fill=YELLOW if i == spoken else WHITE)
    img = Image.alpha_composite(shadow, ink)
    out = Path(out)
    img.save(out, optimize=False, compress_level=3)
    return out


def blank_png(out: Path) -> Path:
    Image.new("RGBA", (W, CAP_BAND_H), (0, 0, 0, 0)).save(out)
    return Path(out)


def caption_track(words: list[dict], a: float, b: float, work: Path) -> Path | None:
    """Write one PNG per caption state plus an ffmpeg concat list (with durations) that plays them in time.
    Returns the list file, or None when nobody speaks in the clip."""
    dur = b - a
    states = caption_states(clip_words(words, a, b), dur)
    if not states:
        return None
    work = Path(work)
    blank = blank_png(work / "cap_blank.png")
    q = lambda t: round(max(0.0, min(dur, t)) * FPS)       # snap to frames so nothing ends up 0 frames long
    timeline: list[tuple[int, int, Path]] = []
    cache: dict[tuple, Path] = {}
    cursor = 0
    for t0, t1, texts, spoken in states:
        f0, f1 = max(q(t0), cursor), q(t1)
        if f1 <= f0:
            continue
        if f0 > cursor:
            timeline.append((cursor, f0, blank))
        key = (tuple(texts), spoken)
        if key not in cache:
            cache[key] = caption_png(texts, spoken, work / f"cap_{len(cache):04d}.png")
        timeline.append((f0, f1, cache[key]))
        cursor = f1
    end_frame = round(dur * FPS) + FPS                     # run a second past the end so it never runs out
    timeline.append((cursor, end_frame, blank))
    lines = ["ffconcat version 1.0"]
    for f0, f1, png in timeline:
        lines += [f"file '{png.name}'", f"duration {(f1 - f0) / FPS:.6f}"]
    lines.append(f"file '{blank.name}'")                    # the concat demuxer ignores the last duration
    lst = work / "captions.ffconcat"
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lst
