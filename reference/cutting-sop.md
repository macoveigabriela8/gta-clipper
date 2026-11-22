# Cutting SOP: every Short, every time

Each rule below was a real bug in a test Short. The right column is the automatic QA check that catches it
(`scripts/clipper/qc.py`). `gc make` runs every check on every Short; ✗ = fail (fix before the user sees it),
! = warning (look, fix if you can).

## 1. Pick the moment

| Rule | Why (the bug it prevents) | QA check |
|---|---|---|
| Setup, then payoff. Sound off, a stranger gets it in 2 s. | A Short with no point gets scrolled. | eye check 5 |
| **Mark the payoff**: `"payoff"` (what happens) and `"payoff_at"` (its second in the video). | Without it, "where does it end?" is a guess. | ✗ `payoff` |
| End **1-3 s after the payoff** (the reaction, the crash, the "no way"). | Ended mid-scene: "I don't understand the ending". | ✗ `payoff` (0.3-4.5 s after) |
| Start 3-6 s before the setup. 15-45 s long. | No context, or too long to hold. | ! `link_start`, ✗ `length` |
| The streamer talks or reacts out loud (0.5+ words a second). | Only the game is heard; captions empty. | ! `talking` |
| **Not the end of somebody else's clip.** A 30 s Twitch clip often stops mid-scene. If the payoff isn't inside the link, ask the user for a longer link (the full VOD). | The Short stopped where a viewer stopped the clip. | ✗ `link_end` (add `"end_checked": true` only after you SAW the payoff on the QA sheet) |
| Keep 🔇 silent stretches (listed in the scan notes) out of the window. | Muted audio for 9 s at the start; dead air in the middle. | ✗ `silence` |
| No menus, loading screens, map browsing, music or radio (claims). | Frozen picture, copyright claims. | ✗ `frozen` |
| A compilation: stay inside one streamer's clip. | Two faces, two games in one Short. | ✗ `webcam` |

## 2. The hook (black pill, first 3 s)

5-9 plain words: what's about to happen, never the payoff. No emoji, no hashtags. (! `hook`)
Formulas: **He thought X…** · **Stakes** ("He just paid 25K for this truck") · **Wait for it** · **POV** ·
**Number** ("He tried this 47 times") · **Question**.

## 3. What the tool does for you (don't redo it by hand)

- Trims dead air at the very start (> 0.25 s) and end (> 0.9 s).
- Moves a start or end that lands inside a word out to the word's edge (QA: ! `clean_start`, ! `clean_end`).
- Finds the game's subtitles and the stream's own caption bars at the bottom and cuts the gameplay above them,
  so only our captions show (QA proves it on the finished Short: ✗ `no_double_captions`).
- Finds the webcam and checks the top band really shows a face in the same room (✗ `webcam`).
- Brings the sound to about -14 LUFS with a limiter (✗ `loudness`, ! `peak`).

## 4. Automatic QA on every Short (all measured)

| Check | Rule | Fail / warn |
|---|---|---|
| `plays` | decodes start to end without an error | ✗ |
| `length` | within 0.25 s of the planned length | ✗ |
| `size` | 1080 x 1920 | ✗ |
| `shorts_length` | 59 s or less (YouTube Shorts) | ! |
| `sound` | has sound, not silent | ✗ |
| `loudness` | -14 LUFS ± 3 | ✗ |
| `peak` | true peak -0.5 dB or lower | ! |
| `silence` | no 2 s+ of dead air (under -35 dB on the finished Short) and no muted stretch in the source | ✗ |
| `black` | no black picture over 1 s | ✗ |
| `frozen` | the picture never stands still 2.5 s+ | ✗ |
| `no_double_captions` | no text rows left low in the gameplay (same height in 2+ frames) | ✗ |
| `webcam` | top band: a whole face (not cut by the edge) in 50%+ of frames, background change 0.12 or less | ✗ |
| `one_clip` | the webcam never jumps mid-Short (a compilation switching streamer, a zoom) | ✗ |
| `bright_enough` | gameplay not near-black in most frames | ! |
| `payoff` | payoff marked, the Short ends 0.3-4.5 s after it | ✗ |
| `link_end` | doesn't run to the very end of the link, unless checked by eye | ✗ |
| `link_start` | doesn't start at 0 s of a short clip | ! |
| `clean_start` / `clean_end` | no word cut in half, no end mid-sentence | ! |
| `talking` | 0.5+ words a second | ! |
| `hook` | 3-10 words, no emoji or hashtag | ! |

## 5. Eye check: read every `<name>_qa.jpg` (hook, middle, last 4 s)

1. Top band = the streamer's real webcam (or full-height gameplay when there's no cam), face not cut off.
2. No text near our captions saying the same words (game subtitles, stream captions).
3. The first frame shows the hook pill over moving action (no menu, no loading screen).
4. Nothing important cut in half at the edges (a chat box or an alert sliced down the middle).
5. **The last 4 frames show the payoff or the reaction.** If the ending doesn't land, move `"b"` or drop it.
6. Captions sit on the seam, readable, and don't cover the face.

## 6. Fix loop

| Fails | Fix |
|---|---|
| `payoff` / `link_end` | Move `"b"` to 1-3 s after the payoff. No payoff inside the link: tell the user and ask for the full VOD. |
| `silence` | Move the window off the silent stretch, or split it into two moments. |
| `frozen` / `black` | Move the window off it. |
| `no_double_captions` | Set `"drop_bottom": 0.25` on the moment (share of the height to cut, max 0.3). |
| `webcam` | Set `"facecam": false` (or give `"cam": {x, y, w, h}` in the video's pixels). |
| `loudness` | Make it again; still off = the source is clipped or near-silent: pick another moment. |
| `talking` (!) | Prefer a moment where the streamer reacts out loud; keep it only if the picture tells the story. |

Make again (`gc make`), at most two rounds. Still failing: drop the moment and tell the user why in one line.
Only hand over Shorts that passed every ✗ check and the eye check. Tell the user, per Short, one line:
"✓ QA passed" or what you fixed.

## 7. Top 5 countdown (one Short, five moments, #5 first, #1 last)

Offer it only when the stream has 5+ decent moments. Plan shape (next to `"moments"`, or alone):
`"top5": {"hook": "His 5 wildest RP moments", "caption": "Which one was the worst? #gtarp", "items": [`
`{"rank": 5, "a": 122, "b": 129, "label": "Granny's new boy toy", "payoff_at": 128.4}, ... ]}`
(`"cam"` / `"facecam"` / `"drop_bottom"` per item as for moments; `"cam"` on `top5` = for every item.)
Each piece is made like a normal Short; its pill reads "#3 LABEL" for the whole piece (piece #5 also shows the hook
on the line above). The five are joined into `top5_<hook>.mp4` with a short whoosh on every rank change (generated
by ffmpeg from noise, so nothing to license; 9 dB under the game, loudest right on the cut), levelled, and checked.

| Rule | Why | QA check |
|---|---|---|
| Ranks 5, 4, 3, 2, 1, each once. Worst first, best last. | A countdown that skips or repeats a number reads as a mistake. | ✗ `ranks` |
| Each piece 4-9 s around its payoff: start right before the setup line, end 0.3-3 s after the payoff. | Long pieces kill the pace; a piece cut before its payoff has no point. | ✗ `item_length` (3-10 s), ✗ `item_payoff` |
| Five different moments, at least 1 s apart in the video. | The same seconds playing twice. | ✗ `items_apart` |
| Labels 2-4 words, the moment's name ("Laughing crash"), not the joke. | Long labels wrap small and nobody reads them. | ! `labels` |
| Hook 3-8 words, short enough to sit on one line above "#5 ...". | A long hook shrinks the first pill. | ! `hook` |
| Total 59 s or less. | YouTube Shorts limit. | ! `total_length` |
| Edges on word gaps (pick `a` / `b` between two words in `words.json`). | A word cut in half at a cut. | ! `item_edges` |
| No menus, dark scenes or someone else's clip in a piece. | Same as any Short. | the file checks in § 4 |

Automatic checks on the joined Short: every § 4 file check (plays, length, size, sound, loudness, peak, silence,
black, frozen, no_double_captions, bright_enough, shorts_length), plus `badge_visible` ✗ (one frame half-way
through each piece: the rank pill is really there, 70%+ of its pixels match), `webcam` ✗ judged per piece (each
piece has its own cam crop) and `whoosh` ! (one per rank change, 8-10 dB under the game, measured). The single-moment checks (payoff, link_end, clean_*, talking, one_clip) are replaced by
the per-piece ones above.

**Eye check:** read `top5_<hook>_qa.jpg` (row 1 = first frame of #5..#1, row 2 = last frame): the pill readable in
every frame, the real webcam on top, one set of captions, each last frame on its payoff.

| Fails | Fix |
|---|---|
| `ranks` | Number the items 5..1, each once. |
| `item_length` / `item_payoff` | Move that item's `"a"` / `"b"` to 4-9 s ending 0.3-3 s after `"payoff_at"`. |
| `items_apart` | Pick another moment for one of the two. |
| `badge_visible` | Look at the QA sheet; usually a broken render: make it again. |
| `webcam` (#N) | Give `"cam"` by hand on item #N (or `"facecam": false`). |
| `item_edges` (!) | Move that edge into the gap between two words. |
| `whoosh` (!) | Make it again; still off = the pieces are near-silent (pick louder moments). |

## 8. Reaction zoom (the face takes over at the payoff)

Pick it when the streamer's **face is the punchline**: a scream, a laugh, a stunned stare right as the payoff hits
(the crash, the kill, the reveal). Not when the payoff is only on the game screen, and never without a webcam.
Add `"zoom": true` to that moment (it needs `"payoff_at"`). At the payoff the Short cuts to the face, full screen,
with a slow push (1.00 → 1.06) for 1.5 s, then cuts back to the split layout; if that would leave under 1 s, it
stays on the face to the end. A moment without `"zoom"` renders exactly as before.

| Rule | Why | QA check |
|---|---|---|
| The zoom starts on `payoff_at`, measured on the finished Short (the cut to the face within 0.3 s), and cuts back when it returns. | Early, it spoils the joke; late, the reaction is gone. | ✗ `zoom_window` |
| The face fills the screen: in 3 frames inside the zoom, 2+ show a face 30-115% of the screen's width, centred (measured on the webcam pixels the zoom crops: the detector can't read a face blown up 8x). | A zoom on a wall, a hand or half a face is a mistake. | ✗ `zoom_face` |
| The webcam is blown up 9x or less. | A tiny webcam turns to mush full screen. | ! `zoom_sharp` |
| At least 0.8 s of Short after the payoff, a webcam, a `payoff_at`. | Otherwise no zoom is made. | ! `zoom` |
| Our captions and the hook stay on top of the zoom; the captions drop to 80% of the height while it shows. | At the usual seam a full-screen face has its eyes right there. | eye check |

The § 4 band checks (`webcam`, `bright_enough`, `no_double_captions`) don't sample frames inside the zoom (there is no
split layout there); every other check runs on the whole Short.

**Eye check:** the last-4-s row of `<name>_qa.jpg` holds the zoom: the face fills the screen, eyes and mouth visible,
not cut off, not a blurry mess, captions low (on the chin / chest), not over the eyes.

| Fails | Fix |
|---|---|
| `zoom_window` | `"payoff_at"` is off: find the exact second (`gc look --every 0.5`), make it again. |
| `zoom_face` | The cam box misses the face: give `"cam"` by hand (the whole webcam), make it again. Still failing: remove `"zoom"`. |
| `zoom_sharp` (!) or a mushy face on the QA sheet | Remove `"zoom"` from that moment rather than ship a blurry face. |
| `zoom` (!) | Read the note: add `"payoff_at"`, end 1-3 s after it, or remove `"zoom"` (no webcam). |

## 9. Instant replay (the payoff again, slowed, after the Short)

Pick it when the payoff is **fast and visual**: a crash, a fall, a shot, a sudden scare. A viewer wants to see it
again, and half speed shows what they missed. Skip it for payoffs that are mostly words (a joke, a line): words at
half speed sound wrong. Add `"replay": true` to the moment. Nothing else changes: `"payoff_at"` is required, and
`"b"` still ends 1-3 s after the payoff. The replay comes after `"b"`.
What `gc make` adds: the stream from `payoff_at - 1.0` to `payoff_at + 0.5`, played at half speed (3 s, the sound
slowed too, pitch kept). The gameplay is zoomed 1.2x around its centre. The webcam band, the game crop and the
cut-off bottom are exactly the main part's. A black "REPLAY" pill (the hook's style) stays the whole 3 s, with no
captions. Then one hard cut joins it to the main part, and the loudness, cover and QA run on the joined Short.

| Rule | Why | QA check |
|---|---|---|
| `payoff_at` inside the moment, 1 s or more after `"a"` and 0.5 s or more before `"b"`. | Without it there's no replay, or a cut-short one. | ✗ `replay_length` (2.5-4 s) |
| The replay shows the payoff itself. | A replay of the wrong second is a bug, not an edit. | ✗ `replay_matches` (the frame half-way through the replay vs the stream at that second, cropped the same way: difference ≤ 0.03, and smaller than unzoomed, which proves the zoom) |
| The REPLAY pill is on screen. | Without it, the replay reads as a glitch that repeats. | ✗ `replay_badge` (70%+ of the pill's pixels match half-way through) |
| Main part + replay = the file's length. | A broken join. | ✗ `length` (the real lengths of both parts) |

The single-moment checks (`payoff`, `link_end`, `clean_*`, `talking`) judge the main part only: they use `"a"` / `"b"`,
not the replay. Every § 4 file check runs on the joined Short.

**Eye check:** the last row of `<name>_qa.jpg` is the REPLAY row (each frame labelled with its stream second). The
payoff is visible and bigger than in the END frames above it, the pill is readable, the webcam band looks the same
as in the main part (no jump), and there are no captions.

| Fails | Fix |
|---|---|
| `replay_length` | Set `"payoff_at"` to the real second (look at the QA sheet), or move `"a"` / `"b"` so it sits 1 s after `"a"` and 0.5 s before `"b"`. |
| `replay_matches` | Check `"payoff_at"` first, then make it again (a broken render). |
| `replay_badge` | Make it again; still failing = look at the REPLAY row on the QA sheet. |
| A slowed line sounds odd (eye/ear check) | Drop `"replay"` on that moment: its payoff is words, not action. |

One extra per moment: `"zoom"` or `"replay"`, not both (the replay is cut from the plain layout).
