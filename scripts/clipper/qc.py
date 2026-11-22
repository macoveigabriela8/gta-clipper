"""Checks on a finished Short before anyone sees it: length, size, loudness, no long black stretch, decodes clean."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .frames import video_info
from .paths import H, W, ffmpeg

TARGET_LUFS = -14.0
LUFS_TOLERANCE = 3.0      # outside -17 .. -11 the sound is clearly off
DUR_TOLERANCE = 0.25
BLACK_MAX_S = 1.0


def _measure(mp4: Path, has_audio: bool) -> dict:
    """One decode of the whole file: integrated loudness (ebur128) and black stretches (blackdetect)."""
    if has_audio:
        graph = f"[0:v]blackdetect=d={BLACK_MAX_S}:pix_th=0.10[v];[0:a]ebur128[a]"
        maps = ["-map", "[v]", "-map", "[a]"]
    else:
        graph = f"[0:v]blackdetect=d={BLACK_MAX_S}:pix_th=0.10[v]"
        maps = ["-map", "[v]"]
    p = subprocess.run([ffmpeg(), "-hide_banner", "-nostats", "-xerror", "-i", str(mp4), "-filter_complex", graph,
                        *maps, "-f", "null", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    err = p.stderr or ""
    lufs = None
    found = re.findall(r"\bI:\s+(-?inf|-?\d+(?:\.\d+)?)\s+LUFS", err)
    if found:
        lufs = float("-inf") if "inf" in found[-1] else float(found[-1])
    blacks = [(float(s), float(d)) for s, d in
              re.findall(r"black_start:\s*(-?[\d.]+)\s+black_end:\s*-?[\d.]+\s+black_duration:\s*([\d.]+)", err)]
    tail = "\n".join(err.strip().splitlines()[-3:])
    return {"decodes": p.returncode == 0, "lufs": lufs, "blacks": blacks, "tail": tail}


def check(mp4: Path, want_s: float, max_s: float | None = None) -> tuple[bool, list[str], dict]:
    """(ok, notes, measured). Notes are plain sentences, only for what's off; too long is a warning, not a fail."""
    mp4 = Path(mp4)
    notes: list[str] = []
    if not mp4.exists() or mp4.stat().st_size < 10_000:
        return False, ["The Short wasn't written (file missing or empty)."], {}
    info = video_info(mp4)
    m = _measure(mp4, info["has_audio"])
    measured = {"seconds": round(info["dur"], 2), "w": info["w"], "h": info["h"], "lufs": m["lufs"],
                "black": m["blacks"]}
    ok = True

    if not m["decodes"]:
        ok = False
        notes.append(f"The file doesn't play back cleanly: {m['tail']}")
    if abs(info["dur"] - want_s) > DUR_TOLERANCE:
        ok = False
        notes.append(f"It's {info['dur']:.2f} s long but should be {want_s:.2f} s.")
    if (info["w"], info["h"]) != (W, H):
        ok = False
        notes.append(f"The picture is {info['w']}x{info['h']}, not {W}x{H}.")
    if not info["has_audio"]:
        ok = False
        notes.append("There's no sound track.")
    elif m["lufs"] is None:
        ok = False
        notes.append("Couldn't measure how loud it is.")
    elif m["lufs"] == float("-inf") or m["lufs"] < -60:
        ok = False
        notes.append("The sound is silent.")
    elif abs(m["lufs"] - TARGET_LUFS) > LUFS_TOLERANCE:
        ok = False
        notes.append(f"Loudness is {m['lufs']:.1f} LUFS; it should be about {TARGET_LUFS:.0f}.")
    for start, length in m["blacks"]:
        if length > BLACK_MAX_S:
            ok = False
            notes.append(f"The picture is black for {length:.1f} s from {start:.1f} s.")
    if max_s and info["dur"] > float(max_s) + 0.01:
        notes.append(f"Heads-up: it's {info['dur']:.0f} s, longer than {float(max_s):.0f} s "
                     "(too long for YouTube Shorts; fine for TikTok).")
    return ok, notes, measured
