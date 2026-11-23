"""Where GTA Clipper keeps things, plus the few helpers every part shares (Mac and Windows alike)."""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

WORK = Path(os.environ.get("GTACLIP_HOME", Path.home() / "GTAClipper"))
MODELS = WORK / ".models"
PROJECTS = WORK / "projects"
SHORTS = WORK / "shorts"
INPUTS = WORK / "inputs"
SKILL_DIR = Path(__file__).resolve().parents[2]          # .../gta-clipper
FONTS = SKILL_DIR / "assets" / "fonts"
CAPTION_FONT = FONTS / "Anton-Regular.ttf"
HOOK_FONT = FONTS / "Montserrat-Black.ttf"

W, H, FPS = 1080, 1920, 30

_DEFAULTS = {"whisper_model": "base.en", "hook_seconds": 3.0, "max_short_s": 59,
             "hashtags": "#gta #gtarp #gaming #shorts", "drop_bottom": "auto", "drop_top": "auto"}
_SETTINGS_FILE = WORK / "settings.json"


def settings() -> dict:
    s = dict(_DEFAULTS)
    s.update(load(_SETTINGS_FILE, {}) or {})
    return s


def setting(key: str):
    return settings().get(key)


def log(msg: str) -> None:
    sys.stdout.write(f"{msg}\n")
    sys.stdout.flush()


def load(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")


_FF = None


def ffmpeg() -> str:
    """ffmpeg from the imageio-ffmpeg pip package (same build on Mac and Windows); GTACLIP_FFMPEG overrides."""
    global _FF
    if _FF is None:
        exe = os.environ.get("GTACLIP_FFMPEG")
        if not exe:
            try:
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                exe = shutil.which("ffmpeg")
        if not exe:
            raise SystemExit("No ffmpeg found. Run the setup command first.")
        _FF = exe
    return _FF


def run(cmd: list, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a command, text output captured. Raises RuntimeError with the end of stderr when it fails."""
    p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if p.returncode != 0:
        tail = "\n".join((p.stderr or "").strip().splitlines()[-8:])
        raise RuntimeError(f"{Path(str(cmd[0])).name} failed ({p.returncode}):\n{tail}")
    return p


def slug(text: str, n: int = 40) -> str:
    """'He paid 25K!' -> 'he-paid-25k' (file-name safe on Mac and Windows)."""
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    out = ""
    for w in words:
        if len(out) + len(w) + (1 if out else 0) > n:
            break
        out = f"{out}-{w}" if out else w
    return out or "clip"


def clock(sec: float) -> str:
    sec = max(0, int(round(sec)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def seconds(v) -> float:
    """75, 75.5, "75", "1:15", "0:01:15" -> seconds."""
    if isinstance(v, (int, float)):
        return float(v)
    total = 0.0
    for part in str(v).strip().split(":"):
        total = total * 60 + float(part)
    return total


def today() -> str:
    return _dt.date.today().isoformat()


def open_path(p) -> None:
    p = str(p)
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass
