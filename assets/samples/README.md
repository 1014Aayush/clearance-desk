# Sample cuts

Eight short excerpts that ship with the application, so anyone evaluating it can
get a real report without having to go and find footage.
They are offered in the UI under **Try a sample cut**, and each one replays a
recorded pass rather than researching again — see
[`../../clearance_desk/samples.py`](../../clearance_desk/samples.py).

## Why these eight

Stock footage is the obvious choice and the wrong one: it is deliberately
scrubbed of brands, music and recognisable faces, so it produces a near-empty
report and reads as the tool failing. These were picked for the opposite
quality — a minute that is *dense* with clearable material, and a different
kind of problem in each:

| Clip | Excerpt | The problem it puts in front of you |
|---|---|---|
| `century-21-calling.mp4` | 1:40–2:40 | Signage read straight off the frame — shopfront lettering, neon, exhibit branding — plus an architectural work and a crowd of faces. |
| `design-for-dreaming.mp4` | 2:00–3:00 | A featured performer, motor vehicle designs, and a music cue whose composer the cue sheet cannot name. |
| `duck-and-cover.mp4` | 0:00–1:00 | A public-domain film that still contains a character and a song someone may control. Three different answers to "who owns this" in one minute. |
| `word-to-the-wives.mp4` | 0:00–1:00 | A credited screen performer, named on the main title before anything else appears. |
| `chevrolet-convention.mp4` | 3:15–4:15 | A crowd of performers rather than one, wall-to-wall music, and scenic artwork — the case where nobody in frame is individually identifiable. |
| `shibuya-scramble.mp4` | 1:10–2:10 | One of two modern clips. Living trade marks whose owners still trade, an advertising screen playing a commercial inside the shot, and no cue sheet at all. |
| `piccadilly-circus.mp4` | 1:10–2:10 | A whole advertising wall at once — seven live marks in a single frame — plus a public sculpture, where the answer turns on freedom of panorama rather than on who owns the mark. |
| `street-orchestra.mp4` | 1:15–2:15 | The only clip whose music can be named from the audio, and the only one whose own licence does not survive a second look — a cover recording, so composition and master come apart. |

## Provenance

Five clips are excerpts of sponsored films held in the **Prelinger Archives**
at the Internet Archive, each carrying that archive's public domain mark. The
other three are excerpts of **Creative Commons** videos from Wikimedia Commons -
`shibuya-scramble.mp4` (CC BY 3.0, Japan Travel Rec), `piccadilly-circus.mp4`
(CC BY 3.0, Kauko Helavuo) and `street-orchestra.mp4` (CC BY 3.0, uploaded by
Yojan Smith Chipana Sierra) - and unlike the Prelinger clips they carry an
**attribution requirement** that travels with them. The Piccadilly clip's
licence has been reviewed on Commons; the Shibuya clip's is the uploader's
assertion carried over from YouTube and is still awaiting review.

`piccadilly-circus.mp4` carries the author's copyright notice burned into the
picture. It is left in place on purpose: CC BY requires the notice to be
retained, so cropping it out would breach the licence this clip ships under.

`street-orchestra.mp4` is the one sample included **despite** its paperwork
rather than because of it. The picture carries a `Rhapsody Philharmonic` credit
that does not match the Commons uploader, so the chain from performer to
uploader is not established; and the music is a released cover recording, which
no Creative Commons licence over the picture can grant. It is here because it is
the only clip in the set whose music an acoustic search can name, and because a
sample that fails its own clearance is a fair thing for this tool to be pointed
at. Do not treat its licence as settled. The
per-clip source URL, sponsor, producer and rights basis are recorded in
[`manifest.json`](manifest.json) and shown in the UI beside each sample.

The excerpts were cut from the archive's own MP4 derivatives with `ffmpeg`, and
re-encoded to keep the repository small. Nothing else was altered.

The public domain claim here is the Internet Archive's, and the Creative
Commons claim is the uploader's — both restated, not independent determinations
by this project. If you intend to use any of this
footage for something other than testing this tool, check it yourself.

## About the cue sheets

`*.cue.csv` are **illustrative sample documents**, written for these excerpts to
exercise the cue sheet path. They are not authoritative music rights data.

Where a credit is well documented it is used as it stands — the *Duck and
Cover* title song is credited to Leon Carr and Leo Corday, and that is what the
sheet says. Where it is not, the sheet says so rather than inventing a
publisher: `Unattributed — see production files` is a real thing for a cue
sheet to say, and it is the input that makes the research stage go and look.

That mix is deliberate, because it is what a real cue sheet looks like. Between
them the five sheets cover a named song with no publisher, cues marked as
pre-cleared library music, an unattributed composer, and a sheet that leaves
part of its clip uncovered — four different things for the pipeline to do.

## Adding another

Drop the video in this directory, add an entry to `manifest.json`, and it
appears in the UI. A sample whose video is missing is skipped rather than
advertised. To give it a recorded run — so testers replay instead of billing
the host — use:

```bash
python -m clearance_desk record-sample <id>
```

which prints the cost and asks before spending anything.
