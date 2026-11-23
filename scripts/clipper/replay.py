"""Instant replay (format 5): right after the main part, the payoff plays again at half speed, the gameplay zoomed
1.2x, under a black "REPLAY" pill. Same layout and the same crop numbers as the main part (cam box, keep share,
game box), no captions. render.build_short calls add() right after the main part is written, so loudness, cover,
QA and the QA sheet run on the joined file; qc.check calls checks() (reference/cutting-sop.md § 9)."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import numpy as np

from . import overlays, render, top5
from .frames import grab_frames, video_info
from .paths import FPS, H, W, ffmpeg, run, seconds

BEFORE, AFTER = 1.0, 0.5    # the replay shows the stream from 1.0 s before the payoff to 0.5 s after it
SLOW = 2.0                  # half speed: those 1.5 s play for 3 s
ZOOM = 1.2                  # the gameplay is zoomed in this much around its centre (the webcam band is not)
TEXT = "REPLAY"
LENGTH_S = (2.5, 4.0)       # replay_length
MATCH_MAX = 0.03            # replay_matches: mean gameplay difference (0-1) to the zoomed source at the payoff
                            # (measured: the right second 0.001-0.002; 1-6 s off 0.068-0.20, even in a dark scene)


def zoom_box(g: dict) -> dict:
    """The middle 1/ZOOM of the game box (source pixels), same shape."""
    w, h = render._even(g["w"] / ZOOM), render._even(g["h"] / ZOOM)
    return {"x": render._even(g["x"] + (g["w"] - w) / 2), "y": render._even(g["y"] + (g["h"] - h) / 2), "w": w, "h": h}


def add(video: Path, mp4: Path, a: float, b: float, moment: dict, cam_raw: dict | None, info: dict, keep: float,
        work: Path, top: float = 0.0) -> dict:
    """Append the replay to the finished main part `mp4` (the file is replaced by main + replay).
    Returns what checks() and qa_sheet() need; {"error": ..., "main_s"} when there's nothing to replay."""
    video, mp4 = Path(video), Path(mp4)
    main_s = video_info(mp4)["dur"]
    try:
        pay = seconds(moment.get("payoff_at"))
    except (TypeError, ValueError):
        return {"error": 'There is no "payoff_at" on this moment, so nothing to replay.', "main_s": main_s, "a": a}
    ra, rb = max(a, pay - BEFORE), min(b, pay + AFTER)
    if rb - ra < 0.5:
        return {"error": f"The payoff ({pay:.1f} s) isn't inside the Short ({a:.1f}-{b:.1f} s).", "main_s": main_s,
                "a": a}
    sw, sh = info["w"], info["h"]
    cam = render.fit_cam(cam_raw, sw, sh)              # exactly render._render / _graph's numbers
    avoid = {k: float(cam_raw[k]) for k in ("x", "y", "w", "h")} if cam else None
    g = render.game_box(sw, sh, bool(cam), avoid, keep, top)
    z = zoom_box(g)
    dur = (rb - ra) * SLOW
    pill, hx, hy = overlays.hook_png(TEXT, work / "replay_pill.png")
    piece, joined = work / "replay.mp4", work / "joined.mp4"
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-ss", f"{ra:.3f}", "-t", f"{rb - ra:.3f}", "-i", str(video),
           "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(pill)]
    crop = lambda r, w, h: f"crop={r['w']}:{r['h']}:{r['x']}:{r['y']},scale={w}:{h}:flags=bicubic,setsar=1"
    slow = f"[0:v]setpts={SLOW}*(PTS-STARTPTS),fps={FPS}"
    if cam:
        v = (f"{slow},split=2[s1][s2];[s1]{crop(cam, W, render.CAM_H)}[top];[s2]{crop(z, W, render.GAME_H)}[bot];"
             "[top][bot]vstack=inputs=2[base]")
    else:
        v = f"{slow},{crop(z, W, H)}[base]"
    v += f";[1:v]format=rgba[pill];[base][pill]overlay={hx}:{hy}:eof_action=pass,format=yuv420p[vout]"
    if info["has_audio"]:
        sound = f"[0:a]atempo={1 / SLOW},aresample=48000,{render._loudness_fix(video, ra, rb - ra)},aresample=48000"
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        sound = "[2:a]aresample=48000"
    sound += (f",apad,atrim=0:{dur:.3f},afade=t=in:st=0:d={render.FADE},"
              f"afade=t=out:st={max(0.0, dur - render.FADE):.3f}:d={render.FADE}[aout]")
    cmd += ["-filter_complex", f"{v};{sound}", "-map", "[vout]", "-map", "[aout]", "-t", f"{dur:.3f}",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(piece)]
    run(cmd)
    replay_s = video_info(piece)["dur"]
    top5._join([mp4, piece], joined)                   # hard cut, one re-encode, same settings as a Short
    shutil.copyfile(joined, mp4)
    return {"main_s": main_s, "replay_s": replay_s, "a": a, "ra": ra, "rb": rb, "video": str(video),
            "game": g, "zoom": z, "band_top": render.CAM_H if cam else 0}


def _gray(img, size: tuple[int, int]) -> np.ndarray:
    return np.asarray(img.convert("L").resize(size), dtype=float) / 255


def checks(mp4: Path, rep: dict) -> list[dict]:
    """The replay's own rules (levels in qc.RULES)."""
    from .qc import result
    out: list[dict] = []
    add = lambda cid, ok, say="": out.append(result(cid, ok, say))
    if "replay_s" not in rep:
        add("replay_length", False, rep.get("error", "The replay wasn't made.") +
            ' Give the moment a "payoff_at" inside it, or drop "replay".')
        return out
    r, main_s = rep["replay_s"], rep["main_s"]
    add("replay_length", LENGTH_S[0] <= r <= LENGTH_S[1],
        f"The replay is {r:.2f} s; it should be {LENGTH_S[0]}-{LENGTH_S[1]:.0f} s. Start the moment 1 s or more "
        "before its payoff and end it 0.5 s or more after.")

    # the frame half-way through the replay vs the stream at that second, cropped the replay's way and the main way
    mid = main_s + r / 2
    src_t = rep["ra"] + (r / 2) / SLOW
    shot = grab_frames(mp4, [mid], W)
    src = grab_frames(Path(rep["video"]), [src_t - 1 / 30, src_t, src_t + 1 / 30], 10_000)
    top = rep["band_top"] + round(0.2 * (H - rep["band_top"]))         # the bottom 80% of the game: off the pill
    size = (64, round(64 * (H - top) / W))
    if not shot or not src:
        add("replay_matches", False, "Couldn't read the replay's frames back.")
    else:
        sw = src[0].width
        scale = sw / max(1, video_info(Path(rep["video"]))["w"])        # 1.0 unless grab_frames scaled it
        got = _gray(shot[0].crop((0, top, W, H)), size)

        def diff(box: dict) -> float:
            y0 = box["y"] + 0.2 * box["h"]
            area = tuple(round(v * scale) for v in (box["x"], y0, box["x"] + box["w"], box["y"] + box["h"]))
            return min(float(np.abs(_gray(f.crop(area), size) - got).mean()) for f in src)

        zoomed, plain = diff(rep["zoom"]), diff(rep["game"])
        add("replay_matches", zoomed <= MATCH_MAX and zoomed < plain,
            f"The replay doesn't show the payoff zoomed: half-way through it differs from the stream at "
            f"{src_t:.1f} s by {zoomed:.3f} at {ZOOM}x (needs {MATCH_MAX} or less) and {plain:.3f} unzoomed "
            "(must be more). Look at the QA sheet's REPLAY row; usually a broken render: make it again.")

    with tempfile.TemporaryDirectory(prefix="gtaclip-replay-") as tmp:
        shown = top5._pill_shows(mp4, {"text": TEXT, "rank": "replay", "t0": main_s, "t1": main_s + r}, Path(tmp))
    add("replay_badge", shown >= top5.PILL_MATCH,
        f"The REPLAY pill is {shown:.0%} on screen half-way through the replay (needs "
        f"{top5.PILL_MATCH:.0%}). Look at the QA sheet's REPLAY row.")
    return out


def qa_sheet(mp4: Path, out_jpg: Path, rep: dict) -> Path:
    """qc.qa_sheet's frames for the main part (hook, middle, last 4 s), then a row of 6 frames of the replay."""
    from . import qc
    from .sheets import sheet_at
    main_s, a = rep["main_s"], rep["a"]
    if "replay_s" not in rep:
        return qc.qa_sheet(mp4, out_jpg, main_s, a)
    r, ra = rep["replay_s"], rep["ra"]
    head = [0.1, 1.5, 2.9]
    body = [float(t) for t in np.arange(4.5, max(4.5, main_s - 4.5), 3.0)]
    tail = [max(0.0, main_s - x) for x in (4.0, 3.0, 2.0, 1.0, 0.1)]
    main_t = sorted({round(t, 2) for t in head + body + tail if 0 <= t <= main_s - 0.05})
    rep_t = [round(main_s + r * f, 2) for f in (0.05, 0.25, 0.45, 0.65, 0.85, 0.97)]

    def label(t: float) -> str:
        if t >= main_s:
            return f"REPLAY {t - main_s:.1f}s (src {ra + (t - main_s) / SLOW:.2f})"
        return f"{t:.1f}s" + ("  END" if t >= main_s - 4.05 else "") + f"  (src {a + t:.1f})"
    return sheet_at(Path(mp4), Path(out_jpg), main_t + rep_t,
                    f"{Path(mp4).stem} · QA: hook, middle, last 4 s, then the REPLAY", label, cols=6, width=216)
