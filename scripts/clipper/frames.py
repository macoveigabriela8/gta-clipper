"""Reading a video without ffprobe (imageio-ffmpeg ships ffmpeg only): its size, length, and single frames."""
from __future__ import annotations

import io
import re
import subprocess
from pathlib import Path

from PIL import Image

from .paths import ffmpeg


def video_info(path: Path) -> dict:
    """{"w", "h", "dur", "fps", "has_audio"} read from ffmpeg's own description of the file."""
    p = subprocess.run([ffmpeg(), "-hide_banner", "-i", str(path)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    txt = p.stderr or ""
    info = {"w": 0, "h": 0, "dur": 0.0, "fps": 30.0, "has_audio": " Audio:" in txt}
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", txt)
    if m:
        info["dur"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    for line in txt.splitlines():
        if " Video:" in line:
            size = re.search(r"\s(\d{2,5})x(\d{2,5})[\s,\[]", line)
            if size:
                info["w"], info["h"] = int(size.group(1)), int(size.group(2))
            rate = re.search(r"([\d.]+)\s*fps", line)
            if rate:
                info["fps"] = float(rate.group(1))
            break
    return info


def grab_frames(path: Path, times: list[float], width: int = 480) -> list[Image.Image]:
    """One RGB picture per time (seconds), scaled to `width` pixels wide."""
    out = []
    for t in times:
        p = subprocess.run([ffmpeg(), "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
                            "-frames:v", "1", "-vf", f"scale={width}:-2", "-f", "image2pipe", "-vcodec", "png", "-"],
                           capture_output=True)
        if p.returncode == 0 and p.stdout:
            out.append(Image.open(io.BytesIO(p.stdout)).convert("RGB"))
        elif out:
            out.append(out[-1].copy())
    return out
