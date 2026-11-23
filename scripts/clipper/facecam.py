"""Finding the streamer's webcam in a stream: a face that stays in the same spot in most frames is the facecam
(faces in the game move around). Returns the box to cut out for the top of the Short, already in the right shape."""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .frames import grab_frames, video_info
from .paths import W

CAM_H = 740                 # height of the facecam band at the top of the 1080 x 1920 Short
CAM_ASPECT = W / CAM_H      # the box we cut from the stream has this shape, so nothing gets stretched

_cascades = None


def _detectors():
    global _cascades
    if _cascades is None:
        import cv2
        base = cv2.data.haarcascades
        # front-on (two models) + side-on. Measured: a capped streamer was found in 22/24 frames by alt2 alone;
        # a streamer holding his head (small bottom-left cam) only in 9-13/30 by front-on, 17/30 by side-on.
        _cascades = [cv2.CascadeClassifier(base + n) for n in
                     ("haarcascade_frontalface_alt2.xml", "haarcascade_frontalface_default.xml",
                      "haarcascade_profileface.xml")]
    return _cascades


def _faces(img) -> list[tuple[int, int, int, int]]:
    """Face boxes in one frame from every detector together (side-on faces both ways too). Loose on purpose
    (small webcams, caps, hands on the face, heads turned): detect() keeps only a face that sits in the same
    spot across many frames, which throws away the stray hits."""
    import cv2
    g = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2GRAY)
    g = cv2.equalizeHist(g)
    w = g.shape[1]
    found: list[tuple[int, int, int, int]] = []
    for i, c in enumerate(_detectors()):
        found += [tuple(int(v) for v in b) for b in c.detectMultiScale(g, scaleFactor=1.06, minNeighbors=3,
                                                                        minSize=(14, 14))]
        if i == 2:                               # the side-on model only knows one direction: also look mirrored
            found += [(w - int(x) - int(bw), int(y), int(bw), int(bh)) for (x, y, bw, bh) in
                      c.detectMultiScale(cv2.flip(g, 1), scaleFactor=1.06, minNeighbors=3, minSize=(14, 14))]
    return found


FRAMES = 32                 # frames read per moment
EDGE_HOLD = 0.6             # a pixel that is an edge in 60%+ of the frames is fixed: an overlay's side (gameplay moves)
LINE_SHARE = 0.6            # a webcam side = a fixed straight line along 60%+ of the stretch beside the face
REACH = 4.5                 # look for the webcam's sides up to 4.5 face-widths from the face
NEARBY_S = 90.0             # too few face hits in the moment: read the stream 90 s around it (the webcam doesn't move)
CHANGE_MAX = 0.09           # mean change of the webcam box over the clip (0-1): webcams 0.02-0.06, a game close-up 0.10
GAME_MIN, GAME_STEP = 4.0, 4.0   # beyond a webcam side the game changes 4+ (0-255) and 4x more than the room inside


def fixed_edges(frames) -> tuple[np.ndarray, np.ndarray]:
    """(vertical, horizontal) edge pixels that stay put in EDGE_HOLD of the frames: a webcam's sides, the HUD.
    A line may wobble by 1 pixel between frames (a 1080p stream read at 960 px splits it over two columns)."""
    import cv2
    gs = [cv2.cvtColor(np.asarray(f), cv2.COLOR_RGB2GRAY).astype(np.float32) for f in frames]
    wide, tall = np.ones((1, 3), np.uint8), np.ones((3, 1), np.uint8)
    vert = np.mean([cv2.dilate((np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)) > 60).astype(np.uint8), wide)
                    for g in gs], axis=0) >= EDGE_HOLD
    horz = np.mean([cv2.dilate((np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)) > 60).astype(np.uint8), tall)
                    for g in gs], axis=0) >= EDGE_HOLD
    return vert, horz


def change_map(frames) -> np.ndarray:
    """How much each pixel changes over the frames (mean distance from its median, 0-255): the game moves, the
    streamer's room stands still."""
    import cv2
    g = np.stack([cv2.cvtColor(np.asarray(f), cv2.COLOR_RGB2GRAY) for f in frames]).astype(np.float32)
    return np.mean(np.abs(g - np.median(g, axis=0)), axis=0)


def _side(share: np.ndarray, start: int, stop: int, edge: int, across) -> tuple[int | None, str]:
    """Walk from just outside the face toward `stop`. The first fixed straight line with moving game beyond it and
    the still room before it is the webcam's side (a shelf or a door in the room has still room on both sides; a thick
    border gives its inner edge first, so the border stays out). No such line and the walk reached the picture's
    edge = the webcam sits flush against it; otherwise unknown."""
    step = 1 if stop > start else -1
    for i in range(start, stop, step):
        if 0 <= i < len(share) and share[i] >= LINE_SHARE and across(i, step):
            return i, "line"
    return (edge, "edge") if stop in (edge + step, edge) else (None, "")


def _moves_beyond(dev: np.ndarray, band: tuple[slice, slice], vertical: bool):
    """across(i, step) for _side: True when the strip beyond line i (away from the face) changes GAME_STEP x more
    than the strip before it. Measured: a webcam's real side 10-36 outside vs 0.2-0.7 inside (day and night); a line
    in the room ~0.6 on both sides."""
    def across(i: int, step: int) -> bool:
        lo, hi = sorted((i + step * 3, i + step * 12))
        lo_in, hi_in = sorted((i - step * 3, i - step * 12))
        size = dev.shape[1] if vertical else dev.shape[0]
        if lo < 0 or hi >= size or lo_in < 0 or hi_in >= size:
            return False                             # too close to the picture's edge to tell
        if vertical:
            out, inn = dev[band[0], lo:hi + 1], dev[band[0], lo_in:hi_in + 1]
        else:
            out, inn = dev[lo:hi + 1, band[1]], dev[lo_in:hi_in + 1, band[1]]
        o, n = float(np.median(out)), float(np.percentile(inn, 25))
        return o >= GAME_MIN and o >= GAME_STEP * max(n, 0.5)
    return across


def webcam_rect(face: tuple[float, float, float, float], vert: np.ndarray, horz: np.ndarray, dev: np.ndarray
                ) -> tuple[tuple[int, int, int, int], int] | None:
    """The webcam's real rectangle (x0, y0, x1, y1 at sample size) around a face, measured from the fixed edges and
    the change map, and how many of its sides are drawn lines. None when no sensible rectangle is there."""
    fh_img, fw_img = vert.shape
    x, y, w, h = face
    cx, cy = x + w / 2, y + h / 2
    rows_sl = slice(int(max(0, cy - 1.2 * h)), int(min(fh_img, cy + 1.2 * h)))
    cols_sl = slice(int(max(0, cx - 1.5 * w)), int(min(fw_img, cx + 1.5 * w)))
    rows, cols = vert[rows_sl], horz[:, cols_sl]
    if rows.size == 0 or cols.size == 0:
        return None
    colshare, rowshare = rows.mean(axis=0), cols.mean(axis=1)
    across_v = _moves_beyond(dev, (rows_sl, cols_sl), True)
    across_h = _moves_beyond(dev, (rows_sl, cols_sl), False)
    near_x, near_y, far = 0.7 * w, 0.7 * h, REACH * w
    left = _side(colshare, int(cx - near_x), max(-1, int(cx - far)), 0, across_v)
    right = _side(colshare, int(cx + near_x), min(fw_img, int(cx + far)), fw_img - 1, across_v)
    top = _side(rowshare, int(cy - near_y), max(-1, int(cy - far)), 0, across_h)
    bottom = _side(rowshare, int(cy + near_y), min(fh_img, int(cy + far)), fh_img - 1, across_h)
    if None in (left[0], right[0], top[0], bottom[0]):
        return None
    x0, x1, y0, y1 = left[0], right[0], top[0], bottom[0]
    rw, rh = x1 - x0, y1 - y0
    if rh <= 0 or not (1.0 <= rw / rh <= 2.6 and 0.12 <= w / rw <= 0.65) or rw * rh > 0.4 * fw_img * fh_img:
        return None
    # step a little inside a drawn side so no sliver of a border or the game shows in the band
    ix, iy = max(2, round(0.02 * rw)), max(2, round(0.02 * rh))
    x0 += ix if left[1] == "line" else 0
    x1 -= ix if right[1] == "line" else 0
    y0 += iy if top[1] == "line" else 0
    y1 -= iy if bottom[1] == "line" else 0
    lines = sum(1 for sd in (left, right, top, bottom) if sd[1] == "line")
    return (x0, y0, x1, y1), lines


def _clusters(boxes: list[tuple], tol: float) -> list[tuple[list, set]]:
    """Faces that sit in the same spot (centre within tol, similar size), biggest groups first, one per spot."""
    groups = []
    for (_, x, y, w, h) in boxes:
        cx, cy = x + w / 2, y + h / 2
        members = [bb for bb in boxes
                   if abs(bb[1] + bb[3] / 2 - cx) < tol and abs(bb[2] + bb[4] / 2 - cy) < tol and 0.6 < bb[3] / w < 1.6]
        groups.append((members, {bb[0] for bb in members}))
    groups.sort(key=lambda g: -len(g[1]))
    out: list[tuple[list, set]] = []
    for members, hit in groups:
        med = np.median(np.array([bb[1:] for bb in members], dtype=float), axis=0)
        if all(abs(med[0] - o[2][0]) > tol or abs(med[1] - o[2][1]) > tol for o in out):
            out.append((members, hit, med))
    return [(m, h) for m, h, _ in out]


def _change(frames, crop: tuple[int, int, int, int]) -> float:
    tiles = np.stack([np.asarray(fr.crop(crop).convert("L").resize((48, 32)), dtype=float) for fr in frames])
    return float(np.mean(np.abs(tiles - np.median(tiles, axis=0))) / 255)


def _find(video: Path, a: float, b: float, n: int, sample_w: int) -> tuple[dict | None, list]:
    info = video_info(Path(video))
    sw, sh = info["w"], info["h"]
    if not sw or not sh or b - a < 1:
        return None, []
    frames = grab_frames(Path(video), [float(t) for t in np.linspace(a + 0.3, b - 0.3, n)], sample_w)
    if not frames:
        return None, []
    boxes = [(i, *f) for i, fr in enumerate(frames) for f in _faces(fr)]   # (frame index, x, y, w, h) at sample size
    if not boxes:
        return None, frames
    fw, fh = frames[0].width, frames[0].height
    vert, horz = fixed_edges(frames)
    dev = change_map(frames)
    quarter = lambda hit: len({min(3, 4 * i // len(frames)) for i in hit})
    for k, (members, hit) in enumerate(_clusters(boxes, 0.07 * fw)[:4]):
        med = np.median(np.array([bb[1:] for bb in members], dtype=float), axis=0)
        x, y, w, h = med
        if h > 0.45 * fh:
            continue                                 # a full-screen camera (just chatting), not a game with a facecam
        rect = webcam_rect((x, y, w, h), vert, horz, dev)
        if rect:                                     # a face inside a drawn webcam frame: a few hits are enough
            if len(hit) < 3 or quarter(hit) < 2:
                continue
            (x0, y0, x1, y1), lines = rect
            crop = (x0, y0, x1, y1)
        else:                                        # no frame found: the face alone must be steady and always there
            if k > 0 or len(hit) < max(4, int(0.35 * len(frames))) or quarter(hit) < 3:
                continue
            lines = 0
            bw = min(w * 3.3, fw * 0.6)              # a webcam box is usually ~3x as wide as the face in it
            bh = bw / CAM_ASPECT
            if bh > fh:
                bh, bw = fh, fh * CAM_ASPECT
            cx, cy = x + w / 2, y + h / 2
            x0 = min(max(0.0, cx - bw / 2), fw - bw)
            y0 = min(max(0.0, cy - bh * 0.55), fh - bh)   # face a bit low: the hook pill sits on top
            crop = (int(x0), int(y0), int(x0 + bw), int(y0 + bh))
        change = _change(frames, crop)
        if change > CHANGE_MAX:
            continue                                 # the box's picture keeps changing: a game scene, not a webcam
        k_src = sw / fw
        ev = lambda v: int(round(v * k_src / 2) * 2)
        x0, y0, x1, y1 = crop
        return ({"x": ev(x0), "y": ev(y0), "w": ev(x1 - x0), "h": ev(y1 - y0), "frames": len(hit), "of": len(frames),
                 "side": "left" if (x + w / 2) < fw / 2 else "right", "change": round(change, 3),
                 "measured": bool(rect), "border_lines": lines}, frames)
    return None, frames


def _still_there(cam: dict, frames, sw: int) -> bool:
    """A webcam found NEAR the moment: is it on screen in the moment too (same room, a face or its frame in it)?"""
    if not frames:
        return False
    k = frames[0].width / sw
    crop = tuple(int(v * k) for v in (cam["x"], cam["y"], cam["x"] + cam["w"], cam["y"] + cam["h"]))
    if _change(frames, crop) > CHANGE_MAX:
        return False
    inside = sum(1 for fr in frames if any(crop[0] <= fx + fw_ / 2 <= crop[2] and crop[1] <= fy + fh_ / 2 <= crop[3]
                                           for fx, fy, fw_, fh_ in _faces(fr.crop((0, 0, fr.width, fr.height)))))
    return inside >= 2


def detect(video: Path, a: float = 0.0, b: float | None = None, n: int = FRAMES, sample_w: int = 960) -> dict | None:
    """{"x", "y", "w", "h", ...} of the streamer's webcam in the stream's own pixels, or None when there's none.
    The box is the webcam's real rectangle when its edges can be measured ("measured": True), else sized from the
    face. Too few face hits in the moment (glasses, looking down): the stream around it is read too."""
    info = video_info(Path(video))
    b = info["dur"] if b is None else min(b, info["dur"])
    cam, frames = _find(video, a, b, n, sample_w)
    if cam or not info["dur"]:
        return cam
    wide, _ = _find(video, max(0.0, a - NEARBY_S), min(info["dur"], b + NEARBY_S), n + 16, sample_w)
    if wide and _still_there(wide, frames, info["w"]):
        wide["nearby"] = True
        return wide
    return None
