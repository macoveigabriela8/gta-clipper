# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = [
#   "numpy<2.3",
#   "opencv-python-headless>=4.8,<5",
#   "pillow",
#   "faster-whisper",
#   "imageio-ffmpeg",
#   "yt-dlp[default]",
#   "deno",
#   "certifi",
# ]
# ///
"""GTA Clipper: one stream link (or video file) in, finished vertical Shorts out.

  gc setup                         install check + speech model (once)
  gc doctor                        check every piece, change nothing
  gc get <link or file>            download, listen, write scan/chunk_*.md      [--minutes 40]
  gc look <video> --from 6:40 --to 7:16 [--every 2]    contact sheet of that window
  gc make [project] [--no-open]    render every moment in plan.json, open the review page
  gc review [day]                  rebuild and open the review page of a day

Run it as: uv run --python 3.12 <skill folder>/scripts/gc.py <command>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

for _stream in (sys.stdout, sys.stderr):        # Windows consoles: print ✓ and 🔥 instead of crashing
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
try:                                            # certifi's certificates for every download (python.org Macs, Windows)
    import certifi
    for _var in ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        os.environ.setdefault(_var, certifi.where())
except Exception:
    pass
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")      # no "set a HF_TOKEN" nag: the model is public
os.environ.setdefault("CT2_VERBOSE", "-1")              # speech engine: errors only

sys.path.insert(0, str(Path(__file__).resolve().parent))
from clipper import paths  # noqa: E402
from clipper.paths import log  # noqa: E402


# ---------- finding things ----------

def _projects() -> list[Path]:
    """Every project, oldest plan.json change first."""
    plans = paths.PROJECTS.glob("*/plan.json") if paths.PROJECTS.exists() else []
    return [p.parent for p in sorted(plans, key=lambda p: p.stat().st_mtime)]


def _project(arg: str | None) -> Path:
    """A project folder from its path, its name, its plan.json, or nothing (= the newest one)."""
    if not arg:
        found = _projects()
        if not found:
            raise SystemExit("No project yet. Start with: gc get <link or file>")
        return found[-1]
    raw = arg.strip().strip('"').strip("'")
    for cand in (Path(os.path.expanduser(raw)), paths.PROJECTS / raw):
        if cand.is_file() and cand.name == "plan.json":
            return cand.parent
        if (cand / "plan.json").is_file():
            return cand
    recent = ", ".join(p.name for p in _projects()[-5:]) or "none"
    raise SystemExit(f"No project called {raw}. Recent ones: {recent}")


def _plan(proj: Path) -> dict:
    f = proj / "plan.json"
    try:
        plan = json.loads(f.read_text(encoding="utf-8"))
    except ValueError as e:
        raise SystemExit(f"{f} isn't valid JSON ({e}). Fix the commas / quotes and run it again.")
    if not isinstance(plan, dict):
        raise SystemExit(f"{f} should hold {{\"source\": ..., \"moments\": [...]}}.")
    return plan


def _source(plan: dict, proj: Path) -> Path:
    src = plan.get("source") or plan.get("file")
    if not src or not Path(src).is_file():
        raise SystemExit(f"The video of {proj.name} is missing ({src}). Run gc get again.")
    return Path(src)


def _video_and_project(arg: str) -> tuple[Path, Path | None]:
    """`gc look` takes a video file or a project; returns the video and its project (if one uses it)."""
    p = Path(os.path.expanduser(arg.strip().strip('"').strip("'")))
    if p.is_file() and p.name != "plan.json":
        video = p.resolve()
        for proj in reversed(_projects()):
            src = (paths.load(proj / "plan.json", {}) or {}).get("source")
            if src and Path(src) == video:
                return video, proj
        return video, None
    proj = _project(arg)
    return _source(_plan(proj), proj), proj


# ---------- commands ----------

def cmd_setup(a) -> int:
    from clipper import setup
    return 0 if setup.setup() else 1


def cmd_doctor(a) -> int:
    from clipper import setup
    return 0 if setup.setup(doctor_only=True) else 1


def cmd_get(a) -> int:
    from clipper import fetch, hearing, hotspots
    t0 = time.time()
    proj = fetch.get(a.link, a.minutes)
    video = _source(_plan(proj), proj)
    hearing.transcribe(video, proj)
    chunks = hotspots.scan(video, proj)
    log(f"\nDone in {time.time() - t0:.0f} s. Read every notes file:")
    for c in chunks:
        log(f"  {c}")
    log(f"Video: {video}")
    log(f"Plan:  {proj / 'plan.json'}")
    log("Next: check each window with gc look, write 2-3 moments into the plan, then: "
        f'gc make "{proj}"')
    return 0


def cmd_look(a) -> int:
    from clipper import frames, sheets
    video, proj = _video_and_project(a.video)
    dur = frames.video_info(video)["dur"]
    start = paths.seconds(a.start) if a.start else 0.0
    end = paths.seconds(a.end) if a.end else start + 36
    if dur:
        start, end = min(start, max(0.0, dur - 0.5)), min(end, dur - 0.05)
    if end <= start:
        raise SystemExit(f"--to must be after --from (the video is {paths.clock(dur)} long).")
    out_dir = proj / "looks" if proj else paths.WORK / "looks"
    out = out_dir / f"{paths.slug(video.stem, 24)}_{start:.0f}-{end:.0f}_every{a.every:g}.jpg"
    sheets.contact_sheet(video, out, start, end, every=a.every)
    log(f"✓ contact sheet {paths.clock(start)}-{paths.clock(end)}: {out}")
    return 0


def _write_check(mp4: Path, info: dict, moment: dict, cam: dict | None, since: float) -> None:
    """Notes next to the Short for the review page, unless the renderer already wrote its own this run."""
    f = mp4.with_suffix(".json")
    if f.exists() and f.stat().st_mtime >= since:
        return
    paths.save(f, {"ok": bool(info.get("ok")), "notes": list(info.get("notes") or []),
                   "seconds": info.get("seconds"), "a": moment.get("a"), "b": moment.get("b"),
                   "hook": moment.get("hook"), "caption": moment.get("caption"), "cam": cam})


def cmd_make(a) -> int:
    from clipper import facecam, frames, gallery, hearing
    try:
        from clipper import render
    except ImportError as e:
        raise SystemExit(f"The renderer isn't installed yet (scripts/clipper/render.py): {e}")
    proj = _project(a.project)
    plan = _plan(proj)
    video = _source(plan, proj)
    moments = plan.get("moments") or []
    if not moments:
        raise SystemExit(f"No moments in {proj / 'plan.json'} yet. Add them like: "
                         '{"a": 401, "b": 435, "hook": "...", "caption": "..."}')
    words = paths.load(proj / "words.json", None)
    if not isinstance(words, list):
        words = hearing.transcribe(video, proj)
    dur = frames.video_info(video)["dur"]
    out_dir = paths.SHORTS / paths.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    log(f"GTA Clipper · making {len(moments)} Short{'s' if len(moments) != 1 else ''} from {proj.name}")
    made = 0
    for i, m in enumerate(moments, 1):
        try:
            start, end = paths.seconds(m["a"]), paths.seconds(m["b"])
        except (KeyError, ValueError, TypeError):
            log(f'\n✗ moment {i}: it needs "a" and "b" (seconds, or "m:ss")')
            continue
        if dur:
            end = min(end, dur)
        hook = str(m.get("hook") or "").strip()
        caption = str(m.get("caption") or "").strip()
        log(f"\n[{i}/{len(moments)}] {paths.clock(start)}-{paths.clock(end)} ({end - start:.0f} s) · {hook or '(no hook)'}")
        if end - start < 3:
            log("✗ shorter than 3 s: skipped")
            continue
        if not 10 <= end - start <= 60:
            log("! outside 10-60 s: most good Shorts are 15-45 s")
        if isinstance(m.get("cam"), dict):
            try:
                cam = {k: int(m["cam"][k]) for k in ("x", "y", "w", "h")}
                log("  facecam: the box given in the plan")
            except (KeyError, ValueError, TypeError):
                log('✗ "cam" needs x, y, w, h in the video\'s pixels: skipped')
                continue
        elif m.get("facecam") is False:
            cam = None
            log("  facecam: off for this moment")
        else:
            cam = facecam.detect(video, start, end)
            log(f"  facecam: found ({cam['w']}x{cam['h']} at {cam['x']},{cam['y']})" if cam
                else "  facecam: none found, gameplay goes full height")
        name = f"{i:02d}_{paths.slug(hook)}"
        t0 = time.time()
        try:
            info = render.build_short(video, start, end, hook, caption, words, cam, out_dir, name)
        except Exception as e:
            log(f"✗ moment {i} failed: {type(e).__name__}: {e}")
            continue
        if not isinstance(info, dict) or not info.get("mp4"):
            log(f"✗ moment {i}: the renderer gave no video back")
            continue
        _write_check(Path(info["mp4"]), info, m, cam, t0)
        made += 1
        secs = info.get("seconds") or 0
        log(f"{'✓' if info.get('ok') else '!'} {info['mp4']} ({float(secs):.1f} s long, made in {time.time() - t0:.0f} s)")
        for note in info.get("notes") or []:
            log(f"   - {note}")
    page = gallery.build()
    log(f"\n{made} of {len(moments)} Shorts made in {out_dir}")
    log(f"Review page: {page}")
    if not a.no_open:
        paths.open_path(page)
    return 0 if made else 1


def cmd_review(a) -> int:
    from clipper import gallery
    day = Path(a.day).name if a.day else paths.today()
    if not (paths.SHORTS / day).is_dir():
        days = sorted(d.name for d in paths.SHORTS.glob("*") if d.is_dir()) if paths.SHORTS.exists() else []
        raise SystemExit(f"No Shorts for {day}. Days with Shorts: {', '.join(days[-7:]) or 'none yet'}")
    page = gallery.build(day)
    log(f"Review page: {page}")
    paths.open_path(page)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gc", description="GTA Clipper: one link in, finished vertical Shorts out.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="install check + download the speech model (once)")
    sub.add_parser("doctor", help="check every piece, change nothing")
    g = sub.add_parser("get", help="download / open a video, listen to it, write the hot-spot notes")
    g.add_argument("link", help="a YouTube / Twitch / Kick / TikTok link, or a video file")
    g.add_argument("--minutes", type=float, default=40, help="long streams: how many minutes to fetch (40)")
    lk = sub.add_parser("look", help="contact sheet of a window of the video")
    lk.add_argument("video", help="the video file (or its project)")
    lk.add_argument("--from", dest="start", help="start, seconds or m:ss (0)")
    lk.add_argument("--to", dest="end", help="end, seconds or m:ss (start + 36 s)")
    lk.add_argument("--every", type=float, default=2.0, help="seconds between frames (2)")
    mk = sub.add_parser("make", help="render every moment of a project's plan.json")
    mk.add_argument("project", nargs="?", help="project folder or name (newest when left out)")
    mk.add_argument("--no-open", action="store_true", help="don't open the review page")
    rv = sub.add_parser("review", help="rebuild and open the review page")
    rv.add_argument("day", nargs="?", help="YYYY-MM-DD (today when left out)")
    a = ap.parse_args(argv)
    commands = {"setup": cmd_setup, "doctor": cmd_doctor, "get": cmd_get, "look": cmd_look, "make": cmd_make,
                "review": cmd_review}
    try:
        return commands[a.cmd](a) or 0
    except KeyboardInterrupt:
        log("\nstopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
