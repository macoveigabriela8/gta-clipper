"""Turning one moment of a video into a finished vertical Short (1080 x 1920, 30 fps): facecam band on top,
gameplay below, black hook pill first, word-by-word captions on the seam, loudness-normalised sound.
Entry point: build_short()."""
from __future__ import annotations

import json
import subprocess
import tempfile
import time
from pathlib import Path

from . import burned, overlays, qc
from .frames import video_info
from .paths import FPS, H, W, ffmpeg, log, run, save, setting

CAM_H = 740                       # facecam band at the top
GAME_H = H - CAM_H                # gameplay underneath: 1080 x 1180
CAPTION_Y_NO_CAM = 0.62           # caption centre as a share of the height when there's no facecam
FADE = 0.05                       # sound fade in / out (s)
HOOK_FADE = 0.2                   # the pill fades out over its last 0.2 s
COVER_AT = 0.5


def _even(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def fit_cam(cam: dict | None, sw: int, sh: int) -> dict | None:
    """The cam box (source pixels) reshaped to the band's 1080:740 shape without stretching: grow it a little
    when it's close, otherwise trim the long side. None when the box is unusable."""
    if not cam:
        return None
    try:
        x, y, w, h = (float(cam[k]) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    x, y = max(0.0, min(x, sw - 2)), max(0.0, min(y, sh - 2))
    w, h = min(w, sw - x), min(h, sh - y)
    if w < 32 or h < 32:
        return None
    aspect = W / CAM_H
    cx, cy = x + w / 2, y + h / 2
    if w / h < aspect:
        grown = h * aspect
        if grown / w <= 1.15 and grown <= sw:
            w = grown
        else:
            h = w / aspect
    else:
        grown = w / aspect
        if grown / h <= 1.15 and grown <= sh:
            h = grown
        else:
            w = h * aspect
    w, h = _even(min(w, sw)), _even(min(h, sh))
    x0 = _even(min(max(0.0, cx - w / 2), sw - w))
    y0 = _even(min(max(0.0, cy - h / 2), sh - h))
    return {"x": x0, "y": y0, "w": w, "h": h}


def cover_box(sw: int, sh: int, tw: int, th: int, avoid: dict | None = None) -> dict:
    """The biggest tw:th window of a sw x sh frame, centred; moved off the facecam when one is given."""
    target = tw / th
    if sw / sh > target:
        ch, cw = sh, sh * target
    else:
        cw, ch = sw, sw / target
    cw, ch = _even(cw), _even(ch)
    x0, y0 = (sw - cw) / 2, (sh - ch) / 2
    if avoid:                                         # (with a small margin: webcam frames have borders)
        mx, my = 0.02 * sw, 0.02 * sh
        ax0, ay0 = avoid["x"] - mx, avoid["y"] - my
        ax1, ay1 = avoid["x"] + avoid["w"] + mx, avoid["y"] + avoid["h"] + my
        if cw < sw:                                   # wide source: slide sideways, away from the cam
            if (ax0 + ax1) / 2 < sw / 2:
                if ax1 > x0:
                    x0 = min(ax1, sw - cw)
            elif ax0 < x0 + cw:
                x0 = max(ax0 - cw, 0)
        elif ch < sh:                                 # tall source: slide up or down instead
            if (ay0 + ay1) / 2 < sh / 2:
                if ay1 > y0:
                    y0 = min(ay1, sh - ch)
            elif ay0 < y0 + ch:
                y0 = max(ay0 - ch, 0)
    return {"x": _even(x0), "y": _even(y0), "w": cw, "h": ch}


def _post_text(hook: str, caption: str) -> str:
    tags = str(setting("hashtags") or "").split()
    have = {t.lower() for t in str(caption).split() if t.startswith("#")}
    tags = [t for t in tags if t.lower() not in have]
    parts = [str(hook).strip(), str(caption).strip(), " ".join(tags)]
    return "\n\n".join(p for p in parts if p) + "\n"


def game_box(sw: int, sh: int, cam_on: bool, cam_raw: dict | None, keep: float = 1.0) -> dict:
    """The gameplay window of the source: the right shape for the layout, off the facecam, and above any
    burned-in subtitles (only the top `keep` share of the source's height is used)."""
    usable = _even(sh * max(0.5, min(1.0, keep)))
    return cover_box(sw, usable, W, GAME_H if cam_on else H, avoid=cam_raw)


def _graph(sw: int, sh: int, cam: dict | None, cam_raw: dict | None, dur: float, cap_input: int | None,
           hook_input: int, hook_xy: tuple[int, int], hook_s: float, audio_src: str, normalise: bool,
           keep: float = 1.0) -> str:
    f = []
    if cam:
        g = game_box(sw, sh, True, cam_raw, keep)
        f.append(f"[0:v]fps={FPS},split=2[s1][s2]")
        f.append(f"[s1]crop={cam['w']}:{cam['h']}:{cam['x']}:{cam['y']},scale={W}:{CAM_H}:flags=bicubic,setsar=1[top]")
        f.append(f"[s2]crop={g['w']}:{g['h']}:{g['x']}:{g['y']},scale={W}:{GAME_H}:flags=bicubic,setsar=1[bot]")
        f.append("[top][bot]vstack=inputs=2[base]")
        cap_mid = CAM_H
    else:
        g = game_box(sw, sh, False, None, keep)
        f.append(f"[0:v]fps={FPS},crop={g['w']}:{g['h']}:{g['x']}:{g['y']},scale={W}:{H}:flags=bicubic,setsar=1[base]")
        cap_mid = round(H * CAPTION_Y_NO_CAM)
    last = "base"
    if cap_input is not None:
        cap_y = cap_mid - overlays.CAP_BAND_H // 2
        f.append(f"[{cap_input}:v]format=rgba[cap]")
        f.append(f"[{last}][cap]overlay=0:{cap_y}:eof_action=pass[withcap]")
        last = "withcap"
    fade_at = max(0.0, hook_s - HOOK_FADE)
    f.append(f"[{hook_input}:v]format=rgba,fade=t=out:st={fade_at:.3f}:d={HOOK_FADE}:alpha=1[hook]")
    f.append(f"[{last}][hook]overlay={hook_xy[0]}:{hook_xy[1]}:eof_action=pass,format=yuv420p[vout]")
    audio = f"[{audio_src}]aresample=48000"
    if normalise:
        audio += f",{normalise},aresample=48000"
    audio += (f",apad,atrim=0:{dur:.3f},afade=t=in:st=0:d={FADE},"
              f"afade=t=out:st={max(0.0, dur - FADE):.3f}:d={FADE}[aout]")
    f.append(audio)
    return ";".join(f)


def _loudness_fix(video: Path, a: float, dur: float) -> str:
    """Measure this stretch's loudness first, then lift or lower it to about -14 LUFS with a peak limiter on top.
    (A single loudnorm pass undershot quiet clips: a 30 s stream clip came out at -22.9.)"""
    fallback = "loudnorm=I=-14:TP=-1.5:LRA=11"
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(video),
                        "-vn", "-af", fallback + ":print_format=json", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        txt = p.stderr
        m = json.loads(txt[txt.rindex("{"):txt.rindex("}") + 1])
        measured = float(m["input_i"])
    except (ValueError, KeyError):
        return fallback
    if measured < -60:                          # silence: leave it alone
        return "anull"
    gain = max(-20.0, min(25.0, -14.0 - measured))
    # a gentle compressor first, so a few loud screams do not hold the whole clip down
    return f"acompressor=threshold=-22dB:ratio=3:attack=10:release=150:makeup=1,volume={gain + 2:.2f}dB,alimiter=limit=0.89:level=false"


def _render(video: Path, a: float, b: float, hook: str, words: list[dict], cam_raw: dict | None, info: dict,
            mp4: Path, work: Path, keep: float = 1.0) -> dict:
    dur = b - a
    sw, sh = info["w"], info["h"]
    cam = fit_cam(cam_raw, sw, sh)
    hook_s = min(float(setting("hook_seconds") or 3.0), dur)
    hook_png, hx, hy = overlays.hook_png(hook, work / "hook.png")
    cap_list = overlays.caption_track(words, a, b, work)

    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y", "-ss", f"{a:.3f}", "-t", f"{dur:.3f}", "-i", str(video)]
    idx = 1
    cap_input = None
    if cap_list:
        cmd += ["-f", "concat", "-safe", "0", "-i", str(cap_list)]
        cap_input, idx = idx, idx + 1
    cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{hook_s:.3f}", "-i", str(hook_png)]
    hook_input, idx = idx, idx + 1
    if info["has_audio"]:
        audio_src, normalise = "0:a", _loudness_fix(video, a, dur)
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
        audio_src, normalise = f"{idx}:a", None
        idx += 1
    avoid = {k: float(cam_raw[k]) for k in ("x", "y", "w", "h")} if cam else None
    graph = _graph(sw, sh, cam, avoid, dur, cap_input, hook_input, (hx, hy), hook_s, audio_src, normalise, keep)
    cmd += ["-filter_complex", graph, "-map", "[vout]", "-map", "[aout]", "-t", f"{dur:.3f}",
            "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-profile:v", "high", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart", str(mp4)]
    run(cmd)
    return {"cam": cam, "captions": bool(cap_list)}


def _loudness_of(mp4: Path) -> tuple[float | None, float | None]:
    """(integrated LUFS, true peak dB) of a finished file's sound."""
    import re
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-i", str(mp4), "-map", "0:a",
                        "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    i = re.findall(r"\bI:\s+(-?inf|-?[\d.]+)\s+LUFS", p.stderr or "")
    pk = re.findall(r"True peak:\s+Peak:\s+(-?inf|-?[\d.]+)", p.stderr or "")
    num = lambda v: None if "inf" in v else float(v)
    return (num(i[-1]) if i else None), (num(pk[-1]) if pk else None)


def _settle_loudness(mp4: Path, target: float = -14.0) -> str:
    """Measure the finished Short and correct it (up to twice) until it sits within 1 LU of the target with peaks
    under -1 dB. (The one-shot fix before the render can miss: a compressor on a loud clip pulled one to -21.)"""
    first = None
    for _ in range(2):
        lufs, peak = _loudness_of(mp4)
        if lufs is None or lufs < -60:
            break
        first = lufs if first is None else first
        if abs(lufs - target) <= 1.0 and (peak is None or peak <= -1.0):
            break
        delta = max(-12.0, min(15.0, target - lufs))
        tmp = mp4.with_name(mp4.stem + ".level.mp4")
        run([ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-y", "-i", str(mp4), "-map", "0:v", "-map", "0:a",
             "-c:v", "copy", "-af", f"volume={delta:.2f}dB,alimiter=limit=0.84:level=false", "-c:a", "aac",
             "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart", str(tmp)])
        tmp.replace(mp4)
    if first is None:
        return ""
    now, _ = _loudness_of(mp4)
    return f"Loudness corrected: {first:.1f} to {now:.1f} LUFS." if now is not None and abs(now - first) > 0.5 else ""


def _cover(mp4: Path, jpg: Path, at: float) -> None:
    run([ffmpeg(), "-hide_banner", "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", str(mp4), "-frames:v", "1",
         "-q:v", "2", str(jpg)])


def _trim_silence(video: Path, a: float, b: float, quiet_db: float = -50.0) -> tuple[float, float, str, str]:
    """Drop dead silence at the start and end of a moment (Twitch clips can open with seconds of muted audio),
    keeping 0.2 s of air and never less than 5 s. Silence of 2 s or more INSIDE the moment is not cut (the picture
    may be the payoff): it comes back as a problem note so the moment gets moved or split.
    Returns (a, b, trim note, problem note)."""
    step = 0.25
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(video),
                        "-vn", "-af", "aresample=48000,asetnsamples=n=12000,astats=metadata=1:reset=1,"
                        "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    levels = []
    for line in (p.stdout or "").splitlines():
        if "RMS_level=" in line:
            v = line.split("=", 1)[1].strip()
            levels.append(-200.0 if "inf" in v else float(v))
    loud = [i for i, v in enumerate(levels) if v > quiet_db]
    if not loud:
        return a, b, "", "There's no sound at all in this moment (muted?). Pick another part."
    gaps, run_start = [], None                      # silent runs between the first and last sound
    for i in range(loud[0], loud[-1] + 1):
        if levels[i] <= quiet_db:
            run_start = i if run_start is None else run_start
        elif run_start is not None:
            if (i - run_start) * step >= 2.0:
                gaps.append((run_start * step, (i - run_start) * step))
            run_start = None
    # Snappy edges: more than 0.25 s of dead air at the start or 0.9 s at the end gets trimmed (a 0.1 s breath stays).
    lead = max(0.0, loud[0] * step - 0.1)
    tail = max(0.0, (len(levels) - 1 - loud[-1]) * step - 0.1)
    if lead <= 0.25:
        lead = 0.0
    if tail <= 0.9:
        tail = 0.0
    if (b - tail) - (a + lead) < 5.0:
        lead = tail = 0.0
    problem = ""
    if gaps:
        where = ", ".join(f"{g:.0f} s ({d:.1f} s long)" for g, d in ((g - lead, d) for g, d in gaps))
        problem = (f"Silent in the middle at {where} of this Short (muted or dead air). "
                   "Move the window to skip it, or split it into two moments, then make it again.")
    if not lead and not tail:
        return a, b, "", problem
    parts = [f"{x:.1f} s of silence at the {w}" for x, w in ((lead, "start"), (tail, "end")) if x]
    return a + lead, b - tail, "Trimmed " + " and ".join(parts) + ".", problem


def _snap_to_words(words: list[dict], a: float, b: float, dur: float) -> tuple[float, float, str]:
    """Never start or end inside a word: an edge that lands in one moves out to that word's edge (at most 1.5 s)."""
    na, nb = a, b
    for w in words or []:
        try:
            ws, we = float(w["start"]), float(w["end"])
        except (KeyError, TypeError, ValueError):
            continue
        if ws < a < we - 0.03 and a - ws <= 1.5:
            na = min(na, max(0.0, ws - 0.12))
        if ws + 0.03 < b < we and we - b <= 1.5:
            nb = max(nb, min(dur or we + 0.2, we + 0.2))
    moved = [f"{x}" for x, d in (("start", a - na), ("end", nb - b)) if d > 0.01]
    return na, nb, ("Moved the " + " and ".join(moved) + " so no word is cut in half.") if moved else ""


def _scene_cuts(video: Path, a: float, b: float, cam: dict | None) -> tuple[list[float], bool]:
    """Hard cuts between a and b (video seconds). With a facecam we watch only the webcam box: a webcam only jumps
    when a compilation switches to the next streamer (or the stream zooms it). Without one, the whole picture with a
    higher bar (games cut their camera too). Returns (cut times, watched the webcam)."""
    import re
    box = None
    if cam:
        try:
            box = {k: int(cam[k]) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError):
            box = None
    crop = f"crop={box['w']}:{box['h']}:{box['x']}:{box['y']}," if box else ""
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-nostats", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
                        "-i", str(video), "-an", "-vf", f"{crop}scale=160:-2,scdet=threshold={10 if box else 14}",
                        "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.findall(r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)", p.stderr or "")
    return sorted({round(a + float(t), 2) for _, t in found}), bool(box)


def _off_the_cuts(cuts: list[float], a: float, b: float) -> tuple[float, float, str]:
    """A cut in the first 1.5 s or the last 2 s means the Short shows a sliver of another clip: start after it /
    end before it."""
    na, nb = a, b
    for c in cuts:
        if a < c <= a + 1.5:
            na = max(na, c + 0.05)
        if b - 2.0 <= c < b:
            nb = min(nb, c - 0.1)
    if nb - na < 5.0:
        return a, b, ""
    moved = [x for x, d in (("start", na - a), ("end", b - nb)) if d > 0.01]
    return na, nb, ("Moved the " + " and ".join(moved) + " off a hard cut (another clip or a zoom).") if moved else ""


def _keep_share(video: Path, a: float, b: float, words: list[dict], cam: dict | None, info: dict,
                moment: dict) -> tuple[float, str]:
    """How much of the source's height the gameplay may use, so the game's subtitles and the stream's own caption
    bars stay out of the Short (our captions would double them). "drop_bottom" on the moment or in settings:
    "auto" (find them), or a share 0-0.3 to cut by hand."""
    forced = moment.get("drop_bottom", setting("drop_bottom"))
    if forced not in (None, "", "auto"):
        try:
            cut = max(0.0, min(burned.MAX_CUT, float(forced)))
            return 1.0 - cut, (f"Cut the bottom {cut:.0%} of the game (set by hand)." if cut else "")
        except (TypeError, ValueError):
            pass
    sw, sh = info["w"], info["h"]
    fitted = fit_cam(cam, sw, sh)
    avoid = {k: float(cam[k]) for k in ("x", "y", "w", "h")} if fitted else None
    g = game_box(sw, sh, bool(fitted), avoid)
    found = burned.find(video, a, b, g["x"] / sw, (g["x"] + g["w"]) / sw, words)
    keep = burned.keep_share(found)
    if keep >= 0.999:
        return 1.0, ""
    return keep, (f"Cut the bottom {1 - keep:.0%} of the game: it carries subtitles or stream captions "
                  f"(seen in {found['frames']} of {found['of']} frames), so only our captions show.")


def build_short(video: Path, a: float, b: float, hook: str, caption: str, words: list[dict], cam: dict | None,
                out_dir: Path, name: str, moment: dict | None = None) -> dict:
    """Render one Short. Writes out_dir/name.mp4, .jpg (cover), .txt (post text) and .json (this result)."""
    video, out_dir = Path(video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    mp4, jpg, txt, js = (out_dir / f"{name}{ext}" for ext in (".mp4", ".jpg", ".txt", ".json"))
    notes: list[str] = []
    result = {"mp4": str(mp4), "cover": str(jpg), "txt": str(txt), "seconds": round(float(b) - float(a), 2),
              "ok": False, "notes": notes}
    t_start = time.time()
    try:
        txt.write_text(_post_text(hook, caption), encoding="utf-8")
        info = video_info(video)
        if not info["w"] or not info["h"]:
            raise RuntimeError(f"can't read the video {video.name}")
        a, b = max(0.0, float(a)), float(b)
        if info["dur"] and b > info["dur"]:
            notes.append(f"The end was past the video's end; cut at {info['dur']:.1f} s.")
            b = info["dur"]
        if b - a < 1.0:
            raise RuntimeError(f"the moment is too short ({b - a:.1f} s)")
        silence_problem = ""
        if info["has_audio"]:
            a, b, trim_note, silence_problem = _trim_silence(video, a, b)
            if trim_note:
                notes.append(trim_note)
        a, b, snap_note = _snap_to_words(words, a, b, info["dur"])
        if snap_note:
            notes.append(snap_note)
        cuts, cuts_on_cam = _scene_cuts(video, a, b, fit_cam(cam, info["w"], info["h"]) and cam)
        a, b, cut_note = _off_the_cuts(cuts, a, b)
        if cut_note:
            notes.append(cut_note)
        result["seconds"] = round(b - a, 2)
        result["a"], result["b"] = round(a, 2), round(b, 2)
        keep, keep_note = _keep_share(video, a, b, words, cam, info, moment or {})
        if keep_note:
            notes.append(keep_note)
        result["keep"] = keep
        with tempfile.TemporaryDirectory(prefix="gtaclip-") as tmp:
            made = _render(video, a, b, hook, words, cam, info, mp4, Path(tmp), keep)
        if cam and not made["cam"]:
            notes.append("The facecam box didn't fit the video, so the gameplay fills the whole height.")
        if info["has_audio"]:
            level_note = _settle_loudness(mp4)
            if level_note:
                notes.append(level_note)
        _cover(mp4, jpg, min(COVER_AT, (b - a) / 2))
        ctx = {"a": a, "b": b, "words": words, "hook": hook, "cam": bool(made["cam"]), "source_dur": info["dur"],
               "moment": moment or {}, "silence_problem": silence_problem,
               "cuts": [c for c in cuts if a + 0.3 < c < b - 0.3], "cuts_on_cam": cuts_on_cam}
        ok, qc_notes, measured, checks = qc.check(mp4, b - a, setting("max_short_s"), ctx)
        notes.extend(qc_notes)
        result["ok"] = ok
        result["qa"] = checks
        result["qa_sheet"] = str(qc.qa_sheet(mp4, out_dir / f"{name}_qa.jpg", b - a, a))
        passed = sum(1 for c in checks if c["pass"])
        log(f"  {name}: {b - a:.1f} s, {measured.get('lufs')} LUFS · QA {passed}/{len(checks)} checks passed · "
            f"{'ok' if ok else 'NEEDS A LOOK'} ({time.time() - t_start:.0f} s to make)")
    except Exception as e:                                  # one bad moment must not stop the others
        notes.append(f"Couldn't make this Short: {e}")
        for p in (mp4, jpg):
            if p.exists() and p.stat().st_size < 1000:
                p.unlink()
    save(js, result)
    return result
