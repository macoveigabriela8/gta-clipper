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


def detect(video: Path, a: float = 0.0, b: float | None = None, n: int = 24, sample_w: int = 960) -> dict | None:
    """{"x", "y", "w", "h"} in the stream's own pixels, or None when there's no steady webcam."""
    info = video_info(Path(video))
    sw, sh = info["w"], info["h"]
    if not sw or not sh:
        return None
    b = info["dur"] if b is None else min(b, info["dur"])
    if b - a < 1:
        return None
    times = [float(t) for t in np.linspace(a + 0.3, b - 0.3, n)]
    frames = grab_frames(Path(video), times, sample_w)
    if not frames:
        return None
    boxes = []                                   # (frame index, x, y, w, h) at sample size
    for i, fr in enumerate(frames):
        for (x, y, w, h) in _faces(fr):
            boxes.append((i, x, y, w, h))
    if not boxes:
        return None
    fw = frames[0].width
    fh = frames[0].height
    tol = 0.07 * fw
    best, best_frames = None, set()
    for (_, x, y, w, h) in boxes:
        cx, cy = x + w / 2, y + h / 2
        members = [bb for bb in boxes
                   if abs(bb[1] + bb[3] / 2 - cx) < tol and abs(bb[2] + bb[4] / 2 - cy) < tol
                   and 0.6 < bb[3] / w < 1.6]
        frames_hit = {bb[0] for bb in members}
        if len(frames_hit) > len(best_frames):
            best, best_frames = members, frames_hit
    if len(best_frames) < max(4, int(0.35 * len(frames))):
        return None                              # no face that stays put: no webcam (or it's hidden)
    quarters = {min(3, 4 * i // len(frames)) for i in best_frames}
    if len(quarters) < 3:
        return None                              # only in one stretch: a game close-up, not a webcam that's always on
    med = np.median(np.array([bb[1:] for bb in best], dtype=float), axis=0)
    x, y, w, h = med
    if h > 0.45 * fh:
        return None                              # a full-screen camera (just chatting), not a game with a facecam
    scale = sw / fw
    cx, cy, face_w = (x + w / 2) * scale, (y + h / 2) * scale, w * scale
    box_w = min(face_w * 3.3, sw * 0.6)              # a webcam box is usually ~3x as wide as the face in it
    box_h = box_w / CAM_ASPECT
    if box_h > sh:
        box_h = sh
        box_w = box_h * CAM_ASPECT
    x0 = min(max(0.0, cx - box_w / 2), sw - box_w)
    y0 = min(max(0.0, cy - box_h * 0.55), sh - box_h)    # face a bit low: the hook pill sits on top
    # A webcam keeps the same room behind the face; a game character's face sits on a changing scene.
    # Measured (mean change of the box over the clip, 0-1): webcams 0.02-0.06, a game close-up 0.10.
    k = fw / sw
    crop = (int(x0 * k), int(y0 * k), int((x0 + box_w) * k), int((y0 + box_h) * k))
    tiles = np.stack([np.asarray(fr.crop(crop).convert("L").resize((48, 32)), dtype=float) for fr in frames])
    change = float(np.mean(np.abs(tiles - np.median(tiles, axis=0))) / 255)
    if change > 0.09:
        return None
    ev = lambda v: int(round(v / 2) * 2)
    return {"x": ev(x0), "y": ev(y0), "w": ev(box_w), "h": ev(box_h), "frames": len(best_frames), "of": len(frames),
            "side": "left" if cx < sw / 2 else "right", "change": round(change, 3)}
