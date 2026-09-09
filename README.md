# Clearance Desk

**Agentic rights clearance for film and television.**
Gemini watches the cut and reads the script. Parallel researches who actually
owns what it found. A deterministic rules engine turns both into the
errors-and-omissions clearance report every distribution deal requires.

Built for the Agentic Cinema hackathon — **Parallel track**.

**Live:** https://clearance-desk-712268517702.us-central1.run.app

---

## The problem

The clearance report is the least glamorous mandatory document in the
industry. No errors-and-omissions insurance without it; no distribution deal
without E&O. It is produced by hand: a clearance supervisor sits with the
picture and stops on every frame containing somebody else's property, then a
paralegal chases the chain of title behind each item through registries,
catalogue sales and estates.

On a feature that is weeks of work, and it is the step productions compress
when the delivery date closes in. The costs of compressing it are well
documented: a single sync clearance runs four to five rounds across *two*
separate rights holders, older recordings drag in sub-publishers and
estates, and music search alone takes days per placement. Items discovered
after picture lock are the expensive ones.

Clearance Desk does the first pass in minutes and — the part that matters —
shows its sources for every ownership claim it makes.

## Why Parallel, specifically

Ask a language model who owns the master recording of a 1968 soul single and
it will tell you, fluently and often wrongly. In clearance work a confident
wrong answer is worse than no answer: it produces a signed warranty that the
production cleared something it did not, which is precisely the exposure E&O
is meant to close.

Parallel's Task API is load-bearing here because it returns **evidence**, not
prose. Every field comes back with citations, quoted excerpts and a confidence
grade ([`research.py`](clearance_desk/research.py)), the output schema forces
research into the shape clearance actually needs — named holders, named roles,
named gaps — and the rules engine downstream refuses to clear anything whose
evidence is thin. Honest uncertainty propagates instead of being smoothed over.

Two design details follow from that:

- **The publishing/master split is demanded in the schema.** A song is two
  rights owned by two companies. Research that returns "Sony" has not answered
  the question.
- **`unresolved_issues` is a first-class output.** A finding that reports "the
  1975 catalogue sale left the master chain unrecorded" is more useful than a
  guess, because it tells a producer to start the conversation eight weeks
  early. That field is what drives the blocking status in the report.

Deep research is billed per request, so items are triaged before any call is
made: categories the web cannot answer (an unnamed extra in a crowd) are
skipped outright, hard chain-of-title problems get the `pro` processor, the
rest get `core`, and a song that plays three times in a cut is researched once.

## Why the legal position is not generated

Every risk tier, required document and blocking flag in the report is produced
by [`rules.py`](clearance_desk/rules.py) — pure Python, no model call in the
path. Given the same evidence it returns the same verdict, and it names the
rules that produced it.

That is not decoration. A clearance report that changes its mind between runs
is not something counsel can rely on, and "the model said so" is not a
defensible basis for a distribution warranty. The models observe and draft;
they never adjudicate.

The engine encodes real clearance practice rather than generic risk scoring:

| Rule | What it encodes |
|---|---|
| `MUS-002` | A public domain **composition** does not free the **recording**. Rhapsody in Blue is out of copyright; the master you actually used is not. |
| `MUS-003` | Music gets no de minimis relief. Three audible seconds of a commercial recording is actionable, so duration does not mitigate. |
| `TM-001` / `TM-003` | Incidental branding is protected expressive use — until the brand is hero-framed or shown unfavourably, which flips it into tarnishment exposure. |
| `ART-002` | Unattributable artwork cannot be licensed at any price. The remedy is to replace or obscure it. |
| `CHAIN-001` | A break in the chain of title blocks E&O binding, however small the item. |
| `DEM-001` | De minimis applies to fleeting, unfocused *visual* material only — and yields to any serious escalation. |
| `ID-001` | Music nobody can name cannot be delivered — but the remedy is the production's cue sheet, not more searching. |
| `ID-002` | A one-off physical object has no discoverable owner; it is covered by the location agreement, or the shot changes. |

Two of those came out of running the system over real footage rather than from
reasoning about it — see below.

The engine also distinguishes a **dispositive fact** (no copyright subsists;
the release is signed) from a **judgement call** (de minimis, expressive use).
Facts survive escalation — no amount of screen time creates a right that does
not exist. Judgements do not.

## What it does, end to end

```
       cut + script                                        clearance report
            │                                                      ▲
            ▼                                                      │
   ┌─────────────────┐   ┌──────────────────┐   ┌───────────────────────────┐
   │  SPOT           │   │  RESEARCH        │   │  ADJUDICATE               │
   │  Gemini 3 Pro   │──▶│  Parallel Task   │──▶│  deterministic rules      │
   │  on Vertex AI   │   │  API             │   │  (pure Python, no model)  │
   └─────────────────┘   └──────────────────┘   └───────────────────────────┘
    timecoded items       holders + citations     tier · documents · fee band
    prominence            + named chain gaps      lead time · E&O blocking
    identifiability       + confidence                       │
                                                             ▼
                                                    ┌────────────────┐
                                                    │  DRAFT         │
                                                    │  Gemini Flash  │
                                                    └────────────────┘
                                                     outreach letters
```

**Spotting** ([`spotter.py`](clearance_desk/spotter.py)) sends the picture to
Gemini and gets back timecoded observations — the Coke can at 0:03:11, the
mural behind the alley fight, the film playing on the motel TV. Feature-length
cuts are split into overlapping windows analysed concurrently at 2fps, with
window-local timecodes shifted back onto the master timeline and seam
duplicates merged (keeping the *more* exposed reading, because under-calling
prominence is the expensive mistake).

The model is asked what is in the frame, how prominently and how legibly —
never whether something is risky or fine. It is explicitly instructed never to
guess a title: a fabricated work sends research after the wrong rights holder,
which is worse than an unnamed item.

**Acoustic identification** ([`audio_id.py`](clearance_desk/audio_id.py)) sits
between spotting and research, and exists only to unblock the step after it.
Gemini can hear that music is playing and where, but not what it is; without a
name, research has nothing to look up and the cue dead-ends at "supply the cue
sheet". A fingerprint match turns "unnamed track at 14:22" into "Ain't No
Sunshine (1971)" — which is precisely the input the research stage needs to go
and find the publisher and the master owner.

It matches *released* recordings only. A score composed for the production is
in no catalogue, so it stays unidentified and `ID-001` still asks for the cue
sheet — correctly. This addresses needle-drops, which is where the money is,
since those are the two-sided clearances. Optional: without `AUDD_API_TOKEN`
the stage is skipped and every cue takes the cue-sheet path.

**The cue sheet outranks both** ([`cue_sheet.py`](clearance_desk/cue_sheet.py)).
A cue sheet is the production's own list of every piece of music in the cut —
the document distributors require on delivery and performance societies pay
composers from. Where one is supplied it wins: it names cues the picture pass
could not, it is the only thing permitted to declare a cue as pre-cleared
library music, and it adds cues buried under dialogue that were missed
entirely. It is applied before acoustic identification, so a cue the production
has already declared is never sent for a paid lookup.

Cue sheets have no standard format, so parsing is forgiving about column names
and timecode styles (including the frames field, which is dropped rather than
converted — nothing downstream is accurate to a frame), and reports what it
could not read instead of guessing.

**Adjudication** produces a position, the documents required, a fee band scaled
by prominence and distribution intent (festival-only is a quarter of worldwide
in perpetuity), a lead time, and whether the item blocks E&O.

**Drafting** writes the outgoing letter — but only where there is somebody to
write to. No letter is addressed to "public domain", and none is drafted for an
unattributable mural, because no letter would help.

## Run it

```bash
git clone <this repo> && cd clearance-desk
pip install -r requirements.txt
cp .env.example .env      # add PARALLEL_API_KEY and GOOGLE_CLOUD_PROJECT
python -m clearance_desk.server
# → http://localhost:8080
```

Click **Run worked example** for a reel of a fictional independent feature
taken from picture to a clearance position, or pick one of the bundled sample
cuts — you do not need footage of your own to see a real report.

Without a `PARALLEL_API_KEY` the research layer replays recorded fixtures
([`fixtures.py`](clearance_desk/fixtures.py)) so the pipeline, UI and tests run
offline. Those fixtures contain only publicly checkable copyright facts with
real, resolvable URLs — a fabricated citation would hollow out the one property
this product sells. Set the key and research goes live.

### Environment

| Variable | Purpose |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | Vertex AI project for Gemini |
| `GOOGLE_CLOUD_LOCATION` | Defaults to `us-central1` |
| `PARALLEL_API_KEY` | Live chain-of-title research ([platform.parallel.ai](https://platform.parallel.ai)) |
| `PARALLEL_PROCESSOR_DEEP` | Tier for hard chain-of-title work (default `pro`) |
| `PARALLEL_PROCESSOR_STANDARD` | Tier for everything else (default `core`) |
| `USE_FIXTURES` | Force offline mode |

### Tests

```bash
python -m pytest tests/ -q      # 286 tests, no network required
```

The suite pins the behaviour that matters: that identical evidence yields
identical verdicts, that every verdict carries an audit trail, that a public
domain composition never frees the master, that music never receives de minimis
relief, that a blocked chain of title is never masked by a mitigating rule, and
that window-local timecodes land in the right place on a 90-minute timeline.

## A real run

[`docs/sample-live-report.md`](docs/sample-live-report.md) is the unedited
output of a live pass against Parallel's Task API — 12 items, **139 cited
sources**, 201 source URLs, in 316 seconds.

Some of what it found, none of which was in the recorded fixtures:

- **Rhapsody in Blue** → Warner Chappell Music. The composition is public
  domain; the specific master is not, and the production never recorded which
  recording it used. Blocked.
- **Nosferatu** → Friedrich-Wilhelm-Murnau-Stiftung. A "public domain" film
  that a foundation actively administers.
- **The Starry Night** → the painting is out of copyright, but the *photographic
  reproduction* is a separate copyright licensed through Art Resource and Scala
  Group, and the production cannot say which file the framed print came from.
  The same trap as composition-versus-master, in a different medium.
- **"Acme Savings & Loan"**, invented for the demo script, matched a real Ohio
  entity — which is exactly the finding that stops a production assuming a name
  is safe because someone made it up.
- **The alley mural** → "Unidentified mural artist(s)", with an explicit warning
  not to attribute it to a painter listed on IMDb without documentation.

That last one caused a bug worth recording here: a placeholder in the holder
slot is a *finding*, not an owner. Counting it as one suppressed `ART-002` and
downgraded the remedy from "replace the asset" to "ask a lawyer". The model now
distinguishes identified holders from placeholders
([`models.py`](clearance_desk/models.py)), and both the rules engine and the
drafter read the same definition.

## What running it on real footage changed

[`docs/real-video-run.txt`](docs/real-video-run.txt) is the first full pass over
video the system had never seen — a reference advertising clip, 18 items, 172
cited sources. It found a Playboy sticker on a wall, read Japanese-language
signage, and identified Kirin Brewery from a shopfront.

It also exposed two design errors that no amount of reasoning had surfaced:

**Research was being asked unanswerable questions.** Six of eighteen items
blocked on a particular neon sign in a particular alley, a hand-painted mural,
a lampshade. There is no registry of these things, so research honestly
returned "cannot identify from a description" — which then read as a broken
chain of title. Unanswerable questions, billed per attempt.

The distinction now encoded: **published works are researchable; one-off
physical objects are not.** The spotter marks the latter, they never reach
research, and the answer comes from the production's own location file. Same
clip after the fix: **9 items researched instead of 15, one blocking item
instead of six.**

**"Obtain a release" was being recommended for works with no findable owner.**
That is not a remedy — there is nobody to write to. Unattributable artwork now
routes to the real options: obscure it in post if it is in the background,
replace or reshoot if it is featured.

## Sample cuts, so a tester needs no footage

Asking an evaluator for a video before the tool will say anything is a bad
first minute, and whatever they reach for is usually a phone clip of nothing in
particular — which produces an empty report and reads as the system failing.

Five sixty-second cuts therefore ship with the application, in
[`assets/samples/`](assets/samples/). Each is an excerpt of a public-domain
sponsored film from the Prelinger Archives, picked because a lot is happening
in it at once, and each puts a different problem in front of you:

| Clip | The problem |
|---|---|
| *Century 21 Calling* (1964) | Signage read straight off the frame, an architectural work, a crowd of faces. |
| *Design for Dreaming* (1956) | A featured performer, vehicle designs, and a cue whose composer the cue sheet cannot name. |
| *Duck and Cover* (1951) | A public-domain film that still contains a character and a song someone may control. |
| *A Word to the Wives* (1955) | A credited screen performer, named on the main title. |
| *Chevrolet Sales Convention Musical* (1954) | A crowd of performers rather than one, and wall-to-wall music. |

Every clip carries a cue sheet, so the cue sheet path can be exercised without
one being written first, and the provenance and public-domain basis of each is
recorded in [`assets/samples/README.md`](assets/samples/README.md). Stock
footage would have been the easier choice and the wrong one: it is deliberately
scrubbed of brands, music and recognisable faces, so it demonstrates nothing.

Like the worked example, each sample **replays a recorded pass** rather than
researching again — five samples researching live on a public URL would bill
the account hosting the page once per tester per clip. Record them with:

```bash
python -m clearance_desk record-sample            # all of them
python -m clearance_desk record-sample duck-and-cover
```

which states the cost and asks before spending anything.

## Reporting what was found, not only what is missing

Two different findings were being printed as one sentence. "We traced this to
Leon Carr and Leo Corday, but cannot prove who administers it today" and
"nobody can say who painted this backdrop" both came out as *chain of title
unresolved* — so a pass that identified an owner every time it looked still
read, line after line, as a search that had failed.

The legal position was never wrong. Both are blockers, both go to a lawyer,
both carry the same lead time. Only the description was wrong, and `CHAIN-001`
now distinguishes them:

- **Owner identified, but the chain to today is incomplete** — a party is named
  in the sources; what is missing is proof of who holds the right now.
- **No owner could be identified** — nobody is named, and there is no one to
  write to.

Three presentation changes follow from the same point. The report now carries
an **owners found** figure beside the count of blockers, so at least one number
on screen records what the pass achieved. The detail pane opens with the owner
and the sources behind it, rather than with the gap. And the gap section is
headed *what is still open* when an owner was found, instead of *what nobody
could establish*.

On the five bundled samples this is the difference between reading as a tool
that mostly fails and one that identified a rights holder for **39 of the 39
items it researched**.

Recordings are snapshots, so a rule change leaves them stating the old finding.
Bringing them back into line costs nothing and calls nothing:

```bash
python -m clearance_desk readjudicate
```

Adjudication is a pure function of the item, the finding and the project, so
the recorded research is reused exactly as returned and only the position is
recomputed. It reports how many verdicts were reworded and how many actually
changed position — if positions moved, the recorded summary paragraph was
written against the old ones and that run needs re-recording properly.

## Nothing bills without being asked

Every path that reaches a paid API stops at a confirmation first: running the
worked example live, analysing an uploaded cut, and running a sample that has
no recording yet. Each one names what will happen and what it is expected to
cost before it happens.

The dialog is deliberately biased towards not spending. Focus lands on
*Cancel*, Escape and Enter both cancel, and clicking outside cancels — only a
deliberate click of the confirming button proceeds. This exists because the
"run it live" link in the replay banner used to be a bare link, one stray click
away from a live pass.

## The demo button replays a recorded run

Research is billed per request. A public "run the demo" button therefore
charges the *owner* once per visitor, not once per demo — a shared link is a
slow drain on a fixed budget, and the failure mode is the worst available: the
account empties and the public URL ends up demonstrating a dead app.

So the demo runs live once and is recorded
([`demo_cache.py`](clearance_desk/demo_cache.py)); visitors replay it.

This is a replay, not a simulation. The served report is the exact output of a
real pass — real Task API calls, real citations, real adjudication — and it is
labelled as a replay in the UI, in the rendered report, and in the API
(`replay_of`, `replay_recorded_at`), with the date it was recorded. Passing a
recording off as a live call would misrepresent what the system just did.

```bash
python -m clearance_desk record-demo   # runs live once, prompts with the cost
```

`POST /api/runs/demo?live=true` forces a fresh pass.

## Deploy

**Cloud Run** (the hosted UI). One-time setup — the API key goes to Secret
Manager rather than into an image layer or an environment variable in the
deploy history:

```bash
PROJECT=your-project-id
gcloud config set project "$PROJECT"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com aiplatform.googleapis.com \
  secretmanager.googleapis.com

printf '%s' "$PARALLEL_API_KEY" | \
  gcloud secrets create parallel-api-key --data-file=- --replication-policy=automatic

# Optional, but without it acoustic identification is off in production and
# unnamed cues are reported as unchecked rather than quietly named.
printf '%s' "$AUDD_API_TOKEN" | \
  gcloud secrets create audd-api-token --data-file=- --replication-policy=automatic

# A fresh project grants its default compute service account none of the roles
# Cloud Build needs. Without these the first deploy fails at "Uploading
# sources" with an opaque storage.objects.get permission error.
SA="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')-compute@developer.gserviceaccount.com"
for role in roles/cloudbuild.builds.builder roles/storage.objectViewer \
            roles/artifactregistry.writer roles/logging.logWriter \
            roles/aiplatform.user; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA" --role="$role" --condition=None
done
gcloud secrets add-iam-policy-binding parallel-api-key \
  --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
gcloud secrets add-iam-policy-binding audd-api-token \
  --member="serviceAccount:$SA" --role=roles/secretmanager.secretAccessor
```

Then deploy (repeat this line alone for subsequent deploys):

```bash
gcloud run deploy clearance-desk \
  --source . --region us-central1 --allow-unauthenticated \
  --memory 2Gi --cpu 2 --timeout 3600 --max-instances 3 \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT,GOOGLE_CLOUD_LOCATION=us-central1,GOOGLE_GENAI_USE_VERTEXAI=true,SPOTTER_MODEL=gemini-2.5-pro,DRAFTER_MODEL=gemini-2.5-flash,PARALLEL_PROCESSOR_STANDARD=core-fast,PARALLEL_PROCESSOR_DEEP=pro-fast,USE_FIXTURES=false" \
  --set-secrets "PARALLEL_API_KEY=parallel-api-key:latest,AUDD_API_TOKEN=audd-api-token:latest"
```

Model availability is project- and region-specific. Verify before deploying:

```bash
python -m clearance_desk check-models
```

**Agent Engine** (the conversational surface):

```bash
pip install "google-cloud-aiplatform[agent_engines,adk]>=1.112.0"
export GCS_BUCKET=your-staging-bucket
python -m clearance_desk.deploy
```

Or locally: `adk web clearance_desk`.

## Where Google Cloud and Parallel are actually called

Both are imported and invoked at runtime, not merely named here.

| | Where |
|---|---|
| Vertex AI / Gemini — multimodal spotting | [`spotter.py`](clearance_desk/spotter.py) → `genai.Client(vertexai=True)`, `models.generate_content` with `VideoMetadata(fps=…, start_offset=…)` and a Pydantic `response_schema` |
| Vertex AI / Gemini — drafting & summary | [`drafter.py`](clearance_desk/drafter.py) |
| Parallel Task API — chain of title | [`research.py`](clearance_desk/research.py) → `client.task_run.create(...)`, `client.task_run.result(...)`, basis/citations preserved verbatim |
| Google ADK — agent + tools | [`agent.py`](clearance_desk/agent.py), [`deploy.py`](clearance_desk/deploy.py) |

## API

| Endpoint | |
|---|---|
| `POST /api/runs` | Start a pass over a cut and/or script |
| `POST /api/runs/demo` | Run the worked example |
| `GET /api/runs/{id}` | Report, rollups and next actions |
| `GET /api/runs/{id}/events` | Progress stream (cursor-paged) |
| `GET /api/runs/{id}/report.md` | Rendered clearance report |
| `GET /api/rules` | Full rule catalogue |

## Limitations

Stated plainly, because a clearance tool that oversells itself is dangerous.

- **Not legal advice.** This is a research and triage instrument. It does not
  replace a clearance attorney, and it tracks what is *required* — never what
  has been secured.
- **Spotting is a first pass.** The model errs toward over-reporting by design;
  a human reviews the schedule. It will miss things, particularly music it
  cannot name and background detail below the sampling rate.
- **Fee bands are estimates.** Heuristics scaled by prominence and distribution
  intent, overridden by real licensing precedent when research finds it. They
  size a budget conversation; they are not quotes.
- **US-centric copyright reasoning.** Public domain determinations follow US
  terms. Territory notes are surfaced but the rules do not yet fork by
  jurisdiction.
- **Runs are held in memory.** Fine for a review session on a single instance;
  a multi-instance deployment needs Firestore behind `RunStore`.

## Licence

MIT — see [LICENSE](LICENSE).
