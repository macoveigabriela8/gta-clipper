"""The review page: one plain HTML file per day listing every finished Short with a player, its post text and its
check notes. No internet needed; it just sits next to the videos."""
from __future__ import annotations

import html
import json
from pathlib import Path
from urllib.parse import quote

from .paths import SHORTS, load, today

CSS = """
body{margin:0;background:#111214;color:#eee;font:15px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
header{padding:18px 20px;border-bottom:1px solid #2a2b30}h1{margin:0;font-size:20px}header p{margin:4px 0 0;color:#999}
main{display:flex;flex-wrap:wrap;gap:20px;padding:20px}
.card{background:#1b1c20;border:1px solid #2a2b30;border-radius:10px;padding:12px;width:300px;box-sizing:border-box}
.card h2{font-size:15px;margin:0 0 8px;word-break:break-word}
video{width:100%;aspect-ratio:9/16;background:#000;border-radius:6px;display:block}
textarea{width:100%;box-sizing:border-box;min-height:84px;margin-top:8px;background:#111214;color:#ddd;
border:1px solid #33343a;border-radius:6px;padding:6px;font-family:inherit;font-size:13px;resize:vertical}
button{margin-top:4px;background:#2d2e35;color:#eee;border:1px solid #44454d;border-radius:6px;padding:4px 10px;
cursor:pointer}button:hover{background:#3a3b44}
.ok{color:#7ddc8a}.warn{color:#ffb454}ul{margin:6px 0 0;padding-left:18px;color:#bbb;font-size:13px}
pre{white-space:pre-wrap;color:#999;font-size:12px}.meta{color:#888;font-size:12px;margin-top:6px}
"""

JS = """
function copyText(id,btn){const t=document.getElementById(id);t.select();
 const done=()=>{btn.textContent='Copied';setTimeout(()=>btn.textContent='Copy text',1200)};
 if(navigator.clipboard){navigator.clipboard.writeText(t.value).then(done,()=>{document.execCommand('copy');done()})}
 else{document.execCommand('copy');done()}}
"""


def _poster(mp4: Path) -> Path | None:
    for name in (f"{mp4.stem}.jpg", f"{mp4.stem}_cover.jpg", f"{mp4.stem}.cover.jpg", f"{mp4.stem}.png",
                 f"{mp4.stem}_cover.png"):
        p = mp4.with_name(name)
        if p.exists():
            return p
    return None


def _notes(data) -> str:
    if not isinstance(data, dict):
        return f"<pre>{html.escape(json.dumps(data, indent=1, ensure_ascii=False))}</pre>" if data else ""
    parts = []
    if "ok" in data:
        qa = data.get("qa") if isinstance(data.get("qa"), list) else []
        score = f" ({sum(1 for c in qa if c.get('pass'))}/{len(qa)} QA checks)" if qa else ""
        parts.append(f'<div class="ok">✓ QA passed{score}</div>' if data.get("ok")
                     else f'<div class="warn">✗ needs a look before posting{score}</div>')
    notes = data.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    if notes:
        parts.append("<ul>" + "".join(f"<li>{html.escape(str(n))}</li>" for n in notes) + "</ul>")
    meta = []
    for key in ("seconds", "a", "b"):
        if isinstance(data.get(key), (int, float)):
            meta.append(f"{key} {data[key]:.1f}" if isinstance(data[key], float) else f"{key} {data[key]}")
    if meta:
        parts.append(f'<div class="meta">{html.escape(" · ".join(meta))}</div>')
    return "".join(parts)


def build(day: str | None = None) -> Path:
    """SHORTS/<day>/index.html for every .mp4 in that folder (today when no day is given)."""
    day = day or today()
    folder = SHORTS / day
    folder.mkdir(parents=True, exist_ok=True)
    cards = []
    mp4s = sorted(folder.glob("*.mp4"))
    for i, mp4 in enumerate(mp4s):
        txt = mp4.with_suffix(".txt")
        post = txt.read_text(encoding="utf-8", errors="replace").strip() if txt.exists() else ""
        check = load(mp4.with_suffix(".json"), None)
        poster = _poster(mp4)
        poster_attr = f' poster="{quote(poster.name)}"' if poster else ""
        card = [f'<div class="card"><h2>{html.escape(mp4.stem)}</h2>',
                f'<video controls preload="metadata"{poster_attr} src="{quote(mp4.name)}"></video>']
        if post:
            card.append(f'<textarea id="t{i}" readonly>{html.escape(post)}</textarea>'
                        f'<button onclick="copyText(\'t{i}\',this)">Copy text</button>')
        card.append(_notes(check))
        card.append("</div>")
        cards.append("".join(card))
    body = "".join(cards) or "<p>No Shorts in this folder yet.</p>"
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f"<title>GTA Clipper · {html.escape(day)}</title><style>{CSS}</style></head><body>"
            f"<header><h1>GTA Clipper · {html.escape(day)}</h1>"
            f"<p>{len(mp4s)} Short{'s' if len(mp4s) != 1 else ''}. Watch each one with the sound on before posting."
            f"</p></header><main>{body}</main><script>{JS}</script></body></html>")
    out = folder / "index.html"
    out.write_text(page, encoding="utf-8")
    return out
