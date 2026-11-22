"""Contact sheets: a grid of frames from a stretch of video, each with its time above it, so Claude can see a
moment (where the action starts, when it ends) without watching the video."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .frames import grab_frames
from .paths import CAPTION_FONT, HOOK_FONT, log

MAX_FRAMES = 120
BG, INK = (18, 18, 20), (245, 245, 245)


def stamp(t: float) -> str:
    """m:ss.ss (h:mm:ss.ss past an hour)."""
    t = max(0.0, float(t))
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}" if h >= 1 else f"{int(m)}:{s:05.2f}"


def _font(size: int):
    for f in (HOOK_FONT, CAPTION_FONT):
        try:
            return ImageFont.truetype(str(f), size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def contact_sheet(video: Path, out_jpg: Path, start: float, end: float, every: float = 2.0, cols: int = 6,
                  width: int = 320) -> Path:
    video, out_jpg = Path(video), Path(out_jpg)
    every = max(0.04, float(every))
    if end <= start:
        end = start + every
    span = end - start
    if span / every + 1 > MAX_FRAMES:
        every = span / (MAX_FRAMES - 1)
        log(f"  (too many frames: one every {every:.2f} s instead)")
    times, t = [], float(start)
    while t <= end + 1e-6:
        times.append(round(t, 3))
        t += every
    title = f"{video.name}  ·  {stamp(start)} - {stamp(end)}  ·  one frame every {every:g} s"
    return sheet_at(video, out_jpg, times, title, stamp, cols, width)


def sheet_at(video: Path, out_jpg: Path, times: list[float], title: str, label=stamp, cols: int = 6,
             width: int = 320) -> Path:
    """A grid of the frames at exactly these times, each with label(time) above it."""
    video, out_jpg = Path(video), Path(out_jpg)
    frames = grab_frames(video, times, width)
    if not frames:
        raise SystemExit(f"Could not read any frame from {video}.")
    times = times[:len(frames)]
    fw, fh = frames[0].size
    cols = max(1, min(cols, len(frames)))
    rows = (len(frames) + cols - 1) // cols
    pad, label_h, title_h = 6, 26, 40
    sheet = Image.new("RGB", (cols * (fw + pad) + pad, title_h + rows * (label_h + fh + pad) + pad), BG)
    d = ImageDraw.Draw(sheet)
    d.text((pad + 4, 10), title, fill=INK, font=_font(20))
    small = _font(17)
    for i, (img, t) in enumerate(zip(frames, times)):
        r, c = divmod(i, cols)
        x = pad + c * (fw + pad)
        y = title_h + r * (label_h + fh + pad)
        d.text((x + 2, y + 3), label(t), fill=INK, font=small)
        sheet.paste(img.resize((fw, fh)) if img.size != (fw, fh) else img, (x, y + label_h))
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_jpg, "JPEG", quality=85)
    return out_jpg
