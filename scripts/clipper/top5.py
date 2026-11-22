"""Top 5 countdown: five short moments of one stream in a single Short, worst (#5) first, best (#1) last.
Every piece is an ordinary Short made by render.build_short, whose black pill reads "#3 LABEL" for the whole
piece (the first piece also carries the hook above it). The five pieces are joined with one re-encode, a generated
whoosh on each rank change, levelled, then checked like any Short plus the countdown's own rules (reference/cutting-sop.md § 7)."""
from __future__ import annotations

import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from . import overlays, qc, render
from .frames import grab_frames, video_info
from .paths import FPS, ffmpeg, log, run, save, seconds, setting, slug

PIECE_S = (3.0, 10.0)       # every piece lasts this long
PAYOFF_AFTER = (0.3, 3.0)   # a piece ends this long after its payoff
APART_S = 1.0               # pieces never share seconds of the video: at least this gap between two
PILL_MATCH = 0.7            # share of the pill's pixels that must look like the pill on the finished Short
WHOOSH_UNDER = (8.0, 10.0)  # the whoosh at each rank change sits this many dB under the game (aim: the middle)
WHOOSH_RISE, WHOOSH_FALL = 0.15, 0.3   # it swells for 0.15 s, is loudest right on the cut, then dies in 0.3 s


def pill_text(rank: int, label: str, hook: str = "") -> str:
    tag = f"#{rank} " + " ".join(str(label).split())
    return f"{hook}\n{tag}" if hook else tag


def whoosh_track(cuts: list[float], length: float, out: Path, gain_db: float = 0.0) -> Path:
    """One sound track holding a whoosh on every cut, generated here (noise shaped into a quick swell and
    band-limited so it reads as rushing air), so there's no sound file to license."""
    swells = "+".join(
        f"between(t,{c - WHOOSH_RISE:.3f},{c + WHOOSH_FALL:.3f})*pow(if(lt(t,{c:.3f}),"
        f"(t-{c - WHOOSH_RISE:.3f})/{WHOOSH_RISE},({c + WHOOSH_FALL:.3f}-t)/{WHOOSH_FALL}),2)" for c in cuts) or "0"
    run([ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"aevalsrc='(2*random(0)-1)*({swells})':s=48000:d={length:.3f}",
         "-af", f"highpass=f=250,lowpass=f=4500,volume={gain_db:.2f}dB", "-ac", "2", "-ar", "48000", str(out)])
    return out


def _join(pieces: list[Path], out: Path, whoosh: Path | None = None) -> None:
    """The finished pieces back to back (hard cuts), one re-encode with the same settings as a normal Short.
    The whoosh track is added on top as is (amerge + pan: a plain sum, no level change to the game)."""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-y"]
    for p in pieces:
        cmd += ["-i", str(p)]
    n = len(pieces)
    graph = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[v][a]"
    if whoosh:
        cmd += ["-i", str(whoosh)]
        graph = (graph.replace("[v][a]", "[v][game]") +
                 f";[game][{n}:a]amerge=inputs=2,pan=stereo|c0=c0+c2|c1=c1+c3[a]")
    cmd += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-r", str(FPS), "-c:v", "libx264",
            "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac",
            "-b:a", "160k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", str(out)]
    run(cmd)


def _pill_shows(mp4: Path, piece: dict, work: Path) -> float:
    """How much of this piece's pill is really on screen half-way through it (0-1): the pill drawn again and laid
    over the same spot of the finished frame, pixel by pixel."""
    png, x, y = overlays.hook_png(piece["text"], work / f"check_{piece['rank']}.png")
    pill = np.asarray(Image.open(png).convert("RGBA"), dtype=float)
    frame = grab_frames(mp4, [(piece["t0"] + piece["t1"]) / 2], 1080)
    if not frame:
        return 0.0
    spot = np.asarray(frame[0].convert("RGB"), dtype=float)[y:y + pill.shape[0], x:x + pill.shape[1]]
    if spot.shape[:2] != pill.shape[:2]:
        return 0.0
    solid = pill[..., 3] > 200
    close = np.abs(spot - pill[..., :3]).mean(axis=2) < 60
    return float(close[solid].mean()) if solid.any() else 0.0


def checks(items: list[dict], pieces: list[dict], words: list[dict], hook: str, total: float,
           mp4: Path, work: Path, whoosh: dict | None = None) -> list[dict]:
    """The countdown's own rules, in qc.py's shape."""
    out: list[dict] = []
    add = lambda cid, level, ok, say: out.append({"check": cid, "level": level, "pass": bool(ok), "say": say})

    ranks = sorted(int(i.get("rank", 0)) for i in items)
    add("ranks", "fail", ranks == [1, 2, 3, 4, 5],
        f"The ranks are {ranks}: a Top 5 needs exactly 5, 4, 3, 2 and 1, each once.")

    bad = [f"#{p['rank']} is {p['b'] - p['a']:.1f} s" for p in pieces
           if not PIECE_S[0] <= p["b"] - p["a"] <= PIECE_S[1]]
    add("item_length", "fail", not bad, "; ".join(bad) + f" (each piece {PIECE_S[0]:.0f}-{PIECE_S[1]:.0f} s).")

    by_time = sorted(pieces, key=lambda p: p["a"])
    close = [f"#{p['rank']} and #{q['rank']}" for p, q in zip(by_time, by_time[1:]) if q["a"] - p["b"] < APART_S]
    add("items_apart", "fail", not close,
        "These pieces share seconds of the video (or sit under 1 s apart): " + ", ".join(close) +
        ". Each piece must be its own moment.")

    pay_bad = []
    for p in pieces:
        try:
            pay = seconds(p["payoff_at"])
        except (KeyError, TypeError, ValueError):
            pay_bad.append(f"#{p['rank']} has no payoff_at")
            continue
        if not p["a"] < pay < p["b"]:
            pay_bad.append(f"#{p['rank']}'s payoff ({pay:.1f} s) is outside it ({p['a']:.1f}-{p['b']:.1f})")
        elif not PAYOFF_AFTER[0] <= p["b"] - pay <= PAYOFF_AFTER[1]:
            pay_bad.append(f"#{p['rank']} ends {p['b'] - pay:.1f} s after its payoff")
    add("item_payoff", "fail", not pay_bad,
        "; ".join(pay_bad) + f" (end each piece {PAYOFF_AFTER[0]}-{PAYOFF_AFTER[1]:.0f} s after its payoff).")

    shown = {p["rank"]: _pill_shows(mp4, p, work) for p in pieces}
    hidden = [f"#{r} ({s:.0%})" for r, s in shown.items() if s < PILL_MATCH]
    add("badge_visible", "fail", not hidden,
        "The rank pill isn't clearly on screen for " + ", ".join(hidden) + ". Look at the QA sheet.")

    max_s = float(setting("max_short_s") or 59)
    add("total_length", "warn", total <= max_s + 0.01,
        f"It's {total:.1f} s: over {max_s:.0f} s is too long for YouTube Shorts. Shorten a piece.")

    wordy = [f"#{p['rank']} \"{p['label']}\"" for p in pieces if not 2 <= len(str(p["label"]).split()) <= 4]
    add("labels", "warn", not wordy, "Labels should be 2-4 words: " + ", ".join(wordy) + ".")

    cut = []
    for p in pieces:
        for w in words or []:
            try:
                ws, we = float(w["start"]), float(w["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if (ws < p["a"] - 0.03 and we > p["a"] + 0.05) or (ws < p["b"] - 0.05 and we > p["b"] + 0.03):
                cut.append(f"#{p['rank']} (\"{str(w.get('w', '')).strip()}\")")
                break
    add("item_edges", "warn", not cut, "A word is cut in half at the edge of " + ", ".join(cut) + ".")

    if whoosh is not None:
        under, n = whoosh.get("under"), whoosh.get("n", 0)
        good = n == len(pieces) - 1 and under is not None and WHOOSH_UNDER[0] <= under <= WHOOSH_UNDER[1]
        add("whoosh", "warn", good,
            "No whoosh in the mix." if under is None else
            f"{n} whooshes for {len(pieces) - 1} rank changes, {under:.1f} dB under the game "
            f"(want one per change, {WHOOSH_UNDER[0]:.0f}-{WHOOSH_UNDER[1]:.0f} dB under).")

    hw = hook.split()
    plain = 3 <= len(hw) <= 8 and "#" not in hook and not any(ord(c) >= 0x2600 and c != "…" for c in hook)
    add("hook", "warn", plain, f'The hook "{hook}" should be 3-8 plain words (no emoji, no hashtags).')
    return out


def qa_sheet(mp4: Path, out_jpg: Path, pieces: list[dict]) -> Path:
    """Row 1: the first frame of #5..#1. Row 2: their last frame. Each labelled with its rank and source second."""
    from .sheets import sheet_at
    marks = {}
    for p in pieces:
        marks[round(p["t0"] + 0.1, 2)] = f"#{p['rank']} first (src {p['a']:.1f})"
    for p in pieces:
        marks[round(p["t1"] - 0.15, 2)] = f"#{p['rank']} last (src {p['b']:.1f})"
    times = list(marks)
    return sheet_at(mp4, out_jpg, times, f"{mp4.stem} · QA: first and last frame of each rank",
                    lambda t: marks.get(round(t, 2), f"{t:.1f}s"), cols=len(pieces), width=216)


def build(video: Path, plan: dict, words: list[dict], cam_for, out_dir: Path) -> dict:
    """Render plan["top5"] = {"hook", "caption", "items": [{"rank", "a", "b", "label", "payoff_at", ...}]}.
    cam_for(item, a, b) -> facecam box or None. Writes top5_<slug>.mp4 / .jpg / .txt / .json / _qa.jpg."""
    video, out_dir = Path(video), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hook = " ".join(str(plan.get("hook") or "").split())
    caption = str(plan.get("caption") or "").strip()
    items = [i for i in plan.get("items") or [] if isinstance(i, dict)]
    name = f"top5_{slug(hook or 'countdown')}"
    mp4, jpg, txt, js = (out_dir / f"{name}{ext}" for ext in (".mp4", ".jpg", ".txt", ".json"))
    notes: list[str] = []
    result = {"mp4": str(mp4), "cover": str(jpg), "txt": str(txt), "ok": False, "notes": notes, "format": "top5"}
    t_start = time.time()
    try:
        txt.write_text(render._post_text(hook, caption), encoding="utf-8")
        src = video_info(video)
        order = sorted(items, key=lambda i: -int(i.get("rank", 0)))          # #5 plays first, #1 last
        pieces: list[dict] = []
        log("  (each piece prints its own quick check; only the joined Top 5's QA counts)")
        with tempfile.TemporaryDirectory(prefix="gtaclip-top5-") as tmp:
            work, t = Path(tmp), 0.0
            for n, it in enumerate(order):
                rank = int(it.get("rank", 0))
                a, b = seconds(it["a"]), seconds(it["b"])
                cam = cam_for(it, a, b)
                text = pill_text(rank, it.get("label", ""), hook if n == 0 else "")
                made = render.build_short(video, a, b, text, "", words, cam, work, f"rank{rank}",
                                          moment={**it, "hook_hold": 999})
                if not Path(made["mp4"]).exists():
                    raise RuntimeError(f"#{rank} couldn't be made: " + " ".join(made.get("notes") or []))
                notes += [f"#{rank}: {x}" for x in made.get("notes") or [] if not x.startswith(("✗", "!"))]
                # the webcam is judged per piece: each piece has its own cam crop, so across the joined Short the
                # band "changes background" at every cut even when it's the right face
                cam_say = [c["say"] for c in made.get("qa") or [] if c["check"] == "webcam" and not c["pass"]]
                d = video_info(Path(made["mp4"]))["dur"]
                pieces.append({"rank": rank, "label": str(it.get("label", "")), "payoff_at": it.get("payoff_at"),
                               "a": float(made.get("a", a)), "b": float(made.get("b", b)), "t0": t, "t1": t + d,
                               "text": text, "mp4": made["mp4"], "cam_say": cam_say,
                               "cam": render.fit_cam(cam, src["w"], src["h"]) is not None})
                t += d
            # the whoosh: measured against the pieces' own loudness, then set WHOOSH_UNDER's middle below it
            heard = [x for x in (render._loudness_of(Path(p["mp4"]))[0] for p in pieces) if x is not None and x > -60]
            game = sum(heard) / len(heard) if heard else -14.0
            cuts = [p["t0"] for p in pieces[1:]]
            wav = whoosh_track(cuts, t + 0.5, work / "whoosh.wav")
            raw = render._loudness_of(wav)[0]
            if raw is not None and raw > -70:
                whoosh_track(cuts, t + 0.5, wav, max(-40.0, min(20.0, game - sum(WHOOSH_UNDER) / 2 - raw)))
                level = render._loudness_of(wav)[0]
            else:
                level = None
            whoosh = {"n": len(cuts) if level is not None else 0, "under": None if level is None else game - level}
            _join([Path(p["mp4"]) for p in pieces], mp4, wav if level is not None else None)
            level = render._settle_loudness(mp4)
            if level:
                notes.append(level)
            render._cover(mp4, jpg, render.COVER_AT)
            ok, qc_notes, measured, found = qc.check(mp4, t, setting("max_short_s"), {"top5": True, "cam": False})
            if any(p["cam"] for p in pieces):
                bad = [f"#{p['rank']}: {p['cam_say'][0]}" for p in pieces if p["cam"] and p["cam_say"]]
                found.append({"check": "webcam", "level": "fail", "pass": not bad, "say": " ".join(bad)})
            found += checks(items, pieces, words, hook, video_info(mp4)["dur"], mp4, work, whoosh)
        ok = all(c["pass"] for c in found if c["level"] == "fail")
        notes += [("✗ " if c["level"] == "fail" else "! ") + c["say"] for c in found if not c["pass"] and c["say"]]
        result.update(ok=ok, qa=found, seconds=round(t, 2), lufs=measured.get("lufs"),
                      pieces=[{k: p[k] for k in ("rank", "label", "a", "b", "t0", "t1")} for p in pieces])
        result["qa_sheet"] = str(qa_sheet(mp4, out_dir / f"{name}_qa.jpg", pieces))
        passed = sum(1 for c in found if c["pass"])
        log(f"  {name}: {t:.1f} s, {measured.get('lufs')} LUFS · QA {passed}/{len(found)} checks passed · "
            f"{'ok' if ok else 'NEEDS A LOOK'} ({time.time() - t_start:.0f} s to make)")
    except Exception as e:
        notes.append(f"Couldn't make the Top 5: {e}")
    save(js, result)
    return result
