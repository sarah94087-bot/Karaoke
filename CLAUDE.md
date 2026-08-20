# karaoke — working notes

Hebrew karaoke player. Upload a song, get separated stems, sing over the
backing track with real-time key and tempo control and timed lyrics.

The spec and the task breakdown are in `docs/`. Findings from phase 0 are in
`docs/phase0/` and are referenced by later decisions — read them before
reopening a decision they already settled.

## Where things are

| Path | What |
|---|---|
| `apps/{api,web,gpu}` | product code — FastAPI, Next.js, remote GPU functions |
| `packages/{core,audio,lyrics,providers}` | shared domain code |
| `research/` | phase 0 measurement scripts and prototypes — finished, kept to reproduce numbers |
| `docs/phase0/` | findings and verified quotas |
| `infra/{docker,github}` | phase 3 |

`packages/providers` is the seam for every external service. Phase 0 already
forced two provider changes, so keep it thin.

## Commands

Run every check (lint, format, types, tests):

```
.venv\Scripts\python.exe scripts\check.py
```

`--fix` applies lint fixes and reformats. CI runs this same file.

Bring up the local environment (API + Postgres):

```
docker compose -f infra\docker\compose.yaml up
```

Run the API alone, without Docker (docs at `/docs`, health at `/system/health`).
Use the module, **not** `uvicorn` directly — see the psycopg note below:

```
.venv\Scripts\python.exe -m apps.api --port 8000
```

Run the web app (Hebrew at `/he`, English at `/en`):

```
cd apps\web
npm install
npm run dev
```

Serve the prototypes locally:

```
.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1
```

Run migrations (needs the compose stack up, or any `DATABASE_URL`):

```
$env:DATABASE_URL = "postgresql://karuki:karuki@localhost:5432/karuki"
.venv\Scripts\python.exe -m alembic upgrade head
.venv\Scripts\python.exe -m alembic downgrade base
```

The same step in the image, which is how it runs on deploy:

```
docker compose -f infra\docker\compose.yaml run --rm api alembic upgrade head
```

Deploy / measure the GPU function:

```
.venv\Scripts\python.exe -m modal deploy apps\gpu\karuki_modal.py
.venv\Scripts\python.exe apps\gpu\run_remote.py measure 5
```

Note: `modal deploy` reports "no changes detected" even after edits. Bump
`BUILD` in `karuki_modal.py` to force it.

## Environment quirks that cost time before

- PowerShell 5.1 has no `&&`. Use `;` or separate commands.
- psycopg's async driver cannot run on Windows' default `ProactorEventLoop` and
  says so in a message that reads like a broken database. Never comes up in the
  container, which is Linux. `packages/core/db.py` has
  `use_a_loop_psycopg_can_run_on()`; call it before `asyncio.run` in any local
  entry point that opens a connection.
  **`uvicorn apps.api.main:app` does not work locally because of this**, and two
  obvious fixes do not help: setting the policy in `main.py` is too late (the
  app is imported after the loop exists), and setting it before `uvicorn.run`
  has no effect (uvicorn builds its loop from a `loop_factory` and never
  consults the policy). `apps/api/__main__.py` runs the server on a loop it
  creates itself, which is why the command above is `python -m apps.api`.
  `--reload` still spawns a child process with its own loop, so it only works
  for changes that do not touch the database.
- This machine runs TLS inspection. `requests` fails with "self-signed
  certificate in certificate chain" — `research/verify_groq.py` fixes it with
  `truststore.inject_into_ssl()`, not by disabling verification. `curl` needs
  `--ssl-no-revoke`.
- **A default `User-Agent` is not neutral.** Groq's edge answers
  `Python-urllib/3.11` with `403`, and on a POST it drops the connection
  instead — which arrives as `EOF occurred in violation of protocol` and reads
  exactly like a broken TLS stack on a machine that really does have TLS
  inspection. `packages/providers/net.py` holds the one `User-Agent` every
  outbound call sends, next to the `truststore` injection, because both are
  one-line fixes to failures that look like something else.
- Console is cp1255. Set `PYTHONIOENCODING=utf-8` when a tool prints Unicode,
  and pass `encoding="utf-8"` to `subprocess` when filenames are Hebrew.
- Shared code is imported as `packages.core`, `packages.audio`, … — not bare
  `core`/`audio`. The bare names exist on PyPI, and having `packages/`
  resolvable under two module names at once is what mypy refused to accept.

## Rules that come from the spec, not from preference

- **No credit card on any service.** Chapter 1 is explicit: without a card the
  worst case is a pause, with one it is a surprise bill. Cloudflare R2 was
  rejected on these grounds even though its free tier fits.
- Published free-tier numbers are unreliable. Two of three were wrong in the
  project's disfavour. Verify in the account, which is what `T-0.6` is for.
- The Supabase project configured in the environment is **not ours**. Do not
  connect to it.

## Status

Phase 0 closed: 19 of 21 tasks done, one cancelled, one blocked on hardware
(`T-0.2.5`, the phone test — everything for it is built and waiting).

Phase 1 closed: `T-1.1` through `T-1.17` done — upload, separation, jobs, the
library, and a working player.

Phase 2 (lyrics) in progress. `T-2.1` through `T-2.3` done. Next is `T-2.4`:
running the transcription twice — on the mix and on the vocals — and recording
which of the two won in `source` (D-29).

Docker Desktop is installed as of 2026-08-18, but it puts `docker` on the
machine PATH without refreshing an already-open shell. If `docker` is "not
recognized", the session's PATH is stale — reopen the terminal, or in
PowerShell:

```
$env:Path = [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
```

From `T-1.2`, worth knowing before touching `apps/api`:

- `/system/health` is registered twice — documented at `/api/v1/system/health`,
  and unlisted at `/system/health` for the container healthcheck and the
  keep-alive cron, so those are configured once and survive a prefix change.
  It must stay cheap: no DB, no storage, no outbound call. A test asserts it.
- Every response carries `request_id`, echoed as `X-Request-ID`. The 500 case is
  handled in `apps/api/middleware.py`, not by an app-level handler, because an
  app-level handler runs outside the middleware and would emit the one response
  with no id on it.
- Errors are `{"error": {"code", "message"}, "request_id"}`. `code` is what the
  web app maps to Hebrew text.
- Dependencies are declared in `pyproject.toml` under `[project.optional-dependencies]`
  but the local venv was built by hand in phase 0 and is not installed from
  them. The image generates its requirements from the `api` group at build time
  via `infra/docker/requirements.py`, so there is no second dependency list to
  drift.

From `T-1.3`:

- The compose file lives at `infra/docker/compose.yaml`; the build context is
  the repository root, because the image needs `packages/` as well as `apps/`.
- `api` waits on `db`'s healthcheck (`service_healthy`), not merely on the
  container starting — otherwise the first connection races Postgres's init.
- Postgres is pinned to `17-alpine`. It should match whatever hosted Postgres
  `D-15` lands on; a mismatch discovered at deploy time is the expensive kind.
- The container `HEALTHCHECK` uses the unprefixed `/system/health` on purpose.
- Verified end to end on 2026-08-18: image builds (239MB), both containers go
  `healthy`, the API reaches `db:5432`, Postgres reports 17.11, and a table
  survives `down` + `up` on the `db_data` volume.
- The image would not build at first: the dependency extraction was a
  multi-line `python -c`, and Docker joins continuations with a leading space,
  which Python rejects as an unexpected indent. That is why it is a file,
  `infra/docker/requirements.py`, and not a one-liner.

From `T-1.4`:

- Models are in `packages/core/{enums,models,db}.py`; Alembic lives at the repo
  root (`alembic.ini`, `migrations/`) because chapter 10 makes migrations a
  deploy step of their own, ahead of the new code.
- The URL comes from `DATABASE_URL` in `migrations/env.py`, not from
  `alembic.ini`, so the migration step and the API cannot disagree about which
  database they mean.
- Vocabularies are `CHECK` constraints over varchar, not native enum types:
  adding a state later stays an ordinary migration with a working downgrade, and
  the schema stays portable while `D-15` is open.
- Every constraint and index is named by the convention in `packages/core/db.py`.
  Without it Postgres names them and a downgrade cannot drop by name what it did
  not name — this is what makes "runs down cleanly" true on more than one machine.
- Ids default to `gen_random_uuid()` in the database, not only in Python. A
  Python-only default breaks every insert that bypasses the ORM.
- `jobs.user_id` has no foreign key: users belong to a managed auth provider and
  `D-16` is undecided.
- `songs`, `stems`, `jobs`, `user_song_settings` (from `T-1.16`) and `lyrics` /
  `lyric_lines` (from `T-2.1`) exist. The other two tables in chapter 5 come
  with the tasks that use them.
- `tests/test_migrations.py` needs a real Postgres and skips without one. It
  drops every table, so it refuses to run unless `DATABASE_URL` points at a
  local host.

From `T-1.5`:

- `packages/audio/normalize.py` is the only place that shells out to
  ffmpeg/ffprobe. Everything downstream may assume 44.1kHz stereo 16-bit PCM;
  that assumption is the whole point of normalising at the boundary.
- ffmpeg is in the API image, in its own layer ahead of the pip layer: it is the
  slowest part of the build and the least likely to change.
- `POST /api/v1/songs/upload` takes the bytes **through the API**, which is not
  what chapter 6 describes (signed URL, browser uploads straight to storage).
  That shape needs `D-12`, which is deferred. `packages/providers/storage.py` is
  the seam that keeps the swap cheap.
- Storage keys are object keys (`songs/<id>/normalised.wav`), not paths, and
  `LocalStorage` rejects any key that would escape its root. Locally the root is
  `var/` (gitignored); in the container it is the `storage_data` volume.
- `content_hash` is the sha256 of the **normalised** audio, so the same song as
  mp3 and as m4a dedups to one row. A repeat upload answers `200`, not `201`,
  with `already_existed: true`.
- The size limit is enforced as the bytes arrive, not from `Content-Length`,
  which is only a claim.
- The song row is flushed, not committed, until storage has succeeded — a row
  pointing at files that are not there is worse than no row.

From `T-1.6`:

- `packages/providers/separation.py` has two backends behind one protocol:
  `local` (Demucs on the CPU — free, ~1.13× song length) and `modal` (the GPU
  function deployed in `T-0.3`). `packages/core/stems.py` is the internal
  service; nothing above it knows a GPU exists.
- **`local` is the default, on purpose.** The Modal workspace has **$1/month**,
  not $30 (`docs/phase0/quotas.md`), so a stray run spends real money. The
  remote backend has to be asked for by name, via `KARUKI_SEPARATION_BACKEND`.
- The Modal path is tested against a fake. **No test spends GPU credit**, and it
  should stay that way.
- torch and demucs are in their own `separation` dependency group, **not** in
  `api`: they are ~2GB and would take the API image from 306MB to unshippable
  on a free tier. In production the `modal` backend does the work. The local
  backend raises a clear `SeparationError` when the group is missing.
- Stems are mp3 128k, matching the storage budget phase 0 measured. Verified
  end to end on a real 30s excerpt: 47.9s separation on CPU, four distinct
  stems, 1.84MB — which extrapolates to ~14.7MB per four-minute song, against
  the 15.4MB phase 0 measured.
- The four stems are encoded concurrently. Phase 0 measured serial encoding at
  15.5s against 6.2s for the separation itself.
- Re-running `separate_song` replaces the stems rather than adding a second set;
  `(song_id, kind)` is unique and chapter 7 wants every stage re-runnable.

From `T-1.7`:

- `packages/core/jobs.py` is the state machine, `packages/core/pipeline.py` runs
  a job through it, `apps/api/runner.py` starts it in the background. D-25: no
  Celery, no Redis — the status in Postgres *is* the queue.
- Progress is derived from the step (`STEP_PROGRESS`), never passed in. A caller
  free to choose its own number eventually reports 90% twice.
- **Every step commits before doing the work.** That is what "survives a
  restart" means in practice: the progress a user is looking at has to be
  durable at the moment they see it.
- On startup the lifespan runs `recover_interrupted`, which marks any row still
  in `running` as `failed` / `interrupted`. Jobs run in this process and chapter
  9 budgets for one instance, so a `running` row at startup is always orphaned.
  It is **not** re-queued: that would be an automatic retry of a step that may
  have cost GPU credit, which chapter 7 forbids. The user retries via
  `POST /jobs/{id}/retry`.
- Verified for real: killed the API mid-separation, the row was left `running`,
  the restart turned it into `failed`/`interrupted`, and the retry then ran to
  `ready` with `attempts=2`.
- Separation runs in a worker thread (`asyncio.to_thread`). Inline it would
  freeze the event loop and the keep-alive ping would time out, so the platform
  would call the service unhealthy while it is working perfectly.
- **The compose container cannot separate** — no torch by design (see T-1.6). It
  reports `separation_unavailable`, deliberately distinct from
  `separation_failed`: one is the operator's problem, the other is the file's.
  Local separation needs the venv, i.e. `python -m apps.api`.
- Uploading now starts a job and returns `job_id`, which is chapter 6's
  `POST /songs` behaviour.

From `T-1.8`:

- `GET /api/v1/jobs/{id}/events` is the SSE stream (D-18). `packages/core/events.py`
  is an **in-process** bus, not database polling and not `LISTEN/NOTIFY`: the job
  runs in this process (D-25), so the process already knows the moment anything
  changes. Polling would cost a query per client per second on a free-tier
  Postgres where connections are the scarce resource; `LISTEN/NOTIFY` wants a
  connection dedicated to listening. This assumes one API instance, which
  chapter 9 requires anyway. If that changes, that file is what becomes
  `LISTEN/NOTIFY`.
- **Subscribe before reading the snapshot.** The other order leaves a gap in
  which the job can finish unobserved — the snapshot says "running", `ready` is
  published to nobody, and the client waits forever. This was a real hang, found
  because the test suite froze.
- The first message is the current state: `snapshot` while running, or
  `ready`/`failed` if it already finished, so a client that reconnects fires the
  same handler as one that watched throughout.
- A heartbeat comment every 15s. Separation genuinely sends nothing for a minute
  or more and proxies close idle connections. `X-Accel-Buffering: no` is there
  because a buffering proxy delivers the whole stream at the end, which looks
  exactly like the feature not working.
- `queue.put_nowait` with a bounded queue: a browser tab that stopped reading
  must never block the separation publishing to it.
- Verified against a real 45s song: `snapshot` at 0.4s, heartbeats at 15/30/45/60s
  through the separation, then `progress` → `playable` → `ready`, and the server
  closed the stream. Note `playable` and `ready` arrive together today because
  nothing runs between them yet; D-28's gap opens up when transcription and
  alignment land in phase 2.
- `GET /jobs/{id}` stays as the fallback for a client that cannot use SSE.

From `T-1.9`:

- `apps/web` is Next.js 16 (App Router) scaffolded by hand rather than by
  `create-next-app`: fewer files, and each one deliberate. No Tailwind, no
  ESLint — the dependency surface stays thin, and `tsc --noEmit` covers types.
- **RTL is the default, not a mode.** `dir` comes from the locale in
  `[locale]/layout.tsx`, and `globals.css` is written entirely in logical
  properties (`margin-inline`, `border-inline-start`). A stylesheet in
  left/right needs fixing screen by screen later; one in logical properties
  never needs fixing at all.
- Two dictionaries exist (`he`, `en`) so the structure is a real one. `he` is
  the default and `/` redirects to `/he`. `Dictionary` is typed from the Hebrew
  file, so a key added there and forgotten in English is a type error.
- **`tests/test_translations.py` is the test that makes the `code` field from
  `T-1.2` worth having.** It greps the Python source for every `ApiError`,
  `PipelineError` and `AudioError` code and fails if any lacks Hebrew text. Same
  for every `JobStep` and `JobState`. Add a code, add a translation.
- `scripts/check.py` now also runs the web typecheck and tests, because "one
  command runs every check" was `T-1.1`'s acceptance criterion and two commands
  start diverging immediately. They **skip**, not fail, when `node_modules` is
  absent.
- Dictionary parity is checked with `node --test` — built in, no test framework.
- Windows trap, again: `new URL(..., import.meta.url).pathname` yields
  `/C:/Users/...`. Use `fileURLToPath`.

From `T-1.10`:

- `GET /api/v1/songs` is the library. Chapter 6 describes `GET /library`, which
  is per-user and needs `D-16`; until auth exists this returns every song, which
  is the same thing while there is one user. **The response shape is the one
  `/library` will have**, so the screen does not change when the decision lands.
- The library is the home screen (`/he`), server-rendered with
  `dynamic = "force-dynamic"`. A cached page would show "processing" for a song
  that finished ten minutes ago.
- A row shows the **step**, not the word "processing" — "מפריד ערוצים" tells a
  waiting user something. And a playable song says so *while still processing*,
  which is the whole of `D-28`; a library that only shows `status` throws it away.
- `apps/web/src/lib/api.ts` is the only place that knows the backend exists. A
  network failure is turned into a code the dictionary knows, so the screen
  shows a Hebrew sentence rather than a stack trace — verified by killing the
  API and reloading.
- Durations are wrapped in `direction: ltr; unicode-bidi: isolate`. Without it
  `1:10` renders as `10:1` inside a Hebrew line.
- Row logic lives in `src/lib/song.ts`, not in the component, so it can be
  tested. Node 24 runs `.ts` directly by stripping types, so `node --test` needs
  no build step and no framework.

From `T-1.11`:

- `/[locale]/upload` and `/[locale]/jobs/[jobId]`. These are the first **client**
  components: one holds a file, the other holds an open connection.
- The file goes **straight from the browser to the API**, not through Next.
  Chapter 6 eventually wants a signed URL with no API in the path at all;
  routing 30MB through a Node server first would be a step away from that.
- The progress screen is SSE with **polling as a real fallback**, not decoration:
  `text/event-stream` is the first thing a corporate proxy breaks, and a bar
  that silently stops looks exactly like a job that hung. Verified by replacing
  `window.EventSource` with one that errors immediately — the screen showed
  "מתחבר מחדש…" and switched to polling `GET /jobs/{id}`.
- The first job state is fetched **on the server** so the page arrives with
  something on it, rather than a blank frame before the first SSE message.
- A library row for a song with a job links to its progress screen. Without it
  the library is a dead end exactly while the work is happening.
- Verified in a real browser against the real API and real Demucs: chose a file,
  uploaded, watched "מפריד ערוצים" arrive live, then "אפשר להתחיל לשיר" and
  "השיר מוכן" — with exactly two API requests, the upload and the stream. A
  non-audio file produced "לא הצלחנו לקרוא את הקובץ כאודיו" on the form.

From `T-1.12`:

- `apps/web/public/pitch-worklet.js` is the phase 0 engine **copied unchanged**.
  It was measured at 0 samples of drift across a whole song at every pitch and
  tempo, and under a cent of pitch error; editing it means re-earning those
  numbers. It has to stay a plain `.js` file in `public/` — `addModule()` needs
  a URL and `AudioWorkletGlobalScope` has no bundler.
- `src/lib/player/engine.ts` wraps it. **`position` is never computed in the app** —
  it arrives from the worklet, which derives it from `inputPos`, the one read
  head every stem advances by. Chapter 8 forbids browser timers, and the reason
  is concrete: a timer and an audio clock agree for about a minute, then the
  lyrics slide.
- Audited live in a browser: **1 AudioContext, 1 worklet node with 4 outputs,
  5 gain nodes (four stems + master), and 0 `setInterval` calls** — while the
  clock ran. That is the acceptance criterion measured rather than asserted.
- Four gain nodes hang off the worklet's outputs, so a mute never touches the
  engine and cannot cost sync (T-0.2.2). Volume changes use `setTargetAtTime`
  over ~70ms; a step in gain is an audible click.
- `GET /songs/{id}` and `GET /songs/{id}/stems/{kind}` were added for this.
  Chapter 6 wants signed URLs, which needs `D-12`; these point back at the API
  instead, which is the same contract from the player's side. Stems are served
  `immutable, max-age=31536000` — a stem never changes once written.
- The engine's constructor builds nothing (`load()` does), so its ranges and
  clamping are testable in plain Node without Web Audio.

From `T-1.13`:

- `src/lib/player/mix.ts` holds the mixer's rules, apart from the component, so
  they can be tested. The rule worth having: **pressing "remove vocals" twice
  puts you back exactly where you were** — someone using the vocals at 20% as a
  guide track gets 20% back, not full volume.
- The button and the vocals fader can never contradict each other: dragging the
  fader to zero *is* removing the vocals, and the button says so.
- The button is not a fifth fader. Chapter 8 calls it big, and it is: full
  width, larger than anything else on the screen.
- Verified live with real stems: the toggle produced four `setTargetAtTime`
  calls with `tc = 0.0233` (the ~70ms fade phase 0 measured), vocals went to 0
  with the other three untouched, and 20% survived a remove/restore round trip.
  Toggling **mid-song** left the clock running straight through — the engine is
  never touched, which is what T-0.2.2 said and why sync cannot be lost.

From `T-1.14`:

- Key gets **buttons**, tempo gets a **slider**. A key is thirteen discrete
  choices and people step it a semitone at a time until their voice fits; a
  tempo is continuous and people scrub for it.
- The displayed value is part of the feature, not decoration — the acceptance
  criterion says so. `+3` and `−2` carry an explicit sign, because `2` reads as
  a setting you have to remember rather than a change you made.
- The controls read from the **engine's** state, not from local copies, so the
  number on screen and the audio cannot disagree.
- Verified live mid-song: key stepped to `+3` then `−2` and tempo to 75% then
  150%, with the clock running straight through (`0:01 → 0:02 → 0:03`), and the
  worklet receiving `{type: "pitch"}` / `{type: "tempo"}` messages. Stepping
  past the ends stops at `±6` and disables the button.
- **Node's type-stripping needs exact import specifiers.** `controls.ts` imports
  `./engine.ts` **with the extension**, which is why `allowImportingTsExtensions`
  is on in `tsconfig.json`. Without it `node --test` cannot resolve a relative
  import that has runtime values in it. (Type-only imports are erased, which is
  why `mix.ts` never needed this.)

From `T-1.15`:

- BPM and key are detected in `packages/audio/analyse.py` with **numpy only** —
  tempo from the autocorrelation of a spectral-flux onset envelope, key by
  correlating a chroma vector against the Krumhansl-Kessler profiles. librosa
  would bring scipy and numba, a few hundred MB into an image that has to stay
  deployable, for two functions.
- **Both read the whole mix, not the stems.** The opposite was tried first, on
  the plausible story that drums give cleaner onsets and that excluding vocals
  gives a cleaner chroma. Measured, the mix won every time (song A: `D` at 0.359
  from the mix against `Am` at 0.033 from `other+bass`, which is not even the
  same note set). A bass line is mostly roots and octaves, so mixing it in skews
  the chroma away from the distribution the profiles were calibrated on. The
  numbers are in `packages/core/analysis.py`.
- **Key confidence is measured against the best *non-relative* candidate.** C and
  Am share all seven notes, so comparing against the runner-up reports near-zero
  confidence for a perfectly clear key. Below `MIN_KEY_CONFIDENCE` nothing is
  stored — a singer may transpose against this number, so "we do not know" beats
  a plausible guess.
- Tempo is folded into 85–165 BPM. 75 and 150 describe the same music and one of
  them is what a person taps.
- Analysis runs after the stems are recorded and **cannot fail the job**, on the
  same reasoning chapter 7 gives for transcription. It is not a `JobStep`:
  chapter 7's pipeline does not have one and it takes about two seconds.

From `T-1.16`:

- `user_song_settings`, keyed on **`(user_id, song_id)`** rather than a surrogate
  id. There is exactly one row per person per song by definition, and saying so
  in the key means the upsert cannot create a second one.
- The settings come back **with the song**, so opening one is a single request —
  the player needs them before it builds the graph, or you hear it adjust.
- **Out-of-range values are clamped, not rejected.** The player saves on every
  change, and a save must never fail a user's session: a tempo of 1.5000001 from
  a float slider is stored as 1.5, not turned into a 422 an auto-save cannot
  report. The DB `CHECK` constraints are the backstop.
- Chapter 5's "saved automatically on every change" cannot be literal — one
  fader drag is an event per pixel. `src/lib/player/persist.ts` coalesces to one
  save per ~600ms, and flushes on `visibilitychange`/`pagehide` so closing the
  tab straight after a change does not lose it.
- `vocalsRemoved` is **derived** from a stored volume of 0, never stored, so the
  button and the fader cannot disagree after a hand-edited row.
- Verified in a browser: set key −4, tempo 85%, vocals 20%, drums 60%, reloaded
  the page, and got all four back — with remove/restore returning to 20% and the
  gain nodes at `[0.2, 0.6, 1, 1]`, so the engine holds them and not just the UI.

From `T-1.17`:

- **The automatic fallback is deliberately timid, and this is the honest part.**
  The first implementation benchmarked the real worklet in an
  `OfflineAudioContext` and fell back above 70% of real time — the instrument
  phase 0 used, which recorded 47% for four stems at +6 on an 8-core desktop.
  Re-run here it reported **164% on a machine that was at that moment playing
  four stems at +6 without a glitch.** An offline render is throttled in a
  background tab and competes with everything else at page-load time, so it
  over-reports. Four stems is now the default and the measurement only overrides
  it above 300%.
- **The mode is a user control**, remembered per device in `localStorage`. That
  is what actually carries the requirement until `T-0.2.5` can calibrate the
  threshold on real hardware: an automatic answer nobody can override is a
  mystery when it is wrong, in either direction.
- Two-stem mode keeps **vocals separate** and folds drums/bass/other into one
  backing channel, summed in the browser from the stems already downloaded — no
  fifth object in storage. "Remove vocals" therefore still works, which is the
  point; what is lost is balancing drums against bass, which is not why anyone
  opened the app.
- **The four stem volumes stay canonical in both modes.** The backing fader
  reads their mean and writes all three, so a mix made on a phone still makes
  sense on a laptop. Verified: drums at 40% → light mode showed backing 80% →
  back to four showed drums 40% again.
- Verified at 375×812: no horizontal overflow, every touch target ≥44px, and
  light mode built **1 worklet node with 2 outputs** and 3 gain nodes instead of
  4 and 5 — half the vocoder work, which is the whole point.

From `T-2.1`:

- `lyrics` is a **version** of a song's words, not the words themselves, and
  `PUT /songs/{id}/lyrics` answers `201` because it creates one. Chapter 6 says
  an edit never overwrites; `(song_id, version)` unique is what makes that true
  of the data and not only of the code that writes it. `GET` takes `?version=`,
  and every version is listed in the response so the editor can offer "back to
  what the machine wrote" without a second request.
- **`GET` answers `202` while `lyrics_status` is `pending`.** D-28 opens the
  player before the lyrics exist, so "not yet" is a normal answer to a normal
  request; a `404` would make the client draw a failure for a song that is
  working as designed. Once the pipeline has given up it is `200` with an empty
  list — the editor has to open on something (T-2.10).
- Because of that, `jobs.finish` now flips a still-`pending` song to `missing`.
  The pipeline has no transcription step until `T-2.3`, and a finished song
  stuck on `pending` would promise words that are never coming.
- **`lyrics_status` is derived from the saved lines, never sent by the caller.**
  `word` needs *every* timed line to carry words — a highlight that works for
  one verse and then stops looks broken, where line-level throughout looks
  deliberate. Words with no times are `missing`, not `line`: from the player's
  side, lyrics it cannot scroll are the same as no lyrics.
- **A lyrics save fails loudly where a settings save is clamped.** T-1.16 clamps
  because the player auto-saves and must never break a session; this is a button
  someone pressed, and quietly rewriting what they typed is worse than saying it
  did not go in. Blank lines are the exception — a paste has them between verses,
  so they are dropped, and line indexes are assigned from the list order rather
  than trusted from the client.
- `words_json` is a blob, not a third table: words are only ever read with their
  line and never queried across songs.
- Verified against the running API: `202` while pending, two saves producing
  versions 1 and 2 with the first still readable at `?version=1`, `invalid_lyrics`
  for a line that ends before it starts, the song reporting `lyrics_status: line`,
  and deleting the song leaving 0 rows in both tables.

From `T-2.2`:

- The open database is **LRCLIB** (`packages/providers/lyrics_catalogue.py`): no
  account, no key, so no card. It is asked over `urllib`, not httpx — one GET,
  and the API image has to stay small. `truststore` is imported only if present,
  which is what makes it work on this machine's TLS inspection and cost nothing
  in the Linux container.
- **On by default**, unlike the separation backend. The reasoning that keeps
  `local` separation the default — a stray run spends real credit — does not
  apply to a free read of a public database. `KARUKI_LYRICS_CATALOGUE=none`
  turns it off.
- The lookup runs in the pipeline after the song is playable and is **not a
  `JobStep`**, for the same reasons the T-1.15 analysis is not one: chapter 7
  has no step for it, it is one HTTP call, and it cannot fail the job. A
  catalogue that is down produces `lyrics_status: missing`, not a failed job.
- `POST /songs/{id}/lyrics/search` is the manual re-run, for a song processed
  before its title was fixed. It lands as a new `db` version, so running it
  after somebody has edited by hand cannot destroy their work.
- **Hebrew needed two changes that only a live search would have shown:**
  - LRCLIB stores Hebrew songs under a Hebrew title with a **transliterated**
    artist — `ממעמקים` by `Idan Raichel`. Sending `עידן רייכל` matches nothing.
    Every reading is therefore asked twice, with the artist and without: hit
    rate on ten well-known Hebrew songs went from **1 to 5**.
  - Comparing a Hebrew artist against a Latin one gives about zero, which reads
    as evidence *against* the right song. `comparable_artists` calls that pair
    incomparable, and a match with no comparable artist then has to be backed by
    the measured duration and a near-exact title.
- **Two false positives were found live and closed, both about precision:**
  - A row called `שביר` scored 0.95 against the whole filename `ריטה - שביר`,
    because the title is one of its words — true of every song whose name
    appears anywhere in a filename. Title containment is now one-directional:
    the extra words may be the database's (`(Remastered)`), never ours.
  - `שביר` by `אריק איינשטיין` then still passed at 0.77, a perfect title
    outvoting an artist at 0.33. A *different* artist is not weaker evidence for
    this song, it is evidence for another one, so it is a veto rather than a low
    score. Two candidates that our evidence cannot tell apart return nothing.
- Duration is the strongest signal we have, because ours is measured off the
  normalised audio rather than claimed. ±3s: beyond that it is a different cut
  and every line in the second half sits wrong.
- Verified against the real service: 6 of 10 well-known songs came back timed
  (`עוף גוזל` 38 lines, `ממעמקים` 55, `יו יה` 78, `רכבת לילה לקהיר` 12,
  `מחכים למשיח` 87, `Bohemian Rhapsody` 50), the four misses are genuinely not
  in the database, and `ריטה - שביר` is refused rather than answered with
  somebody else's song. **No test touches the network** — the catalogue is a
  seam so that stays true.
- Hebrew coverage being about half is the reason `T-2.3` exists and is not a
  disappointment: the database is the free path, transcription is the fallback.

From `T-2.3`:

- `packages/providers/transcription.py` is the seam: Groq's hosted
  `whisper-large-v3` (D-27), free tier, no card, 2,000 requests a day against an
  expected 30 a month. `urllib` again, and the multipart body is built by hand —
  the standard library can read multipart and not write it, and the alternative
  was an HTTP client in the image.
- `KARUKI_TRANSCRIPTION_BACKEND` picks `groq` or `none`. No key is
  `TranscriptionUnavailable`, deliberately distinct from a failure the way
  `separation_unavailable` is: one is the operator's problem, the other is the
  recording's. The key comes from `GROQ_API_KEY` in `.env` (gitignored) and is
  passed through to the container by the compose file, never written into it.
- **Verified live on the phase 0 song**: 132.7s of audio transcribed in 19.2s
  (6.9× real time — slower than phase 0's 8–14×, on a smaller sample), 11
  segments, 54 word-level timings, and the quota headers came back reading
  2,000 / 1,999.
- **The hallucination filter earns its threshold from that run.** Phase 0 warned
  that Groq writes `תודה רבה` over an instrumental intro; the re-run reproduced
  it exactly, plus a `תודה.` at 130.4s of a 132.7s recording. But the numbers
  say something the plausible rule gets backwards:

  | | `no_speech_prob` | `avg_logprob` | |
  |---|---|---|---|
  | `תודה רבה.` at 0.0s | 0.69 | −0.21 | hallucinated |
  | `דרושנה דורשיך` at 30.0s | 0.55 | −0.27 | real |
  | `לשמוע אל הרינה` at 86.4s | **0.82** | −0.27 | real |
  | `תודה.` at 130.4s | 0.76 | −0.11 | hallucinated |

  Sung Hebrew scores *higher* on `no_speech_prob` than the hallucination does,
  so dropping on that number alone would have deleted the real lines and kept
  the false ones. Whisper's rule needs **both** numbers, and with both required
  it leaves this transcript untouched — the caption-phrase list is what actually
  catches phase 0's case. The table is in `packages/lyrics/transcript.py` and in
  a test, so a later tidy-up has to argue with a measurement.
- Two identical segments in a row are a chorus; four are the model stuck in a
  loop. That is the whole repetition rule.
- **No test spends a request.** The suite replaces `urlopen`, which also lets it
  assert the request itself — the model, `verbose_json`, and both
  `timestamp_granularities[]`.
- Nothing calls this from the pipeline yet. `T-2.4` is what runs it twice and
  records which transcript won.

Open provider decisions, both deferred to phase 3 and neither blocking:
`D-12` storage (needs an alternative to R2) and `D-15`/`D-16` database and auth.
