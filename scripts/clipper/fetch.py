"""Bring the video in and give it a project folder. A link is anything yt-dlp opens (YouTube video / VOD / Short,
Twitch VOD or clip, Kick, TikTok...). No account, no key. A path to a video file on the computer works too."""
from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from .frames import video_info
from .paths import INPUTS, PROJECTS, WORK, ffmpeg, load, log, save, setting, slug, today


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
# Other sites: yt-dlp's own stream stitcher first; some Twitch VODs break it ("Initialization fragment found after
# media fragments"), and ffmpeg reads those fine.
_OTHER_TRIES = [None, "ffmpeg"]


def _ffmpeg_on_path() -> None:
    """Before yt-dlp lets ffmpeg do a download (a Twitch stream its own stitcher can't read, or cutting the first
    40 min of a long stream) it checks for a program called "ffmpeg" on PATH; it ignores the ffmpeg we hand it.
    A fresh Windows PC has none (a Mac with Homebrew does), so yt-dlp quietly falls back and fails. Put ours there."""
    if shutil.which("ffmpeg"):
        return
    d = WORK / ".bin"
    d.mkdir(parents=True, exist_ok=True)
    exe = d / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if not exe.exists():
        try:
            os.link(ffmpeg(), exe)
        except OSError:
            shutil.copy2(ffmpeg(), exe)
    os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")


def _drop_parts() -> None:
    """Remove half-downloaded files before the next try. Windows may still hold one open for a moment: let go of it,
    and if it is still locked, leave it (the next try writes its own)."""
    import gc
    gc.collect()
    for part in [*INPUTS.glob("*.part"), *INPUTS.glob("*.ytdl")]:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass


def download(url: str, minutes: float = 40) -> tuple[Path, str]:
    import yt_dlp
    INPUTS.mkdir(parents=True, exist_ok=True)
    _ffmpeg_on_path()
    is_yt = "youtu" in url.lower()
    last_err = None
    for clients in (_YT_CLIENTS if is_yt else _OTHER_TRIES):
        opts = _ydl_opts(INPUTS)
        if clients == "ffmpeg":
            opts["external_downloader"] = {"m3u8": "ffmpeg"}
            opts["outtmpl"] = str(INPUTS / "%(extractor_key)s_%(id)s_ff.%(ext)s")
            clients = None
        elif clients:
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
            last_err = str(e)               # text only: the error object keeps the .part file open on Windows
            log(f"  that didn't work ({str(e).splitlines()[0][:90]}), trying another way…")
            _drop_parts()
    else:
        if is_yt and "confirm your age" in str(last_err).lower():
            raise SystemExit("That YouTube video is age-restricted: YouTube only plays it to a signed-in grown-up "
                             "account, so nothing can download it. Pick another video, or give me the video file.")
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
                last_err = str(e)
            token = os.environ.get("APIFY_TOKEN") or (setting("apify_token") or "")
            if token:
                return _paid_youtube(url, token), "stream"
        raise SystemExit(f"Couldn't download that link: {str(last_err).splitlines()[0] if last_err else 'unknown'}.\n"
                         "Try a Twitch or Kick link, try again in a few minutes, or give me the video file.\n"
                         "Paid backup for YouTube (optional): get a token at apify.com and say "
                         "'set my Apify token to …', then run this again.")
    if not path.exists():                   # the merger may have kept another container
        cands = sorted(INPUTS.glob(f"*_{info.get('id')}*.*"), key=lambda p: p.stat().st_mtime)
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
    try:
        with urllib.request.urlopen(req, timeout=900) as r:
            items = json.loads(r.read().decode("utf-8") or "[]")
    except urllib.error.HTTPError as e:
        why = "the Apify token was refused (wrong or expired: make a new one at apify.com)" if e.code in (401, 403) \
            else f"Apify answered {e.code}"
        raise SystemExit(f"The paid backup didn't work: {why}. Try a Twitch or Kick link, or give me the video file.")
    except (urllib.error.URLError, TimeoutError) as e:
        raise SystemExit(f"The paid backup couldn't be reached ({e}). Try again later, or give me the video file.")
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
