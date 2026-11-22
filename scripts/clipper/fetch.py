"""Bring the video in and give it a project folder. A link is anything yt-dlp opens (YouTube video / VOD / Short,
Twitch VOD or clip, Kick, TikTok...). No account, no key. A path to a video file on the computer works too."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

from .frames import video_info
from .paths import INPUTS, PROJECTS, ffmpeg, load, log, save, setting, slug, today


def _ydl_opts(out_dir: Path) -> dict:
    opts = {
        "outtmpl": str(out_dir / "%(extractor_key)s_%(id)s.%(ext)s"),
        # H.264 up to 1080p first (every editor and every ffmpeg reads it), then anything up to 1080p
        "format": "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg(),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 5,
    }
    try:                                    # YouTube now needs a JavaScript runtime: deno comes with the skill
        import deno
        opts["js_runtimes"] = {"deno": {"path": deno.find_deno_bin()}}
    except Exception:
        pass
    return opts


# YouTube sometimes answers 403 / "confirm you're not a bot" to one player and not to another: try them in turn.
_YT_CLIENTS = [None, ["tv", "web_safari"], ["mweb"], ["web_embedded"], ["android_vr"]]


def download(url: str, minutes: float = 40) -> tuple[Path, str]:
    import yt_dlp
    INPUTS.mkdir(parents=True, exist_ok=True)
    is_yt = "youtu" in url.lower()
    last_err = None
    for clients in (_YT_CLIENTS if is_yt else [None]):
        opts = _ydl_opts(INPUTS)
        if clients:
            opts["extractor_args"] = {"youtube": {"player_client": clients}}
        try:
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=False)
            if info.get("_type") == "playlist" and info.get("entries"):
                info = next(e for e in info["entries"] if e)
                url = info.get("webpage_url") or info.get("url") or url
            title = info.get("title") or info.get("id") or "stream"
            dur = float(info.get("duration") or 0)
            if dur > minutes * 60 * 1.1:
                log(f"  it's {dur / 60:.0f} min long: fetching the first {minutes:.0f} min (change with --minutes)")
                opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(0, minutes * 60)])
            log(f"  downloading: {title}" + (f" (as the {clients[0]} player)" if clients else ""))
            with yt_dlp.YoutubeDL(opts) as y:
                info = y.extract_info(url, download=True)
                path = Path(y.prepare_filename(info)).with_suffix(".mp4")
            break
        except yt_dlp.utils.DownloadError as e:
            last_err = e
            log(f"  that didn't work ({str(e).splitlines()[0][:90]}), trying another way…")
            for part in INPUTS.glob("*.part"):
                part.unlink(missing_ok=True)
    else:
        if is_yt:                              # rate limits usually clear in seconds: one calm retry, then paid
            log("  YouTube is limiting downloads right now: waiting 20 s and trying once more…")
            time.sleep(20)
            try:
                opts = _ydl_opts(INPUTS)
                opts["extractor_args"] = {"youtube": {"player_client": ["tv", "mweb"]}}
                with yt_dlp.YoutubeDL(opts) as y:
                    info = y.extract_info(url, download=True)
                    path = Path(y.prepare_filename(info)).with_suffix(".mp4")
                return path, info.get("title") or info.get("id") or "stream"
            except yt_dlp.utils.DownloadError as e:
                last_err = e
            token = os.environ.get("APIFY_TOKEN") or (setting("apify_token") or "")
            if token:
                return _paid_youtube(url, token), "stream"
        raise SystemExit(f"Couldn't download that link: {str(last_err).splitlines()[0] if last_err else 'unknown'}.\n"
                         "Try a Twitch or Kick link, try again in a few minutes, or give me the video file.\n"
                         "Paid backup for YouTube (optional): get a token at apify.com and say "
                         "'set my Apify token to …', then run this again.")
    if not path.exists():                   # the merger may have kept another container
        cands = sorted(INPUTS.glob(f"*_{info.get('id')}.*"), key=lambda p: p.stat().st_mtime)
        cands = [c for c in cands if c.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
        if not cands:
            raise SystemExit("The download finished but no video file was written. Try another link.")
        path = cands[-1]
    return path, title


_APIFY = ("https://api.apify.com/v2/acts/streamers~youtube-video-downloader/"
          "run-sync-get-dataset-items?maxTotalChargeUsd=1")


def _paid_youtube(url: str, token: str) -> Path:
    """Last resort for YouTube when every free way is blocked: Apify downloads it (costs cents, capped at $1)."""
    log("  using the paid backup (Apify)…")
    body = json.dumps({"videos": [{"url": url}], "startUrls": [{"url": url}], "preferredQuality": "720p",
                       "preferredFormat": "mp4", "storeInKVStore": True}).encode()
    req = urllib.request.Request(_APIFY, body, {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=900) as r:
        items = json.loads(r.read().decode("utf-8") or "[]")
    link = (items or [{}])[0].get("downloadedFileUrl")
    if not link:
        raise SystemExit("The paid backup returned no file. Try another link or give me the video file.")
    dest = INPUTS / f"Youtube_apify_{slug(url[-11:], 20)}.mp4"
    urllib.request.urlretrieve(link, dest)
    return dest


def bring_in(inp: str, minutes: float = 40) -> tuple[Path, str]:
    """(video file, title) from a link or a local path."""
    inp = (inp or "").strip().strip('"').strip("'")
    local = Path(os.path.expanduser(inp))
    if inp and local.is_file():
        log(f"  using the file: {local.resolve()}")
        return local.resolve(), local.stem
    if inp.lower().startswith(("http://", "https://", "www.")):
        return download(inp if inp.lower().startswith("http") else "https://" + inp, minutes)
    raise SystemExit("Give me a link (YouTube, Twitch, Kick, TikTok) or the path to a video file.")


def get(inp: str, minutes: float = 40) -> Path:
    """Video in, project folder out: PROJECTS/<date>_<slug>/plan.json = {"source", "title", "moments": []}.
    Running it again on the same video the same day reuses that project (and keeps its moments)."""
    src, title = bring_in(inp, minutes)
    info = video_info(src)
    if not info["w"] or info["dur"] < 5:
        raise SystemExit(f"{src} doesn't look like a playable video.")
    base = PROJECTS / f"{today()}_{slug(title, 36)}"
    proj, k = base, 2
    while (proj / "plan.json").exists():
        old = load(proj / "plan.json", {}) or {}
        if Path(str(old.get("source", ""))) == src:
            log(f"✓ project (again): {proj}")
            return proj
        proj = base.with_name(f"{base.name}-{k}")
        k += 1
    proj.mkdir(parents=True, exist_ok=True)
    save(proj / "plan.json", {"source": str(src), "title": title, "moments": []})
    log(f"✓ project: {proj}  ({info['w']}x{info['h']}, {info['dur'] / 60:.1f} min)")
    return proj
