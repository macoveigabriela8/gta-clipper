"""Format 4, Reaction zoom: at the payoff the streamer's face takes over the whole screen (a hard punch-in, then a
slow push while it holds ~1.5 s), then the split layout comes back, or the Short ends on the face.
A moment asks for it with "zoom": true; it needs "payoff_at" and a webcam. Also its QA, measured on the finished Short.
render.py and qc.py only call window() / graph() / skip() / checks()."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from .frames import grab_frames
from .paths import FPS, H, W, ffmpeg, seconds

HOLD = 1.5            # seconds the face fills the screen
MIN_LEFT = 0.8        # less Short than this after the payoff: no zoom (it would only flash)
END_ON_FACE = 1.0     # less than this would be left after the hold: hold to the end instead (ends on the face)
PUSH = 0.06           # the slow push while it holds: 1.00 -> 1.06
START_TOL = 0.3       # QA: the cut to the face lands this close to the payoff
FACE_MIN_W = 0.30     # QA: the face box is at least this share of the zoomed picture's width...
FACE_MAX_W = 1.15     # ...and at most this (a face box this much wider than the screen = only part of the face)
SOFT_UPSCALE = 9.0    # QA: the webcam blown up more than this looks soft
CAPTION_Y = 0.80      # during the zoom our captions drop to here (centre, share of the height): off the eyes and mouth


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def _source_faces(video: Path, cam: dict, times: list[float], src_w: int) -> list[tuple[float, float] | None]:
    """Per time: (left, right) in source x of the biggest face inside the cam box, or None. Read at 960 px wide,
    the size facecam.detect finds webcam faces at (the detector misses a face blown up to fill a whole Short)."""
    from .facecam import _faces
    k = 960 / src_w
    out = []
    for fr in grab_frames(Path(video), times, 960):
        found = _faces(fr.crop((int(cam["x"] * k), int(cam["y"] * k), int((cam["x"] + cam["w"]) * k),
                                int((cam["y"] + cam["h"]) * k))))
        if found:
            x, _, w, _ = max(found, key=lambda f: f[2] * f[3])
            out.append((cam["x"] + x / k, cam["x"] + (x + w) / k))
        else:
            out.append(None)
    return out


def window(moment: dict | None, a: float, b: float, cam: dict | None, video: Path, src_w: int
           ) -> tuple[dict | None, str]:
    """(zoom, note). a and b are the FINAL source seconds of the Short (after the silence trim, word snap and cut
    moves; the same a the render seeks to), so the payoff's second in the Short is payoff_at - a.
    cam is the fitted cam box (render.fit_cam). No "zoom" on the moment: (None, "")."""
    moment = moment or {}
    if not moment.get("zoom"):
        return None, ""
    if not cam:
        return None, "No zoom: this moment has no webcam to zoom into (the zoom shows the streamer's face)."
    try:
        pay = seconds(moment["payoff_at"])
    except (KeyError, TypeError, ValueError):
        return None, 'No zoom: it starts on the payoff, so the moment needs "payoff_at" (a second of the video).'
    dur, t0 = b - a, pay - a
    if t0 < 0 or dur - t0 < MIN_LEFT:
        return None, (f"No zoom: the payoff ({pay:.1f} s) is outside the Short or too close to its end "
                      f"({dur - t0:.1f} s left). End 1-3 s after the payoff.")
    t1 = t0 + HOLD
    if dur - t1 < END_ON_FACE:
        t1 = dur
    ch = _even(cam["h"])
    cw = _even(ch * W / H)
    xs = [(f[0] + f[1]) / 2 for f in _source_faces(video, cam, [pay + d for d in (0.1, 0.5, 0.9, 1.3)], src_w) if f]
    cx = float(np.median(xs)) if xs else cam["x"] + cam["w"] / 2
    x0 = _even(min(max(cam["x"], cx - cw / 2), cam["x"] + cam["w"] - cw))
    return {"t0": round(t0, 3), "t1": round(t1, 3), "ends_on_face": t1 >= dur,
            "crop": {"x": x0, "y": cam["y"], "w": cw, "h": ch}, "upscale": round(H / ch, 1),
            "video": str(video), "cam": dict(cam), "src_w": src_w}, ""


def graph(src: str, base: str, out: str, z: dict) -> str:
    """Filter chain: [src] (the raw video, a split branch) -> full-screen face, laid over [base] between t0 and t1."""
    c = z["crop"]
    f0, n = round(z["t0"] * FPS), max(1, round((z["t1"] - z["t0"]) * FPS))
    return (f"[{src}]crop={c['w']}:{c['h']}:{c['x']}:{c['y']},scale={W}:{H}:flags=lanczos,setsar=1,"
            f"zoompan=z='1+{PUSH}*clip((in-{f0})/{n},0,1)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
            f":d=1:s={W}x{H}:fps={FPS}[zface];"
            f"[{base}][zface]overlay=0:0:enable='between(t,{z['t0']:.3f},{z['t1']:.3f})'[{out}]")


def caption_y(normal: int, z: dict | None, band_h: int) -> str:
    """The caption band's y for overlay: unchanged without a zoom (the same graph as before), lower inside it
    (at the seam a full-screen face has its eyes right there)."""
    if not z:
        return str(normal)
    low = round(H * CAPTION_Y - band_h / 2)
    return f"'if(between(t,{z['t0']:.3f},{z['t1']:.3f}),{low},{normal})'"


def skip(ctx: dict) -> tuple[float, float] | None:
    """The zoom window (Short seconds): the older band checks don't sample inside it (no split layout there)."""
    z = (ctx or {}).get("zoom")
    return (z["t0"] - 0.1, z["t1"] + 0.1) if z else None


def outside(times: list[float], win: tuple[float, float] | None) -> list[float]:
    kept = [t for t in times if not (win and win[0] <= t <= win[1])]
    return kept or times


def _cuts(mp4: Path, a: float, b: float) -> list[tuple[float, float]]:
    """(Short second, score) of every hard cut of the whole picture between a and b. Threshold 8, not 14: the cut
    from dark night gameplay to the face scores only ~10."""
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                        "-i", str(mp4), "-an", "-vf", "scale=160:-2,scdet=threshold=8", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.findall(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)", p.stderr or "")
    return [(round(a + float(t), 2), float(s)) for s, t in found]


def checks(mp4: Path, ctx: dict, dur: float) -> list[dict]:
    """QA of the zoom, only for a moment with "zoom": true."""
    moment = ctx.get("moment") or {}
    if not moment.get("zoom"):
        return []
    from .qc import result                                 # (here: qc imports this module)
    out: list[dict] = []
    add = lambda cid, ok, say="": out.append(result(cid, ok, say))
    z = ctx.get("zoom")
    if not z:
        add("zoom", False, (ctx.get("zoom_note") or "No zoom was made.") +
            ' Fix it, or remove "zoom" from the moment.')
        return out
    t0, t1 = z["t0"], z["t1"]
    pay = seconds(moment["payoff_at"]) - float(ctx.get("a", 0))
    cuts = _cuts(mp4, max(0.0, t0 - 1.0), min(dur, t1 + 0.6))
    near_pay = [c for c in cuts if abs(c[0] - pay) <= 1.0]
    start = max(near_pay, key=lambda c: c[1])[0] if near_pay else None
    back = z["ends_on_face"] or any(abs(c[0] - t1) <= START_TOL for c in cuts)
    inside = 0 <= t0 < t1 <= dur + 0.05
    good = start is not None and abs(start - pay) <= START_TOL and back and inside
    add("zoom_window", good,
        "" if good else f"The zoom should cut in on the payoff ({pay:.1f} s in the Short) and "
        f"{'stay to the end' if z['ends_on_face'] else f'cut back at {t1:.1f} s'}; measured: cut in at "
        f"{'none found' if start is None else f'{start:.1f} s'}, cut back {'found' if back else 'missing'}"
        f"{'' if inside else ', window outside the Short'}. Check payoff_at.")

    # The face the zoom shows, measured on the exact source pixels it crops: big (30-115% of the screen's width)
    # and centred (its middle in the middle half), so it is not cut off. Faces in the finished Short are blown up
    # too far for the detector, so it reads them at webcam size.
    span, c, a = t1 - t0, z["crop"], float(ctx.get("a", 0))
    times = [a + t for t in (t0 + 0.25, t0 + span / 2, t1 - 0.15)]
    hit, seen = 0, []
    for f in _source_faces(Path(z["video"]), z["cam"], times, z["src_w"]):
        if not f:
            seen.append("no face")
            continue
        share, mid = (f[1] - f[0]) / c["w"], ((f[0] + f[1]) / 2 - c["x"]) / c["w"]
        seen.append(f"{share:.0%} wide at {mid:.0%}")
        hit += FACE_MIN_W <= share <= FACE_MAX_W and 0.25 <= mid <= 0.75
    add("zoom_face", hit >= 2,
        "" if hit >= 2 else f"Inside the zoom a big, centred face shows in only {hit} of {len(times)} frames "
        f"({'; '.join(seen)}; needs 30-115% of the width, centred): the face is cut off, small or not there. Look "
        'at the QA sheet; give "cam" by hand, or remove "zoom".')
    add("zoom_sharp", z["upscale"] <= SOFT_UPSCALE,
        "" if z["upscale"] <= SOFT_UPSCALE else f"The webcam is blown up {z['upscale']:.1f}x for the zoom (tiny "
        'webcam): the face may look mushy. If it does on the QA sheet, remove "zoom" from this moment.')
    return out
