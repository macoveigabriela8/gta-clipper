"""gc setup / gc doctor: everything comes from pip through uv (no Homebrew, no password, Mac or Windows).
Setup downloads the speech model once and checks each piece; doctor only checks."""
from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .paths import FONTS, MODELS, PROJECTS, SHORTS, WORK, ffmpeg, log, run, setting


def _free_gb() -> float:
    p = Path(WORK)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


def _imports(module: str) -> str:
    """'' when the module imports fine, else the last error line. Each check runs in its own Python, so OpenCV
    and PyAV (both carry an ffmpeg) never load into the same process just for a check."""
    # On Windows the speech engine needs Microsoft's C++ runtime from the msvc-runtime package: point the check at it
    # the way hearing._windows_runtime() does for real listening, or setup says "not ready" on a PC that works.
    dlls = ("import os\nif os.name == 'nt':\n    for d in {sys.prefix, os.path.join(sys.prefix, 'Scripts'), "
            "sys.base_prefix}:\n        if os.path.isdir(d):\n            os.add_dll_directory(d)\n"
            "            os.environ['PATH'] = d + os.pathsep + os.environ.get('PATH', '')\n")
    code = (f"import sys\n{dlls}sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
            f"import {module}")
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    except Exception as e:
        return str(e)
    if p.returncode == 0:
        return ""
    lines = [ln for ln in (p.stderr or "").strip().splitlines() if ln.strip() and not ln.startswith("objc[")]
    return lines[-1] if lines else f"exit code {p.returncode}"


def have_model() -> bool:
    d = MODELS / "whisper"
    return d.exists() and any(d.rglob("model.bin"))


def setup(doctor_only: bool = False) -> bool:
    ok = True
    log("GTA Clipper · " + ("checking" if doctor_only else "setting up") + " this computer\n")
    log(f"✓ {platform.system()} {platform.machine()} · Python {sys.version.split()[0]}")
    free = _free_gb()
    log(f"{'✓' if free > 3 else '!'} {free:.0f} GB free disk")
    try:
        v = run([ffmpeg(), "-hide_banner", "-version"]).stdout.splitlines()[0]
        log(f"✓ ffmpeg ({v.split(' Copyright')[0]})")
    except Exception as e:
        log(f"✗ ffmpeg: {e}")
        ok = False
    for mod, what in (("numpy", "NumPy"), ("cv2", "OpenCV (finds the facecam)"),
                      ("faster_whisper", "faster-whisper (the ears)"), ("yt_dlp", "yt-dlp (downloads links)"),
                      ("PIL", "Pillow (draws captions)")):
        err = _imports(mod)
        log(f"✓ {what}" if not err else f"✗ {what}: {err}")
        ok &= not err
    try:
        import deno
        log(f"✓ deno (lets yt-dlp read YouTube): {deno.find_deno_bin()}")
    except Exception:
        log("! deno missing: some YouTube links may fail")
    fonts = sorted(FONTS.glob("*.ttf"))
    log(f"{'✓' if fonts else '✗'} fonts: {', '.join(f.stem for f in fonts) or 'missing'}")
    ok &= bool(fonts)
    err = _imports("clipper.render")
    log("✓ renderer (makes the Shorts)" if not err else f"✗ renderer (makes the Shorts): {err}")
    ok &= not err
    name = setting("whisper_model")
    if not doctor_only and not have_model():
        log(f"  getting the speech model ({name}, once)…")
    if not doctor_only:
        try:
            from . import hearing
            hearing.load_model(name)
        except Exception as e:
            log(f"✗ speech model: {type(e).__name__}: {e}")
            ok = False
    log(f"{'✓' if have_model() else '✗'} speech model {name}" + ("" if have_model() or not doctor_only
                                                                 else " (run setup to get it)"))
    ok &= have_model()
    if not doctor_only:
        for d in (PROJECTS, SHORTS):
            d.mkdir(parents=True, exist_ok=True)
    log(f"\n{'✓ Ready.' if ok else '✗ Not ready yet: fix the ✗ lines above.'}")
    if ok:
        log(f"  Shorts land in {SHORTS}")
    return ok
