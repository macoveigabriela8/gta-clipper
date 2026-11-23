# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["numpy<2.3", "opencv-python-headless>=4.8,<5", "pillow", "imageio-ffmpeg"]
# ///
"""Fast checks that need no video: the SOP and the code agree, every check is written one way, and the edge snap,
subtitle room, letterbox finder and webcam rectangle behave on made-up cases built from real measurements.
Run: uv run --python 3.12 tests/test_rules.py   (prints each test; exits 1 on any failure)"""
from __future__ import annotations

import random
import re
import sys
import traceback
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from clipper import burned, edges, facecam, qc  # noqa: E402

LEVEL = {"✗": "fail", "!": "warn"}


def sop_levels() -> dict[str, set[str]]:
    """Every check id the SOP gives a level to, with the level(s) it says."""
    found: dict[str, set[str]] = {}
    for line in (ROOT / "reference" / "cutting-sop.md").read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")] if line.startswith("|") else []
        if len(cells) >= 2 and cells[-1] in LEVEL:                       # § 4 table: | `id` | rule | ✗ |
            for cid in re.findall(r"`([a-z_]+)`", cells[0]):
                found.setdefault(cid, set()).add(LEVEL[cells[-1]])
        for sym, cid in re.findall(r"([✗!]) `([a-z_]+)`", line):        # ✗ `payoff`
            found.setdefault(cid, set()).add(LEVEL[sym])
        for cid, sym in re.findall(r"`([a-z_]+)` \(?([✗!])\)?", line):  # `webcam` ✗  /  `talking` (!)
            found.setdefault(cid, set()).add(LEVEL[sym])
    return found


def test_sop_matches_code():
    said = sop_levels()
    wrong = {c: (sorted(l), qc.RULES.get(c)) for c, l in said.items() if c in qc.RULES and l != {qc.RULES[c]}}
    unknown = sorted(c for c in said if c not in qc.RULES and c not in ("clean_", "cam", "drop_bottom", "drop_top"))
    missing = sorted(c for c in qc.RULES if c not in said)
    assert not wrong, f"SOP level differs from qc.RULES: {wrong}"
    assert not unknown, f"SOP names checks the code doesn't have: {unknown}"
    assert not missing, f"checks with no level in the SOP: {missing}"


def test_every_check_goes_through_result():
    for f in (ROOT / "scripts" / "clipper").glob("*.py"):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if '"level":' in line and f.name != "qc.py":
                raise AssertionError(f"{f.name}:{n} builds a check by hand; use qc.result()")
    for f in (ROOT / "scripts" / "clipper").glob("*.py"):
        for cid in re.findall(r'(?:add|result)\("([a-z_]+)"', f.read_text(encoding="utf-8")):
            assert cid in qc.RULES, f"{f.name}: check {cid!r} has no level in qc.RULES"


def test_passing_checks_say_nothing():
    assert qc.result("size", True, "The picture is 1080x1920, not 1080x1920.")["say"] == ""
    bad = qc.result("size", False, "The picture is 720x1280, not 1080x1920.")
    assert bad["say"] and bad["level"] == "fail"
    assert qc.notes_of([qc.result("peak", False, "Peaks hit -0.1 dB.")]) == ["! Peaks hit -0.1 dB."]


def test_snap_never_lands_in_a_word():
    # real words from a stream (an airboat chase): the old snap moved an end at 655.4 to 656.07, inside "want"
    w = [{"w": t, "start": s, "end": e} for t, s, e in [
        ("Match", 654.57, 654.87), ("him", 654.87, 655.11), ("up", 655.11, 655.27), ("I", 655.27, 655.87),
        ("want", 655.87, 656.13), ("to", 656.13, 656.33)]]
    a, b, note = edges.snap(w, 640.0, 655.4, 1185.0)
    assert edges.cut_words(w, a, b) == (None, None) and note, (a, b)
    assert b <= 655.87 + 1e-6, b
    _, b2, _ = edges.snap(w, 640.0, 655.4, 1185.0, hi=655.5)       # just moved off a cut at 655.6: may not go past
    assert b2 <= 655.5 and edges.cut_words(w, 640.0, b2)[1] is None, b2
    rnd = random.Random(7)
    for _ in range(300):                                             # any edge, any word layout: never inside a word
        t, words = 0.0, []
        for _ in range(40):
            t += rnd.choice([0.0, 0.02, 0.1, 0.4, 1.2])
            d = rnd.uniform(0.08, 0.7)
            words.append({"w": "x", "start": t, "end": t + d})
            t += d
        a0, b0 = rnd.uniform(1, t / 2), rnd.uniform(t / 2 + 2, t)
        lo = a0 if rnd.random() < 0.3 else None           # as in build_short: the edge just moved off a cut
        hi = b0 if rnd.random() < 0.3 else None
        a, b, _ = edges.snap(words, a0, b0, t + 5, lo=lo, hi=hi)
        assert edges.cut_words(words, a, b) == (None, None), (a0, b0, a, b)
        assert lo is None or a >= lo - 1e-6
        assert hi is None or b <= hi + 1e-6


def test_subtitle_room_holds_three_lines():
    # an airboat chase: only the bottom line was ever seen (0.894, letters 0.0185 tall); the 3-line top line sat at ~0.806
    cut = burned.subtitle_cut([{"top": 0.894, "h": 0.0185, "frames": {1, 2}}])
    assert cut <= 0.806 - 0.005, cut
    # a cutscene where two lines were seen
    cut2 = burned.subtitle_cut([{"top": 0.859, "h": 0.0204, "frames": {1, 2}}, {"top": 0.889, "h": 0.0204, "frames": {3, 4}}])
    assert cut2 < 0.859 - 0.03, cut2
    assert burned.keep_share({"cut": 0.5}) == 1 - burned.MAX_CUT     # never more than MAX_CUT


def _frame(bars: int = 0, night: bool = False, seed: int = 0) -> Image.Image:
    rng = np.random.default_rng(seed)
    h, w = 540, 960
    if night:                                                        # a dark scene that fades to the top: no bar
        img = np.clip(np.linspace(8, 60, h)[:, None] + rng.normal(0, 4, (h, w)), 0, 255)
    else:
        img = np.clip(rng.normal(110, 40, (h, w)), 0, 255)
    if bars:
        img[:bars] = 2
        img[h - bars:] = 2
        img[h - bars + 8:h - bars + 20, 380:580] = 235                # a subtitle drawn inside the bottom bar
    return Image.fromarray(np.repeat(img.astype(np.uint8)[..., None], 3, axis=2))


def test_letterbox_found_and_night_left_alone():
    found = burned._bars([_frame(30, seed=i) for i in range(6)])
    assert abs(found["top"] - 30 / 540) < 0.006 and abs(found["bottom"] - 30 / 540) < 0.006, found
    none = burned._bars([_frame(0, night=True, seed=i) for i in range(6)])
    assert none["top"] == 0 and none["bottom"] == 0, none
    # a pure black night sky that "ends" at a fixed HUD strip in every frame (a streamer's webcam) is not a letterbox
    sky = []
    for i in range(6):
        img = np.asarray(_frame(0, seed=i).convert("L")).astype(np.float32).copy()
        img[:60] = 3
        img[60:70, :200] = 230                                       # the fixed overlay, part of the width only
        img[60:70, 200:] = 4
        img[70:140, 384:] = 4                                        # and the black sky goes on beside it
        sky.append(Image.fromarray(np.repeat(img.astype(np.uint8)[..., None], 3, axis=2)))
    assert burned._bars(sky)["top"] == 0, burned._bars(sky)
    assert burned._bars([_frame(0, seed=i) for i in range(6)])["top"] == 0


def test_text_vs_long_edges():
    """A subtitle line is text; a long thin bright edge (a wing, a cage bar) at the same size is not."""
    from PIL import ImageDraw, ImageFont
    img = Image.new("RGB", (960, 540), (60, 70, 80))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("Arial.ttf", 13)
    except OSError:
        font = ImageFont.load_default()
    d.text((330, 480), "You hear me? I want him buried in his own mess!", font=font, fill=(245, 245, 245))
    assert burned.text_rows(img), "the subtitle was not seen"
    edge = Image.new("RGB", (960, 540), (60, 70, 80))
    ImageDraw.Draw(edge).line([(300, 470), (660, 478)], fill=(245, 245, 245), width=2)
    assert not burned.text_rows(edge), "a long edge was taken for text"
    try:                                                             # a compilation's bold one-word caption
        bold = ImageFont.truetype("Arial Bold.ttf", 34)
    except OSError:
        bold = None
    if bold is not None:
        word = Image.new("RGB", (960, 540), (60, 70, 80))
        ImageDraw.Draw(word).text((440, 440), "HEY", font=bold, fill=(250, 250, 250))
        assert burned.text_rows(word), "a bold caption word was not seen"
    big = burned.subtitle_cut([{"top": 0.82, "h": 0.06, "frames": {1, 2}}])
    assert abs(big - (0.82 - burned.EDGE_ROOM)) < 1e-6, big       # one bold line: no room for 3 lines


def test_webcam_rect_skips_room_lines():
    """A still 'room' webcam (with a shelf line inside it) on top of moving game: the rectangle is the webcam's
    real sides, not the shelf, flush to the picture's left edge."""
    rng = np.random.default_rng(3)
    h, w = 540, 960
    room = np.clip(rng.normal(150, 6, (134, 240)), 0, 255)
    room[:, 60:62] = 40                                              # a shelf edge inside the room
    room[20:22, :] = 40
    frames = []
    for i in range(24):
        game = np.clip(rng.normal(90, 50, (h, w)), 0, 255)           # the game changes every frame
        game[40:174, 0:240] = room
        frames.append(Image.fromarray(np.repeat(game.astype(np.uint8)[..., None], 3, axis=2)))
    vert, horz = facecam.fixed_edges(frames)
    got = facecam.webcam_rect((100, 90, 46, 46), vert, horz, facecam.change_map(frames))
    assert got, "no rectangle"
    (x0, y0, x1, y1), lines = got
    assert x0 == 0 and abs(x1 - 240) <= 8 and abs(y0 - 40) <= 6 and abs(y1 - 174) <= 6, got


def main() -> int:
    tests = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print(f"✓ {name}")
        except Exception:
            bad += 1
            print(f"✗ {name}\n{traceback.format_exc()}")
    print(f"\n{len(tests) - bad}/{len(tests)} passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
