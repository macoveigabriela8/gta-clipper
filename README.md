# GTA Clipper

One stream link in, finished vertical Shorts out: the streamer's face on top, the game below, word captions, and a
black hook pill as the first thing on screen. Free, runs on your own Mac or Windows PC.

## Install: paste this one sentence into the Claude app (Code tab)

> Install the GTA Clipper skill from https://github.com/macoveigabriela8/gta-clipper and set it up, then clip this: PASTE-YOUR-STREAM-LINK

Works with YouTube, Twitch (VODs and clips), Kick, TikTok, or a video file on your computer.

## If you are Claude, installing this for the user

1. Put this repository in the user's skills folder as `gta-clipper` (no git needed):
   - Mac: `mkdir -p ~/.claude/skills && cd ~/.claude/skills && curl -L -o gc.zip https://github.com/macoveigabriela8/gta-clipper/archive/refs/heads/main.zip && unzip -q -o gc.zip && rm -rf gta-clipper && mv gta-clipper-main gta-clipper && rm gc.zip`
   - Windows (PowerShell): `$s="$env:USERPROFILE\.claude\skills"; New-Item -ItemType Directory -Force $s | Out-Null; Invoke-WebRequest https://github.com/macoveigabriela8/gta-clipper/archive/refs/heads/main.zip -OutFile "$s\gc.zip"; Expand-Archive "$s\gc.zip" $s -Force; Remove-Item "$s\gta-clipper" -Recurse -Force -ErrorAction SilentlyContinue; Rename-Item "$s\gta-clipper-main" gta-clipper; Remove-Item "$s\gc.zip"`
2. Read `gta-clipper/SKILL.md` there and follow "First run" with the link the user gave you. Everything installs
   through `uv`: no Homebrew, no admin, no password.

## What you get

2-3 Shorts per stream (15-45 s, 1080x1920) in `~/GTAClipper/shorts/<date>/`, each with a post caption, and a review
page that opens when they're done. Watch each one before you post it.

Fonts: Anton and Montserrat, SIL Open Font License (`assets/fonts/OFL.txt`).
