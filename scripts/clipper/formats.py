"""One picture of the 3 kinds of Short this skill makes, side by side, so the user SEES them before choosing anything.
Drawn here (no footage, nothing to license) with the real pill and caption code, so the text looks exactly like a
finished Short. `gc formats` writes it and opens it in the computer's own picture viewer (Mac, Windows, Linux)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

from . import overlays
from .paths import H, W

CAM_H = 740                       # same split as render.py
PHONE_W = 360
PHONE_H = PHONE_W * H // W
GAP = 48
LABEL_H = 150
BG = (16, 16, 20)


def _gameplay(w: int, h: int) -> Image.Image:
    """A stand-in game scene: dusk sky, a skyline, a road with a car."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    horizon = int(h * 0.5)
    for y in range(horizon):                                  # sky: deep blue to orange
        k = y / max(1, horizon - 1)
        d.line([(0, y), (w, y)], fill=(int(28 + 190 * k), int(36 + 80 * k), int(84 - 30 * k)))
    x = 0
    for bw, bh in [(110, 260), (80, 380), (140, 220), (90, 470), (120, 300), (100, 410), (150, 250), (90, 330)]:
        d.rectangle([x, horizon - bh, x + bw, horizon], fill=(24, 24, 34))
        for wy in range(horizon - bh + 24, horizon - 20, 44):  # lit windows
            for wx in range(x + 14, x + bw - 20, 30):
                if (wx * 7 + wy) % 5:
                    d.rectangle([wx, wy, wx + 12, wy + 18], fill=(250, 196, 90))
        x += bw + 12
    d.rectangle([0, horizon, w, h], fill=(38, 40, 46))         # ground
    d.polygon([(w * 0.42, horizon), (w * 0.58, horizon), (w * 0.95, h), (w * 0.05, h)], fill=(58, 60, 66))
    for i in range(6):                                         # centre dashes, bigger as they come closer
        t0, t1 = i / 6, (i + 0.5) / 6
        y0, y1 = horizon + (h - horizon) * t0 ** 1.6, horizon + (h - horizon) * t1 ** 1.6
        hw0, hw1 = 3 + 10 * t0, 3 + 10 * t1
        d.polygon([(w / 2 - hw0, y0), (w / 2 + hw0, y0), (w / 2 + hw1, y1), (w / 2 - hw1, y1)], fill=(240, 200, 60))
    cx, cy = int(w * 0.5), int(horizon + (h - horizon) * 0.62)  # the car, from behind
    d.rounded_rectangle([cx - 190, cy - 70, cx + 190, cy + 90], 40, fill=(178, 30, 40))
    d.rounded_rectangle([cx - 130, cy - 150, cx + 130, cy - 40], 36, fill=(150, 24, 34))
    d.rounded_rectangle([cx - 105, cy - 130, cx + 105, cy - 60], 22, fill=(60, 80, 110))
    for sx in (-150, 110):
        d.rounded_rectangle([cx + sx, cy - 20, cx + sx + 40, cy + 10], 8, fill=(255, 70, 60))
    return img


def _webcam(w: int, h: int) -> Image.Image:
    """A stand-in webcam: a streamer silhouette with headphones and a mic in a dim room."""
    img = Image.new("RGB", (w, h), (52, 50, 60))
    d = ImageDraw.Draw(img)
    d.rectangle([int(w * 0.68), int(h * 0.12), int(w * 0.92), int(h * 0.55)], fill=(70, 66, 80))   # a poster
    d.rectangle([0, int(h * 0.8), w, h], fill=(40, 38, 46))
    cx = w // 2
    d.ellipse([cx - 300, int(h * 0.76), cx + 300, int(h * 1.5)], fill=(222, 222, 228))           # shoulders
    d.ellipse([cx - 110, int(h * 0.34), cx + 110, int(h * 0.76)], fill=(150, 108, 84))             # head
    d.arc([cx - 145, int(h * 0.29), cx + 145, int(h * 0.7)], 180, 360, fill=(20, 20, 24), width=26)  # headphones
    for sx in (-160, 130):
        d.rounded_rectangle([cx + sx, int(h * 0.49), cx + sx + 32, int(h * 0.63)], 12, fill=(20, 20, 24))
    d.line([(cx + 330, h), (cx + 210, int(h * 0.72))], fill=(30, 30, 34), width=22)                # mic arm
    d.rounded_rectangle([cx + 150, int(h * 0.6), cx + 250, int(h * 0.8)], 30, fill=(30, 30, 34))
    return img


def _overlay(base: Image.Image, png: Path, xy: tuple[int, int]) -> None:
    top = Image.open(png).convert("RGBA")
    base.paste(top, xy, top)


def _phone(kind: str, work: Path) -> Image.Image:
    frame = Image.new("RGB", (W, H))
    if kind == "full":
        frame.paste(_gameplay(W, H), (0, 0))
        cap_mid = round(H * 0.62)
    else:
        frame.paste(_webcam(W, CAM_H), (0, 0))
        frame.paste(_gameplay(W, H - CAM_H), (0, CAM_H))
        cap_mid = CAM_H
    pill = {"cam": "He thought the van could swim", "full": "Wait for the cop's reaction",
            "top5": "#3 LAUGHING CRASH"}[kind]
    words = {"cam": (["NO", "WAY", "BRO"], 1), "full": (["LOOK", "AT", "THIS"], 2),
             "top5": (["OH", "NO"], 1)}[kind]
    png, x, y = overlays.hook_png(pill, work / f"pill_{kind}.png")
    _overlay(frame, png, (x, y))
    _overlay(frame, overlays.caption_png(words[0], words[1], work / f"cap_{kind}.png"),
             (0, cap_mid - overlays.CAP_BAND_H // 2))
    small = frame.resize((PHONE_W, PHONE_H), Image.LANCZOS)
    mask = Image.new("L", small.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, PHONE_W - 1, PHONE_H - 1], 28, fill=255)
    out = Image.new("RGBA", small.size, (0, 0, 0, 0))
    out.paste(small, (0, 0), mask)
    return out


def picture(out: Path) -> Path:
    """The 3 kinds side by side with a name and one line each. Returns the picture's path."""
    kinds = [("cam", "1  Streamer moments", "face on top, game below"),
             ("full", "2  Full gameplay", "no webcam: the game fills it"),
             ("top5", "3  Top 5 countdown", "#5 to #1 in one Short")]
    width = GAP + len(kinds) * (PHONE_W + GAP)
    sheet = Image.new("RGB", (width, GAP + PHONE_H + LABEL_H), BG)
    d = ImageDraw.Draw(sheet)
    name_f, line_f = overlays.hook_font(30), overlays.hook_font(21)
    with tempfile.TemporaryDirectory(prefix="gtaclip-formats-") as tmp:
        for i, (kind, name, line) in enumerate(kinds):
            x = GAP + i * (PHONE_W + GAP)
            phone = _phone(kind, Path(tmp))
            glow = Image.new("RGBA", (PHONE_W + 40, PHONE_H + 40), (0, 0, 0, 0))
            ImageDraw.Draw(glow).rounded_rectangle([20, 20, PHONE_W + 19, PHONE_H + 19], 30, fill=(0, 0, 0, 200))
            glow = glow.filter(ImageFilter.GaussianBlur(12))
            sheet.paste(glow, (x - 20, GAP - 14), glow)
            sheet.paste(phone, (x, GAP), phone)
            d.text((x + PHONE_W / 2, GAP + PHONE_H + 34), name, font=name_f, fill=(255, 214, 10), anchor="ma")
            d.text((x + PHONE_W / 2, GAP + PHONE_H + 82), line, font=line_f, fill=(235, 235, 235), anchor="ma")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, "PNG")
    return out
