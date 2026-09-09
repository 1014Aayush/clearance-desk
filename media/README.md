# Test footage

Four clips that exercise different paths through the pipeline, plus a cue
sheet. Nothing here is committed — `media/*.mp4` is git-ignored.

| File | Runtime | What it is | What it should exercise |
|---|---|---|---|
| `01-advert-tokyo.mp4` | 57s | Advertising footage shot in Tokyo | The main case. Brands, shopfront signage in two languages, artwork, murals, faces, a music bed. A previous run found **18 items**. |
| `02-behind-the-scenes.mp4` | 5m 23s | Longer documentary-style piece | **Windowing.** Over five minutes, so it splits into overlapping windows analysed concurrently — this is the path a feature takes, and the one least tested. |
| `03-animals.mp4` | 1m 38s | Promotional film shot at a zoo — despite the filename, not wildlife footage | **The densest clip here.** A run found **16 items**: a music bed, an animated film playing on screen, a named location, three identifiable people, several brands and an on-screen campaign URL. Good for seeing the full range of categories at once. |
| `04-music-cue.mp4` | 60s | Black picture, one commercial track from 0:20 | **Acoustic identification.** The track is a released recording, so fingerprinting should name it and hand the title to research. Everything before 0:20 is silence. |
| `sample-cue-sheet.csv` | — | A two-row cue sheet matching clip 04 | **The cue sheet path.** Names the cue *before* fingerprinting runs, and declares the first cue as library music — the one claim the picture pass is not allowed to make on its own. |

## Suggested order

**`03-animals.mp4` produces the richest report** — sixteen items across almost
every category, including a genuine blocker most people would miss (an animated
film playing on a screen inside the shot, which needs a clip licence).

**`01-advert-tokyo.mp4`** is tighter and shows the spotter noticing small
things: a sticker on a wall, signage in two languages.

**Then `04-music-cue.mp4` twice:** once on its own, and once with
`sample-cue-sheet.csv` attached. The second run should name the cue without
spending anything on fingerprinting, and should mark the opening cue as
pre-cleared library music.

**Leave `02-behind-the-scenes.mp4` until last.** It is five times longer than
anything the pipeline has been run against, so it is the most likely to
surprise — and the most expensive, since more distinct works means more
research.

## Cost

Research is billed per *distinct* work, after de-duplication and triage. The
Tokyo advert cost roughly **$0.30**. The animals clip should cost close to
nothing. Budget more for the long one.

## Licensing

Clips 01–03 are Google's public Vertex AI sample media, used here to exercise
the pipeline. Clip 04 was assembled locally from the reference sample published
by the acoustic identification service for testing.

None of this footage is redistributed with the project, and none of it is
suitable for a public demo reel — shoot or licence your own for anything you
publish.
