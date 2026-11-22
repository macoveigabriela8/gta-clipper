"""QA on every finished Short, before anyone sees it. Each check is one rule from reference/cutting-sop.md with a
measured threshold. "fail" = the Short comes back NEEDS A LOOK and must be fixed; "warn" = worth a look.
Also writes the QA sheet (first 3 s, the middle, the last 4 s) that Claude reads with its own eyes."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np

from . import burned
from .frames import grab_frames, video_info
from .paths import H, W, ffmpeg, seconds

TARGET_LUFS = -14.0
LUFS_TOLERANCE = 3.0      # outside -17 .. -11 the sound is clearly off
PEAK_MAX = -0.5           # true peak (dBTP): above it, phones distort
DUR_TOLERANCE = 0.25
BLACK_MAX_S = 1.0
FREEZE_MAX_S = 2.5        # the picture stands still this long: a frozen stream or a pause menu
SILENCE_MAX_S = 2.0       # dead air this long inside a Short makes people scroll
QUIET_DB = -35            # on the finished Short (about -14 LUFS), under -35 dB is dead air to a viewer
DARK_LUMA = 35            # gameplay darker than this (0-255) on average can't be seen on a phone
MIN_WORDS_PER_S = 0.5     # under this the captions are mostly empty: the streamer is quiet, only the game talks
PAYOFF_GAP = (0.3, 4.5)   # end this many seconds after the payoff
CAM_BAND = 740            # facecam band height in the Short
SENTENCE_END = (".", "?", "!", "…")


def _measure(mp4: Path, has_audio: bool) -> dict:
    """One decode of the whole file: loudness + true peak (ebur128), silences, black and frozen stretches."""
    v = f"[0:v]blackdetect=d={BLACK_MAX_S}:pix_th=0.10,freezedetect=n=-50dB:d={FREEZE_MAX_S}[v]"
    if has_audio:
        graph = f"{v};[0:a]ebur128=peak=true,silencedetect=n={QUIET_DB}dB:d={SILENCE_MAX_S}[a]"
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        graph, maps = v, ["-map", "[v]"]
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-xerror", "-i", str(mp4), "-filter_complex", graph,
                        *maps, "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    err = p.stderr or ""
    num = lambda x: float("-inf") if "inf" in x else float(x)
    found = re.findall(r"\bI:\s+(-?inf|-?\d+(?:\.\d+)?)\s+LUFS", err)
    peak = re.findall(r"True peak:\s+Peak:\s+(-?inf|-?\d+(?:\.\d+)?)", err)
    return {
        "decodes": p.returncode == 0,
        "lufs": num(found[-1]) if found else None,
        "peak": num(peak[-1]) if peak else None,
        "blacks": [(float(s), float(d)) for s, d in
                   re.findall(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*-?[\d.]+\s+black_duration:\s*([\d.]+)", err)],
        "freezes": [(float(s), float(d)) for s, d in
                    re.findall(r"freeze_start:\s*([\d.]+)[\s\S]*?freeze_duration:\s*([\d.]+)", err)],
        "silences": [(float(e) - float(d), float(d)) for e, d in
                     re.findall(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)", err)],
        "tail": "\n".join(err.strip().splitlines()[-3:]),
    }


def _look_at_bands(mp4: Path, dur: float, cam: bool) -> dict:
    """Frames after the hook: is the top band a webcam (a whole face in most frames, the same room behind it:
    change ~0.02-0.06, a game scene or a zoom changes more), and how dark is the gameplay?"""
    from .facecam import _faces
    start = 3.2 if dur > 7 else 0.3                  # after the hook pill
    times = [float(t) for t in np.linspace(start, max(start + 0.1, dur - 0.3), 12)]
    frames = grab_frames(mp4, times, 540)
    if not frames:
        return {"of": 0}
    band_h = round(frames[0].height * CAM_BAND / H) if cam else 0
    game = [np.asarray(f.crop((0, band_h, f.width, f.height)).convert("L"), dtype=float) for f in frames]
    dark = sum(1 for g in game if g.mean() < DARK_LUMA)
    out = {"of": len(frames), "dark_frames": dark}
    if cam:
        bands = [f.crop((0, 0, f.width, band_h)) for f in frames]
        whole = lambda y, fh: fh >= 0.12 * band_h and y > 2 and y + fh < band_h - 2
        out["face_frames"] = sum(1 for b in bands if any(whole(y, fh) for (_, y, _, fh) in _faces(b)))
        tiles = np.stack([np.asarray(b.convert("L").resize((48, 32)), dtype=float) for b in bands])
        out["change"] = round(float(np.mean(np.abs(tiles - np.median(tiles, axis=0))) / 255), 3)
    return out


def _words_in(words: list[dict], a: float, b: float) -> list[dict]:
    out = []
    for w in words or []:
        try:
            if a - 0.05 <= float(w["start"]) < b:
                out.append(w)
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _cut_checks(ctx: dict, dur: float) -> list[dict]:
    """The editor's checks: whole words, the ending on a payoff, not the end of somebody else's clip, people talk."""
    out = []
    add = lambda cid, level, ok, say: out.append({"check": cid, "level": level, "pass": bool(ok), "say": say})
    a, b = float(ctx.get("a", 0)), float(ctx.get("b", dur))
    words, moment = ctx.get("words") or [], ctx.get("moment") or {}

    timed = []
    for w in words:
        try:
            timed.append((float(w["start"]), float(w["end"]), str(w.get("w", ""))))
        except (KeyError, TypeError, ValueError):
            continue
    cut_start = [t for t in timed if t[0] < a - 0.03 and t[1] > a + 0.05]
    add("clean_start", "warn", not cut_start,
        f'Starts in the middle of the word "{cut_start[0][2]}".' if cut_start else "")
    cut_end = [t for t in timed if t[0] < b - 0.05 and t[1] > b + 0.03]
    if cut_end:
        add("clean_end", "warn", False, f'Ends in the middle of the word "{cut_end[0][2]}".')
    else:
        before = [t for t in timed if t[1] <= b + 0.05]
        after = [t for t in timed if t[0] >= b - 0.05]
        mid = (before and after and not before[-1][2].strip().endswith(SENTENCE_END)
               and after[0][0] - b < 0.6 and b - before[-1][1] < 0.8)
        tail = " ".join(t[2].strip() for t in before[-4:]) if before else ""
        add("clean_end", "warn", not mid,
            f'Ends mid-sentence ("…{tail}"): the next word comes right after the cut. End after the sentence.'
            if mid else "")

    pay = moment.get("payoff_at")
    if pay is None:
        add("payoff", "fail", False, 'No payoff marked. Add "payoff" (what happens) and "payoff_at" (its second in '
            "the video) to this moment, so the Short can be checked to end on it.")
    else:
        try:
            pay_s = seconds(pay)
        except (TypeError, ValueError):
            pay_s = None
        if pay_s is None:
            add("payoff", "fail", False, f'"payoff_at" should be a second of the video, not {pay!r}.')
        elif pay_s < a + 1:
            add("payoff", "fail", False, f"The payoff ({pay_s:.1f} s) is before the Short starts ({a:.1f} s).")
        elif b - pay_s < PAYOFF_GAP[0]:
            add("payoff", "fail", False, f"The Short ends before its payoff ({b - pay_s:+.1f} s). End 1-3 s after it.")
        elif b - pay_s > PAYOFF_GAP[1]:
            add("payoff", "warn", False, f"It keeps going {b - pay_s:.1f} s after the payoff. End 1-3 s after it.")
        else:
            add("payoff", "fail", True, "")

    src = float(ctx.get("source_dur") or 0)
    at_end = bool(src) and b >= src - 0.6
    add("link_end", "fail", (not at_end) or bool(moment.get("end_checked")),
        "This Short runs to the very end of the link, so it may stop mid-scene (a clip somebody else cut). Look at "
        'the last frames on the QA sheet: if the payoff is there, add "end_checked": true to the moment; if not, ask '
        "the user for a longer link (the full stream / VOD)." if at_end else "")
    add("link_start", "warn", not (src and src < 180 and a <= 0.3),
        "It starts at the very start of the link: the setup may be missing." if src and src < 180 and a <= 0.3 else "")

    n = len(_words_in(words, a, b))
    rate = n / max(dur, 0.1)
    add("talking", "warn", rate >= MIN_WORDS_PER_S,
        f"Only {n} words are said in {dur:.0f} s: the captions are mostly empty and the streamer may be quiet here "
        "(only the game is heard). A moment where the streamer reacts out loud works better."
        if rate < MIN_WORDS_PER_S else "")

    hook = str(ctx.get("hook") or "")
    hw = hook.split()
    emoji = any(ord(ch) >= 0x2600 and ch not in "…" for ch in hook)
    add("hook", "warn", 3 <= len(hw) <= 10 and "#" not in hook and not emoji,
        f'The hook "{hook}" should be 5-9 plain words (no emoji, no hashtags).'
        if not (3 <= len(hw) <= 10 and "#" not in hook and not emoji) else "")
    return out


def check(mp4: Path, want_s: float, max_s: float | None = None, ctx: dict | None = None
          ) -> tuple[bool, list[str], dict, list[dict]]:
    """(ok, notes, measured, checks). Notes are plain sentences, only for what's off."""
    mp4, ctx = Path(mp4), ctx or {}
    checks: list[dict] = []
    add = lambda cid, level, ok, say: checks.append({"check": cid, "level": level, "pass": bool(ok), "say": say})
    if not mp4.exists() or mp4.stat().st_size < 10_000:
        add("plays", "fail", False, "The Short wasn't written (file missing or empty).")
        return False, [checks[0]["say"]], {}, checks
    info = video_info(mp4)
    dur = info["dur"] or want_s
    m = _measure(mp4, info["has_audio"])
    measured = {"seconds": round(info["dur"], 2), "w": info["w"], "h": info["h"], "lufs": m["lufs"],
                "peak": m["peak"], "black": m["blacks"], "frozen": m["freezes"], "silent": m["silences"]}

    # the file
    add("plays", "fail", m["decodes"], "" if m["decodes"] else f"The file doesn't play back cleanly: {m['tail']}")
    add("length", "fail", abs(info["dur"] - want_s) <= DUR_TOLERANCE,
        f"It's {info['dur']:.2f} s long but should be {want_s:.2f} s.")
    add("size", "fail", (info["w"], info["h"]) == (W, H), f"The picture is {info['w']}x{info['h']}, not {W}x{H}.")
    if max_s and info["dur"] > float(max_s) + 0.01:
        add("shorts_length", "warn", False, f"It's {info['dur']:.0f} s: longer than {float(max_s):.0f} s is too long "
            "for YouTube Shorts (fine for TikTok).")

    # the sound
    if not info["has_audio"]:
        add("sound", "fail", False, "There's no sound track.")
    elif m["lufs"] is None or m["lufs"] == float("-inf") or m["lufs"] < -60:
        add("sound", "fail", False, "The sound is silent." if m["lufs"] is not None else "Couldn't measure the sound.")
    else:
        add("sound", "fail", True, "")
        add("loudness", "fail", abs(m["lufs"] - TARGET_LUFS) <= LUFS_TOLERANCE,
            f"Loudness is {m['lufs']:.1f} LUFS; it should be about {TARGET_LUFS:.0f}.")
        if m["peak"] is not None:
            add("peak", "warn", m["peak"] <= PEAK_MAX, f"Peaks hit {m['peak']:.1f} dB: it may crackle on phones.")
    long_quiet = [(s, d) for s, d in m["silences"] if d >= SILENCE_MAX_S and 0.3 < s and s + d < dur - 0.3]
    problem = str(ctx.get("silence_problem") or "")
    add("silence", "fail", not long_quiet and not problem,
        problem or ("Nearly silent for " + ", ".join(f"{d:.1f} s at {s:.1f} s" for s, d in long_quiet) +
                    " (dead air). Move the window to skip it, or split it into two moments."))

    # the picture
    blacks = [(s, d) for s, d in m["blacks"] if d > BLACK_MAX_S]
    add("black", "fail", not blacks, "; ".join(f"Black for {d:.1f} s from {s:.1f} s." for s, d in blacks))
    add("frozen", "fail", not m["freezes"],
        "; ".join(f"The picture stands still for {d:.1f} s from {s:.1f} s (frozen stream or a pause menu)."
                  for s, d in m["freezes"]))
    left = burned.left_in_short(mp4, dur)
    add("no_double_captions", "fail", left is None,
        f"Text still shows low in the gameplay ({left['frames']} of {left['of']} frames, at {left['top']:.0%} of the "
        'height): game subtitles or stream captions next to ours. Set "drop_bottom" on this moment (e.g. 0.25).'
        if left else "")
    look = _look_at_bands(mp4, dur, bool(ctx.get("cam")))
    of = look.get("of") or 0
    if ctx.get("cam") and of:
        hit, change = look["face_frames"], look["change"]
        good = hit >= max(3, round(0.5 * of)) and change <= 0.12
        add("webcam", "fail", good,
            f"The top band may not show the streamer's whole face (a whole face in {hit} of {of} frames, background "
            f"change {change:.2f}): a game scene, a zoomed webcam or the face cut off. Look at the QA sheet; if it "
            'is not the webcam, set "facecam": false on the moment (or give "cam" by hand).' if not good else "")
    if of:
        add("bright_enough", "warn", look["dark_frames"] <= of // 2,
            f"The gameplay is very dark in {look['dark_frames']} of {of} frames: hard to see on a phone."
            if look["dark_frames"] > of // 2 else "")
    measured["bands"] = look
    cuts = [round(c - float(ctx.get("a", 0)), 1) for c in ctx.get("cuts") or []]
    if ctx.get("cuts_on_cam"):
        add("one_clip", "fail", not cuts,
            f"The webcam jumps at {', '.join(f'{c:.1f} s' for c in cuts)}: another streamer's clip or a zoom starts "
            "inside the Short. Stay inside one clip (end before it, or start after it)." if cuts else "")

    if not ctx.get("top5"):                  # a Top 5 is five moments: it gets per-piece checks instead (top5.py)
        checks += _cut_checks(ctx, float((ctx.get("replay") or {}).get("main_s") or dur))   # a..b = the main part
    if ctx.get("replay"):                    # format 5: the replay's own checks (replay.py)
        from . import replay
        checks += replay.checks(mp4, ctx["replay"])
    ok = all(c["pass"] for c in checks if c["level"] == "fail")
    notes = [("✗ " if c["level"] == "fail" else "! ") + c["say"] for c in checks if not c["pass"] and c["say"]]
    return ok, notes, measured, checks


def qa_sheet(mp4: Path, out_jpg: Path, dur: float, a: float = 0.0) -> Path:
    """Frames to judge by eye: the hook (first 3 s), the middle every ~3 s, and the LAST 4 s second by second."""
    from .sheets import sheet_at
    head = [0.1, 1.5, 2.9]
    body = [float(t) for t in np.arange(4.5, max(4.5, dur - 4.5), 3.0)]
    tail = [max(0.0, dur - x) for x in (4.0, 3.0, 2.0, 1.0, 0.1)]
    times = sorted({round(t, 2) for t in head + body + tail if 0 <= t <= dur})
    label = lambda t: f"{t:.1f}s" + ("  END" if t >= dur - 4.05 else "") + f"  (src {a + t:.1f})"
    return sheet_at(Path(mp4), Path(out_jpg), times, f"{Path(mp4).stem} · QA: hook, middle, last 4 s", label,
                    cols=6, width=216)
