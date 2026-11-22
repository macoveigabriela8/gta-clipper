---
name: gta-clipper
description: Turns one stream or gameplay link (YouTube, Twitch, Kick, TikTok) or a video file into finished vertical Shorts of its best moments, with the streamer's facecam on top, gameplay below, word-by-word captions and a black hook pill first. Runs on the user's own Mac or Windows PC, free, no account or key. Use when someone wants to clip a stream, make GTA (or any game) Shorts from a link, says "clip this", or says "set up GTA Clipper".
---

# GTA Clipper

One link in, 2-3 finished Shorts out. The heavy work (download, listening, facecam, edit) runs on the user's computer
through one script. Your job is the taste: which moments, where each starts and ends, and the hook. Talk in plain
words; the user is often not technical. Show only the lines that matter from each command.

**The command** (works the same on Mac and Windows, from any folder):
`uv run --python 3.12 "<this skill's folder>/scripts/gc.py" <command>` — below written as `gc <command>`.
This skill's folder is the one holding this SKILL.md (normally `~/.claude/skills/gta-clipper`, on Windows
`%USERPROFILE%\.claude\skills\gta-clipper`).

## First run (do it all, one go)

1. **uv** (it brings Python and every library; no admin, no password). Check `uv --version`. If missing:
   - Mac: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   Then use the full path until a new terminal opens: Mac `~/.local/bin/uv`, Windows `$env:USERPROFILE\.local\bin\uv.exe`.
   If `uv run --python 3.12` fails on Windows with a link/shortcut error, run `uv python find 3.12` and pass that
   full python.exe path to `--python` instead (some Windows Servers break uv's Python shortcut).
2. `gc setup` — installs the libraries and downloads the speech model once (about 1-2 minutes). Wait for "Ready".
3. `gc get "<link or file>"` — downloads the video (long streams: the first 40 min; `--minutes 90` for more), listens
   to it and writes `scan/chunk_*.md` in a new project under `~/GTAClipper/projects/`.
4. Read `reference/cutting-sop.md` (the cutting rules and the QA), then every chunk file. Lines marked 🔥🔥 / 🔥
   are the loudest, wildest seconds. Find every moment worth a Short (below) and check each window with
   `gc look "<video>" --from 6:40 --to 7:16 --every 3` (read the contact sheet): you must SEE the setup and the
   payoff inside it.
5. **Always ask, even for one moment** (unless they already said how many, or "you choose"): list what you found,
   best first, one line each (time, length, the hook), then "I found N clippable moments. Make just the best one,
   or all N? Each takes about 10 seconds." With one: "I found 1 clippable moment: … Make it, or pick a different
   part?" Then write the chosen ones into the project's `plan.json` under `"moments"`:
   `{"a": 401, "b": 435, "hook": "He just paid 25K for this truck", "caption": "Never complain out loud #gtarp",
   "payoff": "the truck flips into the river", "payoff_at": 432.5}`
   (`a` / `b` / `payoff_at` in seconds of the video; end 1-3 s after the payoff). "You choose" = the best 3.
6. `gc make "<project folder>" --no-open` — renders them one by one and runs the QA on each (below).
7. **QA, never skipped.** Fix every ✗ the make printed (the fix for each is in `reference/cutting-sop.md` § 6),
   then Read every `<name>_qa.jpg` it lists and do the eye check (§ 5): real webcam on top, no second set of
   captions, the last 4 frames land on the payoff. Make again until clean (two rounds at most; then drop that
   moment and say why). Only then open the review page (`~/GTAClipper/shorts/<date>/index.html`) and tell the
   user where the Shorts are, one line each: "✓ QA passed" or what you fixed.

**Keep the user posted, in the chat itself** (the app folds command output away, so they won't see it unless you say
it). Before every step, one line with what happens and how long:
- setup: "Installing the tools, about 1-2 minutes (only the first time)."
- get: "Downloading and listening to the stream: about 1-3 min per 10 min of video (this one is N min)."
- make: "Making N Shorts and checking each one, about 15-30 seconds each."
Add once: "You can click the running step to watch it live." After each step, say what came out in one line
("Heard 755 words, 15 hot moments", "Short 1 of 3 done").

No link given? Ask for one: a YouTube video or VOD, a Twitch VOD or clip, or a video file on the computer.

## Picking a moment

- Keep it if someone with the sound off gets it in two seconds, and it has a setup and a payoff (the plan, then what
  really happened). End right after the payoff (the reaction, the explosion, the "no way").
- Window: start 3-6 s before the setup, end 1-3 s after the payoff. 15-45 s.
- **The payoff must be inside the link.** A short clip (a 30 s Twitch clip) often stops mid-scene where the viewer
  who cut it stopped. If the payoff isn't there, say so and ask for a longer link (the full stream or VOD); never
  hand over a Short whose ending doesn't land.
- Prefer moments where the streamer talks or reacts out loud: when only the game is heard, the captions stay empty
  and the Short loses the streamer (QA warns under 0.5 words a second).
- Never put a 🔇 silent stretch (listed in the notes) inside a window: start after it or end before it. Silence at
  the very start or end of a Short is trimmed for you; silence of 2 s or more in the middle makes the Short come
  back "needs a look" with the second it happens. Then move the window or split it into two moments and make it
  again. Don't hand over a Short with a silent middle.
- Drop it if it needs five minutes of context, has music or radio playing (claims), or is a menu or loading screen.
- A compilation (several streamers): each moment finds its own facecam; stay inside one clip.

## The hook (the black pill, on screen from the first frame, 3 s)

5-9 words. Say what's about to happen, never the payoff. Formulas that keep working:
- **He thought X…** — "He thought the van could swim"
- **Stakes** — "He just paid 25K for this truck"
- **Wait for it** — "Wait for the cop's reaction"
- **POV** — "POV: you trust your GTA RP friend"
- **Number** — "He tried this 47 times"
- **Question** — "Can a car actually float in GTA?"
Write it like you'd text a friend. No emoji, no hashtags in the hook.

## If something goes wrong

- `gc doctor` checks every piece. Setup problems: run `gc setup` again (it only fetches what's missing).
- No facecam found → the Short uses the gameplay full height (that's fine). Wrong face picked → set
  `"facecam": false` on that moment, or give `"cam": {"x":..,"y":..,"w":..,"h":..}` in the video's pixels.
- Game subtitles or stream captions still showing under ours → `"drop_bottom": 0.25` on that moment (the share of
  the picture's height cut off the bottom; the tool finds most of them by itself).
- A link fails → `gc get` already tries five YouTube players, waits and tries again. Still failing: YouTube is
  rate-limiting this computer. Offer, in this order: a Twitch / Kick link of the same stream, trying again in a few
  minutes, or the video file. Optional paid backup for YouTube: if the user has an Apify token, save it as
  `"apify_token"` in `~/GTAClipper/settings.json` (never print it back) and run `gc get` again; it costs cents,
  capped at $1 a video.

## Honest notes for the user

Only clip streamers who are fine with it (many invite clippers), or add your own reaction or voice: platforms pay
less, or nothing, for re-uploads with nothing new in them. Footage you recorded yourself is always yours to post.
Views are never guaranteed.
