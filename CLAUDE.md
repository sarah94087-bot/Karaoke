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

Give the web app its browser-safe settings (after any change to `SUPABASE_*`
in `.env`; Next reads env files from its own directory, not the repo root):

```
.venv\Scripts\python.exe scripts\web_env.py
```

Remove the audio of songs nobody has played for six months (chapter 9). The
dry run is the default:

```
.venv\Scripts\python.exe scriptseap.py
.venv\Scripts\python.exe scriptseap.py --apply
```

Apply the bucket's CORS rule (needed once per bucket, and again when a
deployment adds its own origin):

```
.venv\Scripts\python.exe scriptsucket_cors.py --apply
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
- **`localhost` and `127.0.0.1` are not interchangeable here, in both
  directions.**
  - *Server side*: Node resolves `localhost` to `::1` first and
    `python -m apps.api` binds IPv4 only (uvicorn's default), so a
    server-rendered page hangs until it times out and the screen says "the
    service is unavailable" while `curl` on the same machine answers instantly.
    The compose container publishes on both stacks, which is why this only bites
    the venv setup — the one used to develop. Both API-base defaults now say
    `127.0.0.1`.
  - *Browser side*: Next 16 refuses to serve dev chunks to an unrecognised
    origin, so opening the app at `http://127.0.0.1:3000` renders the HTML and
    then **never hydrates** — the words are on the screen and nothing is
    clickable, with the reason only in the dev server's log.
    `allowedDevOrigins: ["127.0.0.1"]` in `next.config.ts` settles it; `npm run
    dev` still prints `localhost:3000` and that address always worked.
- A Hebrew filename uploaded **from the browser** stores a Hebrew title; a
  filename sent by `curl` from this cp1255 console arrives as mojibake
  (`ãøåù ðà`) because the bytes are cp1255 and the server reads them as latin-1.
  Worth knowing before filing a bug about it: it is the test command, not the
  app.
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

Phase 2 closed: `T-2.1` through `T-2.10` done — the words, from the open
database or from transcription, aligned, running in the player, and editable by
hand in both text and time.

`T-5.2` (the A–B loop) was done out of order: it depends only on the player,
and the rest of phase 3 was waiting on account signups.

Phase 5 (polish) is open: `T-5.1` (full screen and the playback queue) and
`T-5.2` are done; `T-5.3`, the PWA, is what is left.

Phase 3 (cloud and multiple users) is closed except for one checklist item:
`T-3.1` to `T-3.13` are done —
object storage with expiring links, uploads that go straight to the bucket,
separation on a rented GPU that reads and writes the bucket itself, every job
carrying the handle on its remote call and what it spent, staged readiness
measured end to end in the cloud configuration, accounts, a library that is one
person's, chapter 9's limits with the screen that shows them, the audio of
songs nobody sings any more removed on a schedule, the whole thing deployed and
taken through one song end to end from its public address, a keep-alive that
was measured rather than assumed, errors that report themselves, and chapter
14's checklist with the evidence for each item in
`docs/phase3/deploy-checklist.md`. **Eleven of its twelve are done** - the
smoke test passed in full on 2026-08-25, eight checks with nothing skipped -
and the twelfth is the phone (`T-0.2.5`), still waiting on hardware.

Phase 4 (import) is closed: `T-4.1` and `T-4.2` are done — a song can be added
from a link, behind `KARUKI_IMPORT`, which removes the route and the form when
it is off; and the title, the artist and the length fill themselves from the
file's tags, the importer and the open lyrics database, with a person's
correction beating all three.

**Docker Desktop crashes on a stale socket after an unclean shutdown**, and the
dialog offers "Reset to factory defaults" right next to "Quit". Do not take it:
it deletes every volume, including `db_data`. The fix is to quit and remove the
leftover socket files, which live in more than one place - it crashed twice on
2026-08-23, first on `%LOCALAPPDATA%\Docker\run\sailor-ingest.sock` and then on
`%LOCALAPPDATA%\docker-secrets-engine\engine.sock`:

```
Get-ChildItem "$env:LOCALAPPDATA\Docker", "$env:LOCALAPPDATA\docker-secrets-engine" -Recurse -Filter *.sock -ErrorAction SilentlyContinue | Remove-Item -Force
```

A Windows restart clears them all at once if it keeps finding new ones. The
containers and the volumes survive this untouched; only the daemon is broken.

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

From `T-2.4`:

- **D-29 is implemented as phase 0 restated it, not as chapter 7 wrote it.** The
  spec says "transcribe both and keep the better one"; `T-0.4.2` measured that
  competition and found there is none — the vocals stem won 3 of 3 and the mix
  returned **39% of the words**. So the mix is a *stand-in shown early*, the
  vocals run replaces it whenever it produces anything, and there is no scoring
  function to go wrong. `packages/core/transcribe.py` opens with that reasoning.
- The one comparison left is a sanity check with a different question behind it:
  a vocals run that returns **nothing** keeps the stand-in, because deleting
  words we have for words we do not is not an improvement.
- The mix run **starts before the separation** and runs beside it in a thread,
  which is D-29's actual argument — time, not quality. It never touches the
  session: the task returns a value and every write happens on the main flow.
- The pipeline now asks the open database **first** (T-2.2 ran it after
  separation), because its answer decides whether to transcribe at all. A song
  LRCLIB knows costs zero requests.
- `JobStep` was reordered so both transcription steps come **after** `ENCODING`,
  and `STEP_PROGRESS` with it (10 / 50 / 78 / 84 / 92 / 100). The steps are in
  the order the job *reports* them, not the order it starts them:
  `transcribing_mix` is only named when the job is genuinely waiting on it, and
  a bar that goes backwards reads as a bug even when nothing is wrong.
- **The language is detected on the mix and then handed to the vocals run.**
  This is the one that a live run caught and no unit test would have: on a real
  Hebrew song the isolated vocals stem was detected as **English** and came back
  transliterated — `Me'onecha, deros na'ador she'cha` — and replaced a perfectly
  good Hebrew stand-in. The mix, which still has the instruments in it, gets the
  language right. Passing the hint fixed it; a vocals run that *still* comes
  back in a different language than the mix is treated as a run that went wrong,
  and the stand-in stays.
- Nothing is forced to Hebrew, though. A Hebrew speaker's library has English
  songs in it, and telling the model they are Hebrew produces Hebrew-shaped
  nonsense — which is the same failure in the other direction.
- Verified end to end against the real service, on a 45s excerpt of a Hebrew
  song with `KARUKI_LYRICS_CATALOGUE=none` to force the transcription path:
  progress ran `separating 50` → `transcribing_vocals 92` → `ready` (the mix run
  finished during the separation, so its step was never reported, which is the
  design), and the song ended with two versions — `mix_asr` at 13 lines and
  `vocals_asr` at 7 — both Hebrew, `lyrics_status: word`.
- **No test spends a request**, the same rule the GPU has.

From `T-2.5`:

- **No timestamp is ever moved.** `T-0.5.3` tapped a song by hand and compared:
  raw Whisper missed by 242ms at the median, the energetic aligner built for the
  job by 372ms and never once landed inside 100ms. Re-anchoring lines to onsets
  would make the timing worse, so `packages/lyrics/align.py` only ever *splits*.
- **A segment is not a line, and the split comes from the audio.** `T-0.5.1`
  found segments running 14.86s→26.46s across four sung phrases; the transcripts
  measured here have segments of 14.0s, 18.1s and 18.9s.
  `packages/audio/silence.py` decodes the **vocals stem** and finds the gaps —
  the one job `T-0.5.3` left the detector after rejecting it for timing.
- Word gaps could not do that job, and measuring said so: Groq returns word
  timings that are **contiguous** (p50, p90 and p95 of the gaps between words
  are all 0ms), so there is nothing to split on inside a segment. That is why
  the audio is opened again at all.
- Length is the backstop where the audio has no gap: a continuously sung vocal
  genuinely has very little silence in it (7–19 gaps ≥350ms per song). A line
  over 8s or 10 words is split at a word boundary — the *break point* is a
  guess, the *times* stay measured.
- **A line whose end would be absurd has no end at all.** One measured case is a
  single word the model gave a 15.1s duration. `end_ms` was made nullable in
  T-2.1 precisely so "we do not know" can be said; the player shows the line
  until the next one starts.
- **Word-level highlighting is opt-in per line, and phase 0's own threshold
  decides.** `T-0.5.2` found word timings relatively right and absolutely wrong
  (first word off by 215–395ms, varying line to line) and warned that a
  word-level highlight as a default "will look broken". So words survive only
  when `exp(avg_logprob) ≥ 0.5` — the same usable-word threshold `T-0.4.2` was
  built on — and the words are in order and cover ≥60% of their line. Everything
  else keeps its text and its line-level timing, which is D-09 exactly.
- The silence floor is anchored to the **loud** end (8% of the 90th percentile
  of frame energy). Anchoring it to the quiet end was tried first and is subtly
  broken: a percentile of the quiet frames moves with how much silence the song
  contains, so a long instrumental raises the floor until the singing falls
  under it — measured as "no gaps found at all" on a song with obvious ones.
- Aligning is `JobStep.ALIGNING`, reported for real: it is the one step that
  opens the audio again.
- Measured on three real songs: 9→14, 31→32 and 36→38 lines, with line lengths
  p50 3.1–4.8s and p90 4.6–7.3s, and word timings kept on 25/32 to 36/38 lines.
- Verified end to end on the 45s excerpt: `separating 50` → `encoding 78` →
  `transcribing_vocals 92` → `ready`, 10 lines at p50 3.3s and max 5.7s, 8 of
  them with word timings, and the song reporting `lyrics_status: line` because
  two lines did not qualify — which is the derived-status rule from T-2.1 doing
  its job.

From `T-2.6`:

- **The clock is still the engine's, and rAF is the paint rather than the
  clock.** The worklet reports every ~116ms, which is longer than the whole
  100ms budget chapter 8 gives the lyrics, so `engine.positionNow()` carries the
  last report forward using `AudioContext.currentTime` — the same audio clock,
  read from the other end — scaled by tempo, because the read head advances at
  the playback rate. `requestAnimationFrame` asks "where are we" once a frame
  and never counts time. React re-renders only when the highlighted line or
  word actually changes, which is a few dozen times a song rather than 60/s.
- `src/lib/lyrics.ts` holds the rules (which line, which word, the offset) apart
  from the component, because the acceptance criterion is a number and a rule
  with a number in it belongs where it can be tested in a millisecond.
- A line **holds for 1.2s after its own end** unless the next one starts first.
  Without that, every gap between phrases blanks the area and the screen
  flickers through the whole song. A line with `end_ms: null` (T-2.5's "we do
  not know") is shown until the next line starts, which is what that null means.
- The lyrics are **fetched on the server with the song** and re-fetched by the
  player while the pipeline is still working: a 202 is a normal answer (D-28),
  and T-2.4 replaces the stand-in transcript mid-song, so the words improve
  under the singer. That is chapter 8's "lyrics on the way", working as written.
- The words are painted **before the engine is ready** — decoding four stems
  takes a moment and the lyrics are the one thing on that screen readable
  without a clock. The same reasoning as D-28, one level down.
- **Measured in a real browser**, which is the only place this claim means
  anything. Played a 45s song from zero and sampled the DOM every frame for the
  whole run, then matched all twelve line changes against the stored times. The
  error of each change relative to the first one:

      0  −23  +13  −4  +16  −2  +8  +38  +37  +37  +73  +129 ms

  **Eleven of twelve inside 100ms**, and that includes the sampler's own frame
  of latency. The last line, 44s in, is +129ms; the error grows slowly across
  the song, which is worth remembering when `T-2.9` starts moving line times by
  hand. Note this measures the *display against the stored times* - how well
  those times match the singing is phase 0's 242ms median, and the reason
  T-2.7 and T-2.9 exist.

From `T-2.7`:

- **Positive means later.** The stored `lyric_offset_ms` is *added* to every
  line and word time, the same direction a subtitle delay reads in. The buttons
  say what they do (`המילים מוקדם יותר` / `מאוחר יותר`) so the sign never has to
  be guessed from a number.
- Buttons and not a slider, for T-1.14's reason about the key: this is stepped
  100ms at a time while watching one line go past, which is not scrubbing. The
  step is 100ms because that is the unit the whole feature is judged in.
- Range ±3s in the player, ±30s in the API. The wide one is a backstop against
  a unit mix-up (seconds sent where milliseconds were meant), not a second
  opinion about what a user should be allowed to do; it is clamped rather than
  rejected because an auto-save must never fail a session (T-1.16).
- **`toSettings` used to hardcode `lyric_offset_ms: 0`.** Harmless while nothing
  could set one — and a bug the moment this control existed, because every fader
  drag would have quietly reset the timing somebody had just adjusted. It is a
  parameter now and every save path passes it.
- Phase 0 is why this control exists *and* why it is not enough: the systematic
  bias per song measured +180ms, +540ms and −180ms (so no global constant), but
  the spread *within* one song reached a p90 of 1.7s, which no single number can
  fix. `T-2.9` is where that gets fixed line by line.
- `persist.ts` now imports a runtime value from `lyrics.ts`, which means the
  specifier had to become `../lyrics.ts` — `node --test` strips types but does
  not resolve `@/` paths. Same trap as T-1.14's `controls.ts`; type-only imports
  are erased and can keep the alias.
- Verified live: `500` saved and read back, `400000` clamped to `30000`, `−400`
  arriving in the page payload. **And in a real browser**: three presses of
  `המילים מאוחר יותר` showed `+0.3 שנ׳`, the API had `lyric_offset_ms: 300` a
  second later, and the value was still there after a reload.

From `T-2.8`:

- **Every line on the screen, always editable.** `T-0.4.3` timed a real edit and
  found **64 words corrected against 32 flagged as low confidence** — twice as
  many — so an editor built around "jump to the marked words" would miss half
  the work. The same measurement recorded 5.9 minutes of active editing for a
  2:46 song, which is why there is no per-line dialog and no mode to enter.
- **A line's timing survives an edit of its text; its word timings do not.** The
  words were timed one by one, and after an edit they are timings for words that
  are no longer there — a highlight that lights the wrong syllable and then runs
  out. The screen says so on the line being edited, *before* the save.
- One input per line rather than one textarea over the song. A textarea makes
  the mapping between text and times a matter of counting newlines, and one
  stray newline shifts every timing by one line. Pasting a whole set of words is
  a different job — `T-2.10`'s.
- **Emptying a line deletes it**, because T-2.1 already drops blank lines and
  re-indexes the rest. A line the model heard and nobody sang should go away by
  being cleared.
- `?version=` opens an older set, which is what makes "back to what the machine
  wrote" real: read it, save it, and the correction you abandoned is still there
  one version behind. Nothing in this screen can destroy anything.
- Verified in a real browser end to end: ten lines with timecodes, editing one
  showed `שורות ששונו: 1` and the word-highlight warning, saving produced
  **version 3, `manual`**, with that line's words dropped and **its start time
  unchanged**, while version 2 still reads back with the original text and its
  two word timings. `?version=1` opens the 13-line mix transcript.

From `T-2.9`:

- **The shape comes from a phase 0 failure, not from a guess.** `T-0.5.3` tried
  tapping along in real time and it went wrong the same way twice, on two songs:
  reading the words and pressing in time at once is a double task, and it slid
  the whole take by a line. Its recommendation was a rough pass and then a
  *correction pass* — one line looped and nudged. So the editor plays a line
  **with a 1.5s lead-in** (a line that starts the instant you press play gives
  you nothing to compare it against), loops it, and offers "תפוס זמן" or 100ms
  steps. Nothing asks anyone to be accurate while the song runs past.
- **A shift takes the line's words with it.** `T-0.5.2` measured word timings as
  relatively right and absolutely wrong, so moving the whole line by one delta
  is exactly the correction that keeps the half that was measured. Verified
  live: line start 400→600ms and both its words 400→600 and 1620→1820.
- **Starts stay in ascending order**, clamped to the neighbours. The player
  finds the current line by binary search (T-2.6), so a line that jumped behind
  its predecessor would not be early, it would be invisible.
- The editor builds **its own `PlayerEngine`**. It is the same class the player
  uses, and the loop watches the audio clock rather than a timer — chapter 8's
  rule applies on this screen too.
- **A bug the browser found and no unit test would have.** The nudge buttons
  read `lines` from the closure, so two presses inside one render both computed
  from the same array and the second was lost — exactly what tapping a nudge
  button quickly does. Every `setLines` in that file now takes the functional
  form.
- Verified live: play-from-line advances the clock from the lead-in, "catch the
  time" lands the line where the song is (and visibly stops at the next line's
  start, which is the clamp), three quick nudges move 0.3s, and saving writes a
  new `manual` version with the other lines untouched. **The loop's wrap was not
  observed**: `requestAnimationFrame` is throttled while the window is in the
  background, which is where the browser was, so the loop rests on `loopEnd`'s
  unit tests and on nothing else.
- Practical note for any live check: **`scripts/check.py` empties the songs
  table** (the database tests do), so upload the song you are looking at *after*
  the last run of it, not before.

From `T-2.10`:

- D-08's three sources of words are the open database, the transcription, and
  the editor — and this is the editor's own. Someone who already has the lyrics
  should not have to wait for a model to guess them and then correct the guess.
- **Pasted lines arrive untimed on purpose.** T-2.1 stores `start_ms: null`
  happily and reports the song as `missing` until times exist, which is exactly
  what is true: the words are right and the timing has not been done. Timing
  them is then the same job T-2.9 already does.
- **One button does the rough pass**: "תפוס זמן לשורה הבאה" always means the
  next line without a time, and it wraps round for the ones that were missed.
  Part of `T-0.5.3`'s tapping failure was hunting for the right control while
  reading and listening at once; one button in one place removes the hunting,
  and the correction pass fixes what the tapping got wrong.
- Blank lines in a paste are dropped rather than kept — a paste from a lyrics
  site is full of them and nobody sings an empty line — which is the same rule
  T-2.1 applies on the way in.
- Verified in a browser: three pasted lines came in untimed with the blank one
  gone, three presses of the rough-pass button gave 1.469s / 3.111s / 4.748s,
  and the save wrote version 3 as `manual` with the song reporting
  `lyrics_status: line` and both transcript versions still readable behind it.
  A song with an empty newest version opens on "הדביקו את המילים שלכם", which is
  the state this task exists for.

From `T-3.1`:

- **Reading is through a link that expires, on both backends.** `GET /songs/{id}`
  now hands out signed URLs, which is what chapter 6 says it does, and
  `GET /songs/{id}/stems/{kind}` — T-1.12's unsigned stand-in — is **gone**. The
  path alone is no longer authority; the link is, for `KARUKI_SIGNED_URL_TTL`
  seconds (1h).
- **`D-12` is Backblaze B2**, spoken to over the S3 API. R2 was rejected in
  `T-0.6` for putting its free tier behind a payment method. Nothing in
  `packages/providers/storage_s3.py` is B2-specific — path-style SigV4 — so
  Storj is five environment variables away if the account turns out to want a
  card after all.
- **No boto3.** botocore is tens of megabytes of an image that has to stay
  deployable on a free tier, for four verbs. The signing is `hmac` and
  `hashlib`, the requests are `urllib`, the same call this project already made
  for the hand-built multipart body in `transcription.py`.
- **A presigned GET is computed, not requested.** Opening a song hands out four
  links and costs zero round trips to B2, which is what makes signing on every
  song open affordable.
- **The local backend signs the same promise itself** — an HMAC over the key and
  the expiry, checked in `apps/api/routers/files.py` — so chapter 11's "runs
  locally" stays true without the local path being the weaker one. The expiry is
  *inside* the signed message: editing `expires=` in the address bar invalidates
  the link rather than extending it, and that is asserted on both backends.
- An unset `KARUKI_SIGNING_SECRET` is a **random secret per process**, not an
  absent one. Links stop working after a restart, which a player recovers from
  by re-reading the song; the alternative is a signature anybody can compute.
- Cache-Control on a signed file is `private, max-age=<ttl>` and no longer
  `immutable, max-age=31536000`. The object never changes, but a response cached
  past the expiry would be served by a link that no longer works.
- Verified live on the **local** backend, API and browser: the four stems came
  back as `/api/v1/files/...?expires=&sig=`, the player fetched all four through
  them (`200`, four requests, no console errors) and ran the clock to 0:06 of
  0:20; the path with no signature is `422`, a signature that does not match is
  `403 link_invalid`, an edited expiry is `403 link_invalid`, and the old
  unsigned stem route is `404`.
- Verified live **against the real bucket** (`karuki-songs-sarah`,
  `s3.eu-central-003.backblazeb2.com`): `put` 41B in 3.2s, `list`, a presigned
  GET returning the same bytes in 0.8s, `delete_prefix`, and then a whole song
  through the pipeline — separation writing four distinct stems to B2 and
  `GET /songs/{id}` handing out four presigned links that fetch them. **B2
  accepts the hand-rolled SigV4**, which is the thing no unit test could say.
  The same three refusals hold there and are stronger than locally: unsigned
  `401`, edited `X-Amz-Expires` `403`, expired link `401`.
- **The live run found a bug the fakes did not.** urllib's default `Content-Type`
  for a request with a body is `application/x-www-form-urlencoded`, and B2 kept
  it: four stems sat in the bucket declared as form data. An object store serves
  back whatever it was told at upload time, so this is written into the bucket
  rather than fixed on the way out. `put` now names the type from the key
  (`content_type` in `storage.py`, shared with the local backend so a stem is
  `audio/mpeg` either way), and a test asserts it.
- **The browser cannot read from B2 yet, and that is `T-3.2`.** With
  `KARUKI_STORAGE_BACKEND=s3` the player shows "לא הצלחנו לטעון את הערוצים"
  because the bucket has no CORS rule — the presigned URL is fine, the fetch is
  cross-origin and B2 sends no `Access-Control-Allow-Origin`. `T-3.2` is the
  task that adds the rule (it is on its line already, for uploads). Reading
  needs it too. `local` is still the default backend, and it has no such
  problem.

`D-12` is **closed: Backblaze B2**, account created 2026-08-22, **no payment
method asked for at any point** — verified in the account the way `T-0.6`
insists on, and the opposite of what R2 did. Bucket `karuki-songs-sarah`,
region `eu-central-003` (Amsterdam, the closest of the four to Israel, and the
one that matters because after `T-3.2` the audio moves between the browser and
the bucket without the API in the path). Private, SSE-B2 on, object lock off,
and lifecycle set to **keep only the last version** — a re-run of separation
rewrites the same key, and "keep all versions" would have doubled the stored
bytes of every reprocessed song against a 10GB quota.

From `T-3.2`:

- **The file goes browser → bucket. The API is not in the path.** Chapter 6's
  three steps: `POST /songs/upload-url` hands out a ticket, the browser `PUT`s
  the bytes to it, `POST /songs` is given the key. What T-1.5 did — 30MB through
  the API — stays as `POST /songs/upload`, because it is what the test suite
  and `curl` use and it is the fallback when a bucket cannot be reached
  directly. The screens no longer use it.
- **The ticket is deliberately narrow: one key, one method, one hour.** The
  client chooses none of it. The key is always `uploads/<uuid>/original<suffix>`
  and `POST /songs` accepts no other shape, so a ticket cannot be talked into
  writing over a stem, and `POST /songs` cannot be talked into reading one.
- **The method is inside the signature** on both backends. Without that, every
  stem URL the player is handed would also be permission to overwrite that stem.
- Size is checked **twice**, because the first check is on a claim: the declared
  size refuses the ticket before the upload, and the object that actually
  arrived is measured before it is ingested (and deleted if it is over). The
  local `PUT` route additionally refuses as the bytes arrive, the same rule
  T-1.5 has.
- The staging object is deleted once the song exists — in a `finally`, so a file
  that failed to ingest does not sit in the bucket either. Left behind, every
  upload would cost twice the storage of the song it produced.
- **`POST /songs` still reads the bytes once**, because normalising is ffmpeg's
  job and ffmpeg needs a file. With the object store that is a download of what
  the browser has just uploaded. Chapter 3's "the API never handles audio" is
  about the *transfer*, and that part is now true.
- The upload screen shows a **real percentage**, which needs `XMLHttpRequest`:
  `fetch` still cannot report progress while a body is being sent, and a 30MB
  upload with no progress is indistinguishable from a hung one.
- **The bucket needed a CORS rule, and it is code rather than a console click.**
  `scripts/bucket_cors.py --apply` writes it from `KARUKI_CORS_ORIGINS` — the
  same list the API allows for itself, so the two cannot drift. Re-run it when
  `T-3.10` adds the production origin.
- Verified live, twice. On the **local** backend in a browser: `upload-url` →
  `PUT /api/v1/files/... 201` → `POST /songs 201` → the progress screen showing
  "מפריד ערוצים". On the **s3** backend, against the real bucket: a browser
  `PUT` straight to `s3.eu-central-003.backblazeb2.com` answering **200** with
  the API never in the path, a song built from it and processed to `ready`, and
  the player then fetching all four stems **from B2** (0.72–0.88s each) — which
  is what the CORS rule fixed and what did not work at the end of `T-3.1`.
  Playback itself was not re-observed on this run: the browser pane was not
  displayed, so the audio context never got the user gesture it needs. The same
  code path played on the local backend in `T-3.1`.

From `T-3.3`:

- **The separator is given storage and keys, not files.** Both backends end with
  the four stems *in storage*: the local one through the disk it already has,
  the remote one through signed links it opens itself. Nothing above
  `packages/core/stems.py` knows which happened, which is the rule that file has
  had since T-1.6.
- **The GPU is handed one link to read and four to write, and nothing else.** No
  audio in the call, no storage credential on the rented container - the same
  authorisation the browser upload uses, for one key, for an hour. Phase 0 sent
  23MB in the call and got 15MB back through the API; that path is gone.
- A root-relative link is refused **before** the call. Local storage hands those
  out unless `KARUKI_PUBLIC_BASE_URL` is set, and "this API" means nothing from
  a rented container. Better a clear message than a timeout inside a paid call.
- **Measured on a real 4:30 song, on the GPU, end to end**: `fetch 2.95s →
  model 4.54 → separate 14.72 → encode 10.4 → upload 9.97`, **42.6s billed**,
  49.0s round trip, and the player then opened it from B2. The two transfers are
  now inside the billed window - 12.9s of the 42.6 - which is the price of
  keeping them out of the API. `docs/phase0/quotas.md` carries the new
  utilisation figure.
- **Two failures on the first real runs, both fixed, both worth keeping in
  mind.**
  - B2 answered one `PUT` with a **500** and the whole paid run died with it.
    The S3 API documents that as retryable, so both sides now retry a transfer
    with backoff. This is *not* the retry chapter 7 forbids: that one is a
    second GPU call at double credit, this one is a few seconds that saves the
    credit already spent.
  - The first four-minute song broke ingestion: `put` read the whole 47MB
    normalised wav into memory and the 30s timeout expired mid-upload, which
    surfaced as a 500 from our own API. Uploads now stream from the open file
    (the hash is computed in a separate chunked pass, because SigV4 signs it)
    and the timeout is 300s.
- `logging.basicConfig` is set in `apps/api/main.py`. uvicorn configures its own
  loggers and leaves the root at WARNING, so every `log.info` in this project -
  including the line that reports what a GPU run cost - was going nowhere.

From `T-3.4`:

- **`spawn` then wait, rather than one blocking call.** Spawning hands back a
  call id immediately, and the id is written and committed *before* the work is
  waited on. A job whose process dies mid-call is exactly the one that needs the
  handle on the call still running out there; an id recorded when the call
  returns is one you have only when you no longer need it. D-25 in miniature —
  the platform is the queue and this is its ticket.
- The id travels out of the worker thread through a callback, and the write is
  handed back to the event loop with `run_coroutine_threadsafe`. That is safe
  for the one reason worth stating: the main flow is parked in that `to_thread`,
  so nothing else is touching the session.
- **A failed run records the seconds it burned.** `SeparationError` carries
  `gpu_seconds` and the pipeline commits them before re-raising. A total that
  only adds up the successes is the one that runs out without warning — and the
  failure this was written for, T-3.3's B2 `500`, had already spent 16 seconds
  when it died.
- `packages/core/usage.py` sums the calendar month (UTC, because that is how the
  credit resets) and prices it at the workspace's own T4 rate. Every separation
  now logs `Ns gpu, Ns this month (~$X)`, so the credit is visible without
  anyone remembering to look. `T-3.8`'s quota screen is the same query.
- `GET /jobs/{id}` carries `remote_call_id`, so a job can be traced to the run
  that did the work without opening the database.
- **Verified live.** A real GPU run showed the id in the job **while it was
  still separating** (`fc-01M0PX…` at 12:03:39, the run finished at 12:04:10),
  `gpu_seconds` 14.21 at the end, and the log line reporting the month. The
  stored id is a genuine handle: `modal.FunctionCall.from_id(…).get()` fetched
  that run's result back afterwards. A deliberate failure — a source link the
  GPU cannot read — came back as `GET the source audio: 401` with no stems and
  **0.38s billed**, which is the number the pipeline now keeps.

From `T-3.5`:

- **D-28 measured in the cloud configuration, which is the point of the task**:
  on a real 4:30 song, `playable` at **19.3s** and `ready` at **112.4s** — 93
  seconds of singing before the words arrive. The staged part of chapter 7 was
  built in phase 1 and phase 2; what T-3.5 did was check it still holds when the
  work happens on a rented GPU and the audio lives in a bucket, and it found two
  things that it did not.
- **The analysis ran before the playable mark**, behind a comment claiming it
  did not delay it. On disk that was nearly true — two seconds of numpy. With
  the object store it opens the normalised audio, which for four minutes is a
  47MB download, and the user waited through it with four finished stems already
  in the bucket. Nothing failed; the singing just started later for no reason.
  Playable is now marked and announced first, and the analysis follows.
- **Then the same fact one level down: the event loop was doing the downloading.**
  The mix transcription fetched its audio inline before handing the HTTP call to
  a thread, so `storage.local_path` — free on disk, a 47MB download on B2 — ran
  on the loop. Measured: the SSE stream sent its first message **39.5 seconds**
  after the job started, and for that whole time nothing else in the process
  could answer, including `/system/health`. Chapter 9 budgets one instance, so
  that is the whole service, and T-1.7 had already written the rule down for
  separation. It now covers storage: every read happens in a worker thread.
  After the fix the same run sends its snapshot at **0.0s**, and 38 keep-alive
  pings during the job answered in **0.05s at worst, none failed**.
  `tests/test_pipeline.py` asserts no `local_path` call happens on the loop
  thread, and that test was confirmed to fail when the analysis is moved back.
- **The player has to be told, not just unblocked.** Key and tempo were rendered
  by the server component, so a user who opened at the playable moment would
  never see them: they are measured after that page is built. `SongFacts.tsx`
  fetches them itself, at the same slow cadence the lyrics use, and keeps **only
  those two fields** — a refetch also brings freshly signed stem URLs, and
  handing those to the player would rebuild the audio graph and stop the music
  mid-song.
- Worth knowing, not fixed: the normalised wav is downloaded **twice** in the
  cloud configuration — once by the mix transcription and once by the analysis,
  because the per-process cache only fills when the first one finishes. Both are
  after `playable`, so nobody waits on them; a per-key lock in `S3Storage` is the
  fix if the bandwidth ever matters.

From `T-5.2` (done out of order - it depends only on the player, and phase 3
was blocked on account signups):

- **A-B loop: two buttons, not a drag on the timeline.** The marks are made
  while listening, one at a time, with attention on the music - the playhead is
  already where the ear is. Same reasoning T-1.14 gave for the key buttons.
- The rules are in `src/lib/player/loop.ts`, apart from the component, because
  every one of them has an edge: marking the end first means "from the top",
  marks given backwards are put in order rather than refused, moving the start
  forward **drops** an end left behind it (the alternative loops backwards over
  music the singer has just left), and a loop shorter than a second is pushed to
  a second - below that it is a buzz, and it is where the vocoder artefacts
  live.
- **A wrap is a *crossing*, not "past the end".** Someone who drags the scrubber
  beyond the section has left it on purpose, and a loop that pulled them back
  would be fighting them.
- The loop is **not saved with the settings**: a practice section belongs to the
  half hour spent on one line, not to the song for ever.
- **The browser check found the thing no unit test would have.** In a hidden
  tab `requestAnimationFrame` freezes completely - measured at **zero frames in
  two seconds** with the audio still playing. For the lyrics highlight that is
  harmless, nobody is reading them; for a loop it changes what the user *hears*,
  because the section quietly stops repeating. The watcher now runs on rAF
  **and** a 200ms interval, whichever gets there first, with the position always
  coming from `positionNow()` so chapter 8's rule about the clock is untouched.
- Verified in a real browser on a real song: marked 0:21-0:25 and the clock ran
  `0:22 0:23 0:24 0:25 0:22 …` for three full cycles. **rAF fired 21 times in
  17 seconds** during that measurement - about 1fps, because the window was not
  in front - so what was observed wrapping is the interval fallback doing
  exactly the job it was added for.
- Honest limit, written in the code: browsers clamp timers in a hidden tab to
  roughly a second, so a loop nobody is looking at can overshoot by that much.
  Sample-accurate wrapping belongs in the worklet, and T-1.12 is explicit that
  editing that file means re-earning phase 0's drift measurements.
- Chrome will **freeze a hidden tab that is not playing audio** outright -
  clicks queue and JavaScript does not run, which looks exactly like a broken
  page. A tab playing audio is exempt. Worth knowing before debugging a live
  check that has gone quiet.

From `T-3.6`:

- **`D-15` and `D-16` are both closed, by one signup: Supabase.** Free tier, no
  card asked for at any point (verified the way `T-0.6` insists on). Project
  `karuki`, region **Central EU (Frankfurt)**, `eu-central-1`.
- The hosted Postgres is **17.6**, which is what `T-1.3` pinned the local
  container to `17-alpine` for. That guess, made five days earlier, was right,
  and a mismatch found at deploy time is the expensive kind.
- Migrations were run against it: three of them, up clean, **and down to zero
  tables and back up again on the hosted database itself**. `pgcrypto` is
  present, so `gen_random_uuid()` works server-side as `T-1.4` assumes.
- **Connect with the Session pooler string, not the direct one.** The direct
  connection is IPv6-only and most free hosts dial out over IPv4; it would have
  failed at deploy time rather than here.
- **`SUPABASE_DATABASE_URL`, not `DATABASE_URL`.** The local Postgres owns that
  name, and the test suite drops every table - pointing it at the cloud by
  accident is a bad afternoon. The switch happens in `T-3.10`.
- **No `@supabase/supabase-js`.** `apps/web/src/lib/auth.ts` is four POSTs and a
  refresh; the same reasoning that kept boto3 and httpx out of the API. Adding
  the library later changes nothing above `signIn`/`signOut`/`currentSession`.
- **The session lives in a cookie, not localStorage**, because the library and
  song pages are server-rendered and the *server* needs the token to ask the API
  for this user's songs - which is `T-3.7`. It is not `httpOnly`, since the
  browser writes it; the honest fix is a route handler doing the exchange
  server-side, and `D-31` is when that earns its complexity.
- **The API does not check tokens yet.** T-3.6 is the account surface; the
  binding of songs to their owner is T-3.7, which is why nothing here gates the
  library. Hiding it behind a sign-in that does not yet protect it would be
  theatre.
- **Verified live against the real project, all four**: a wrong password gives
  `bad_credentials` and its Hebrew sentence with no cookie written; a real sign
  in put `sarah94087@gmail.com` in the account bar with 58 minutes on the token
  and nothing in the address bar; sign out cleared the cookie, returned the bar
  to "כניסה" and landed on `/he/signin`; and a reset ran end to end - link
  requested, link followed, new password saved, "הסיסמה הוחלפה".
- **Supabase does not revoke other sessions when a password changes.** Assumed
  it did, checked, and it did not: a token from before the change still answered
  `200` at `/auth/v1/user` afterwards. `POST /auth/v1/logout?scope=global` is
  what actually ends them. Worth knowing before relying on a password change to
  shut a door.
- **The live check found three things the tests could not, all in the same
  seam** - what an emailed link does when it comes back.
  1. Confirming an address does not just mark it confirmed: Supabase redirects
     with a **live session in the URL fragment**. Nothing read it, so the link
     landed on the library *still signed out*, with an access token sitting in
     the address bar and in history. `AccountBar` now adopts that session before
     it even looks at the cookie, and rewrites the URL.
  2. Then the fix made a worse bug: a **recovery** link carries the same shape
     of session, so a password reset became a silent sign-in - the person was
     taken to the library and their password was never changed. It looked
     exactly like success. The fragment says `type=recovery`, and the app now
     routes on that. Deliberately **not** by fixing Supabase's redirect
     allow-list: `redirect_to` is honoured only if the address is configured
     there and falls back to the site root *silently* when it is not, and an app
     one console setting away from a broken password reset is not one to ship.
  3. `422 same_password` had no mapping, so retyping your existing password
     produced the generic "that did not go through" - the least useful sentence
     for somebody who has just typed a password twice. It has its own now.
  Nine tests cover this, two of them built from the real links.
- **Email is the constraint on this tier.** Confirmation is on
  (`mailer_autoconfirm: false`), and the built-in mailer sends only to the
  project owner's address, a couple of messages an hour. `too_many_attempts` is
  its own code and its own Hebrew sentence for exactly that. Real SMTP is a
  `T-3.10` question.
- Tokens are **ES256** (the project publishes a JWKS), so verifying them in the
  API cannot be done with `hmac` alone the way the storage signatures are.
  `T-3.7` settles that.

From `T-3.7`:

- **`pyjwt[crypto]` is the first dependency this project has added rather than
  hand-rolled a signature for, and the reason is the difference between the two
  kinds of failure.** Every other signature here is HMAC - one hash, one
  comparison - and getting it wrong makes the service refuse everything, at
  once, loudly. ES256 is elliptic curve, where a subtly wrong verifier accepts
  forgeries instead and says nothing at all. `tests/test_auth.py` generates real
  EC keys and checks the refusals one at a time: expired, forged, wrong issuer,
  wrong audience, `alg: none`, no subject.
- **Songs have an owner, and `content_hash` is unique per owner rather than
  globally.** Global dedup is cheaper - a song is separated once, ever - but it
  hands the second person to upload something the *first person's row*: their
  title, their stems, and the knowledge that somebody else has that song. At 30
  songs a month against a 10GB bucket the saving was never the constraint.
- **Somebody else's song is a `404`, not a `403`.** A 403 answers a question the
  asker had no business asking. `ownership.py` is one function, used by every
  route that names a song, because the failure this guards against is not "the
  check is wrong" but "one route forgot".
- Jobs are checked through their **song**, not through `jobs.user_id`: ownership
  is recorded in one place now, and two places to ask the same question is one
  place to get a different answer.
- The SSE stream is refused **before it opens**. An event stream that opened and
  then complained would be a 200 the client has to interpret.
- `NoAuth` keeps chapter 11 true - the whole product still runs on a machine
  with no accounts on it - and `create_app` **refuses to start** in production
  without `SUPABASE_URL`, because a deployment with no identity provider serves
  everybody the same library.
- **Verified live**, which is the part the tests cannot reach: a real Supabase
  ES256 token, verified by the API against the project's published keys. No
  token is `401`, rubbish is `401`.
- **The live check found the state nobody designs for**: a cookie the API no
  longer accepts - revoked, expired, or from before a password change. The
  server-rendered library called the API, got a 401, and drew a red error card
  with a request id on it. Being signed out is a state, not a failure; both
  pages now show the way in.

From `T-3.10`:

- **It is deployed.** The API is `https://karuki-api.onrender.com` (Render,
  free, Frankfurt); the web app is `https://karaoke-theta-blue.vercel.app`
  (Vercel, Hobby). `D-23` is closed, and **no provider in this project has
  ever been given a card** - B2, Supabase, Render and Vercel, each verified in
  the account the way `T-0.6` insists on.
- The configuration is `render.yaml` in the repository, read through Render's
  Blueprint: a push changes the deployment and there is nothing to remember to
  click. The web app is deliberately not in it - chapter 14 says the free hours
  fit one continuously running service, and `T-3.11`'s keep-alive spends them
  on the API.
- Vercel needs **Root Directory `apps/web`** (it detects FastAPI from the
  repository root otherwise) and three `NEXT_PUBLIC_*` variables, which are
  **baked at build time** - changing them needs a rebuild, not a restart.
- **Migrations run in the start command.** Chapter 10 asks for a separate step
  ahead of the code and is right, but Render's pre-deploy hook is paid and
  nothing here goes behind a payment method. With the single instance chapter 9
  budgets there is no second container to race, and a failed migration stops
  the server instead of serving against a schema it does not understand. The
  first deploy took Supabase from `c6782423e7f2` to `f27c4d0b9a13`.
- **Two real faults, both found by deploying rather than by reading.**
  - The image crashed at startup: `ModuleNotFoundError: numpy`. T-1.15's tempo
    and key detection is imported by the pipeline on every job, and numpy was
    only in the `separation` group - so it was present on every machine that
    had ever run local separation and absent in the image. It is in `api` now.
  - `KARUKI_CORS_ORIGINS` was marked `sync: false`, Render asked for it before
    Vercel had produced an address, was given nothing, and **skipped the
    variable entirely**. The API ran on the local-development default and
    answered the deployed app's preflight with `400`. It is a `value:` in
    `render.yaml` now - a public address is not a secret, and as a value it
    cannot be silently dropped.
- Chapter 14's three named mistakes are all handled: the port comes from
  `$PORT`, the web app's variables are build-time and written down as such, and
  **CORS is on the bucket as well as on the API** - re-run
  `scripts/bucket_cors.py --apply` after any change to the deployed origin.
- Verified from outside: `/system/health` `200` with `environment: production`,
  `/api/v1/songs` `401 not_signed_in` with a request id, `/docs` `200`, the web
  app `200`, and a preflight from the deployed origin answering with
  `access-control-allow-origin`.
- **The end-to-end run is what actually closed it, and it found five more
  things.** Signed in on the public address, uploaded a 45s excerpt from the
  deployed app and took it to the player: the browser `PUT` went straight to
  B2 with a real percentage, separation ran on the GPU (**17.8s billed**,
  `fc-01M0SHK3KKNK7K2V1EF459JPRS`), the four stems came back from the bucket
  as presigned links, the analysis reported `Dm` and `133 BPM`, and the clock
  ran `0:08 -> 0:24 -> 0:36` of `0:45` with the audio playing. Everything below
  was found by doing that and not by reading anything.
- **The Vercel build had none of its three `NEXT_PUBLIC_*` variables.** Sign-in
  answered "the accounts service is not configured on this deployment", and
  `API_BASE` was falling back to `127.0.0.1:8000` - the deployed app pointing
  at a laptop. The earlier "verified from outside" missed it because every one
  of those checks was `curl` against the API; none of them was the app. **The
  way to check is the bundle, not the dashboard**: fetch the page's chunks and
  grep them for `supabase.co` and the API host, because these are compiled in
  at build time and a variable added afterwards is not in the code that is
  being served until a rebuild.
- **Every table was readable over the internet with the anon key.** `D-15` and
  `D-16` were closed by one signup, and that is exactly the problem: Supabase
  publishes the `public` schema over PostgREST, Alembic creates tables with row
  level security **off**, and the key that opens it is compiled into the browser
  bundle by design. `GET /rest/v1/alembic_version` answered `200` with the
  revision in it. The other tables answered `[]` because the deployed stack had
  never finished a song - that is a schedule, not a defence. Migration
  `b58d0c9a3e77` enables RLS with no policies on all seven tables; our API owns
  them and an owner is exempt, so nothing above it changes. T-3.7 gave songs an
  owner and answers `404` for somebody else's; none of that was in this path,
  because this door does not go through our API at all.
- **`MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET` were swapped**, so every job died
  at `separating` in under a second with `separation_failed`. Reproduced
  locally by swapping them: `modal.exception.AuthError: Token ID is malformed`,
  the deployed message exactly. Two changes came out of it: `AuthError` is now
  `separation_unavailable` - the operator's problem, not the recording's, which
  is T-1.7's distinction and matters because the screen was telling a user to
  retry something that could never work - and `create_app` **refuses to start**
  in production when the backend is `modal` with no tokens, the same way it
  refuses without `SUPABASE_URL`.
- **Every transcription in the cloud had been failing, silently, from the first
  deploy.** Groq answered `400 file must be one of the following types` because
  `S3Storage.local_path` named its downloaded copy by a hash with **no
  extension** - the service types the upload by its filename. Locally the file
  is the real one, suffix and all, so this could only ever appear in the cloud,
  and it appears as a song with no words rather than as a failure. The copy now
  keeps the key's suffix, and `GroqTranscriber` refuses a file with no
  extension itself rather than letting the service answer with a list of types.
  Worth knowing: **a 400 does not move Groq's `x-ratelimit-remaining-requests`**,
  which is what made "the key is missing" look true from outside for an hour.
- **`GET /jobs/{id}/events` cannot authenticate, so D-18's SSE is dead in any
  deployment with accounts.** `EventSource` cannot send an `Authorization`
  header and the session cookie is on the web app's domain, not the API's, so
  the stream is refused before it opens and the progress screen sits on
  "מתחבר מחדש…" for the whole job. **T-1.11's polling fallback carried it**,
  which is why nothing looked broken and why this survived T-3.7 unnoticed.
  Deliberately **not** fixed here: the cheap fix is the token in a query string,
  which is the one thing this project has been careful never to do, and the
  honest one is `fetch` with a `ReadableStream` instead of `EventSource` -
  its own task, not part of a deployment.
- Render's free instance **sleeps**: a cold `/system/health` took **32.7s**
  against 0.5s warm. That is `T-3.11`, measured rather than assumed.
- **Both fixes were then checked against the deployed services, not only in
  tests.** With the deploy live, `GET /rest/v1/alembic_version` with the anon
  key answers `[]` where it had answered the revision, and a song uploaded from
  the public address came back with **two lyric versions - `mix_asr` v1 and
  `vocals_asr` v2, T-2.4's shape exactly** - and its words on the player. The
  transcription fix was also measured directly against B2 and Groq: the
  downloaded copy is now `0cd5ec1db3789b12.mp3` rather than
  `0cd5ec1db3789b12`, and the same stem that had been answered with a `400`
  came back **Hebrew, 5 segments, 29 words, in 4.2s**.

From `D-18`'s repair (the loose end `T-3.10` left, done between `T-3.11`'s
measurements):

- **`EventSource` cannot send a header, so the progress stream had to stop
  being an `EventSource`.** `apps/web/src/lib/sse.ts` reads the response with
  `fetch` and a reader, and parses the frames by hand: `event:` and `data:`
  lines, a blank line ends a message, a leading colon is a comment. That is the
  entire format, which is also why the API frames it by hand
  (`apps/api/sse.py`).
- **The token is in a header and not in the query string.** A URL is logged by
  every proxy it passes, kept in history, and handed on in a `Referer`; this
  project has been careful about that all the way through - see the note in
  `karuki-browser-checks` about a token appearing twice in a transcript - and a
  progress bar is not the reason to stop.
- What is lost is `EventSource`'s automatic reconnection. **The polling
  fallback is now the only recovery**, which is honest: it had to exist anyway
  for the proxy that breaks streaming, and it has in fact been carrying every
  job since `T-3.7`.
- Verified on the deployed pair, which is the only place the bug existed: one
  song from upload to `ready` with **exactly three API calls** -
  `upload-url`, `songs`, and `/jobs/{id}/events` held open for 34s - and
  **zero** `GET /jobs/{id}` polls. The screen showed `מפריד ערוצים` and then
  `מוכן` without ever showing `מתחבר מחדש…`.

From `T-3.11`:

- **The keep-alive is a cron-job.org job, and the GitHub Actions workflow is
  only its backup.** Chapter 14 asks for an external free cron every 10
  minutes and is explicit that it must not run inside the service - a service
  that is asleep does not wake itself. GitHub Actions looked like the answer
  that costs nothing and lives in the repository, and it was **measured and
  rejected**: with a `*/5` schedule it fired **one** run in 81 minutes (at
  14:25 UTC, 43 minutes after the workflow registered, and nothing in the 38
  after). A `push`-triggered run in the same repository took **6 seconds**, so
  the runners were fine and the scheduler was not - GitHub's own status page
  said Actions was degraded. Render sleeps after 15 minutes, so that is not a
  keep-alive, it is the appearance of one.
- `.github/workflows/keep-alive.yml` stays anyway: it costs nothing, it runs
  somewhere else entirely, and the measurement is written at the top of it so
  nobody relies on it alone.
- **cron-job.org**: free, **no payment method** (the fifth provider in this
  project verified that way), minute resolution, `https://karuki-api.onrender.com/system/health`
  every five minutes. Notify after **3** consecutive failures rather than 1,
  because a cold start takes ~33s against the job's 30s timeout - the single
  failure that is worth an email is the one the system was going to fix by
  itself.
- **Verified by not touching it**: 44 minutes in which neither this machine nor
  a browser asked the API anything, then one request - `uptime_sec` **3441**
  (57 minutes) and the answer in **0.78s**. It had not restarted, so it had
  not slept, and the only thing knocking was the cron.
- The measurement before this one was wrong and is worth remembering as a
  shape: the first monitor polled `/system/health` every 100 seconds to watch
  the uptime climb - **the measurement was the keep-alive**. Anything that
  watches a service for signs of sleep has to be silent for longer than the
  sleep threshold.

From `T-3.12`:

- **D-24 is Sentry**, free tier, **no payment method** - the sixth provider
  verified that way. Organisation `karuki`, **EU data storage** (the rest of
  the project is in Frankfurt and Amsterdam, and Sentry says at signup that the
  region cannot be changed later). Two projects, because an error from the API
  and an error in a browser are different incidents: `python-fastapi` and
  `karuki-web`. Only **error monitoring** is enabled - tracing, profiling,
  replay and logs each have their own quota, and the free plan's 5,000 errors a
  month are what has to last.
- **The API uses the real SDK; the browser does not.** That asymmetry is the
  whole design decision. In the API it is one small dependency in an image that
  already carries pyjwt, and it buys stack traces, local variables and request
  context for an error that happened once, to somebody else, in production. In
  the browser `@sentry/nextjs` is a build plugin, instrumentation files and a
  runtime package added to an app that deliberately has three (T-1.9), for a
  wire format that is **a POST with three lines of JSON in it**.
  `apps/web/src/lib/monitoring.ts` writes that envelope by hand. Verified
  against the real service: it answered `200 {"id": "79eea5b1…"}`.
- **Reporting is explicit, not automatic, and the reason is T-1.2.** The
  middleware *handles* every unhandled exception to produce the error shape
  with a `request_id` on it, so by the time an SDK's own ASGI hook could see
  it there is nothing left to see. `middleware.py` therefore calls `capture`
  itself - and passes the `request_id`, which is what makes an issue in the
  dashboard and the id on somebody's screen the same incident.
- A job that **crashes** is reported; a job that **fails** is not. A
  `PipelineError` is the file's problem or the operator's, it already has a
  code the screen explains in Hebrew, and it is not news. An unexpected
  exception in the pipeline has no request to carry it and nobody reading the
  log of a free instance, which is exactly what D-24 is for.
- **`GET /system/error` is chapter 14's "deliberate error", and it is closed by
  default.** It needs `KARUKI_ERROR_PROBE_TOKEN` set *and* matched, and answers
  `404` otherwise - the same answer a wrong token gets, and the same answer a
  deployment gets when the variable is skipped, so forgetting it fails safe. An
  open route that raises 500s would spend a 5,000-error quota in an afternoon.
  Render generates the value (`generateValue: true`), so it is never typed
  anywhere.
- Nothing is reported without a DSN, and a DSN with no `sentry-sdk` installed
  is a warning rather than a crash - the local venv was built by hand in phase
  0 and is not installed from `pyproject`, so that combination is an ordinary
  developer state rather than a mistake.
- **Verified on the deployed pair, both sides.** `/api/v1/system/error` answers
  `404` with no token and with a wrong one, and with the right one answers
  `500 internal_error` carrying `request_id 9c38843e…` - and **that same id is
  a tag on the issue in Sentry**, which is the whole point of reporting from
  the middleware rather than from an SDK hook. From the deployed web app, a
  browser error posted an envelope that answered `200`, and both issues are in
  the dashboard: `ProbeError` in `python-fastapi` and `Error` in `karuki-web`,
  the second with the page URL on it.

From `T-3.13`:

- **The six checks are `scripts/smoke.py`**, one command, about two minutes,
  and chapter 14's rule is repeated at the top of it: if one fails, roll back
  rather than fix forward. Two checks were added that the chapter does not name
  and this project has paid for - the **web app's own page** (`T-3.10` shipped
  a build with none of its variables while every `curl` against the API passed)
  and the **over-quota refusal**, which is how the checklist asks for quotas to
  be verified.
- **A run without credentials skips what it cannot check and says so.** Against
  the deployment: `3 passed, 0 failed, 4 skipped` - the four need a password,
  which lives in the environment and never in the repository or a command line.
  So checklist item 11 is **not** marked done; ten of the twelve are.
- **`.env.example` is now a test, not a habit.** `tests/test_env_example.py`
  reads the source for every environment variable the code looks at and fails
  on any the file does not mention. It found **twelve** undocumented names the
  day it was written. The drift only shows up when somebody is setting up a
  deployment, and a name missing there is a variable missing in production -
  which is exactly how `KARUKI_CORS_ORIGINS`, `MODAL_TOKEN_ID` and
  `GROQ_API_KEY` each broke the first deploy.
- **The retention policy was not scheduled at all**, which the checklist caught.
  `T-3.9` built it as a script for a machine with the database credentials on
  it, and a deployment has no such machine - Render's cron is a paid service
  type. `POST /system/reap` is that script as an endpoint, with **its own
  token** (deliberately not the error probe's: one route raises an exception,
  the other deletes audio), and `.github/workflows/reap.yml` calls it daily.
  **This is the one place GitHub's unreliable scheduler is the right tool** -
  the rule is six months and the pass is idempotent, so a run hours late
  changes nothing. The same scheduler was rejected for the keep-alive in the
  same week, on the same measurement.
- Verified against the deployment: no token and a wrong token both `404`;
  `days=0` counted **4 songs and 8.4MB** with `freed_bytes: 0`; the real
  180-day rule found nothing, which is correct because every song there was
  uploaded that day. The `days=0` call is what proves the mechanism rather than
  the emptiness of the shelf.
- **The one that stays open is item 12, a real phone** (`T-0.2.5`, blocked on
  hardware since phase 0). It is not ticked, and the browser measurements
  standing in for it say plainly that they are simulations.
- Worth knowing for any live check from this machine: **TLS inspection breaks
  `curl` against `*.onrender.com` and `*.vercel.app`** while leaving other
  hosts alone - TCP connects, the handshake renegotiates and hangs. The same
  requests through `trust_system_certificates()` answer in under a second, so
  the project's own scripts are unaffected and `curl` is the thing that lies.
- **The smoke test failed twice on itself before it passed**, and that is the
  part worth remembering: chapter 14 says a failed check means roll back, so a
  tool that fails for its own reasons nearly rolled back a working deployment.
  Both were the script's own vocabulary - `size_bytes` for `bytes` and `key`
  for `upload_key`, which FastAPI answered with `422` and the screen read as a
  broken quota; then a preflight looked up as `Access-Control-Allow-Origin`
  when B2 sends `access-control-allow-origin`, which read as a misconfigured
  bucket and advised re-running `bucket_cors.py` on one that was working.
  `tests/test_smoke_payloads.py` pins the request bodies to the API's models
  and the header lookup to lower case. The script also runs against a **local**
  instance now (no accounts, chapter 11, so the sign-in check steps aside),
  which is how that class of bug gets caught without a password or a deploy.
- **It passed in full on 2026-08-25**: `8 passed, 0 failed, 0 skipped`,
  including the two that had never run - an over-quota upload refused with
  `413 file_too_large`, and 2.8MB going browser-to-bucket in 13.7s with the
  preflight allowing the deployed origin. Checklist item 11 is done; **eleven
  of twelve**, and the twelfth is the phone.

From `T-4.1`:

- **The flag is the feature.** `KARUKI_IMPORT` is read in `create_app`, and with
  it off the import router is **not included**: the path is not routed, not in
  the OpenAPI document, and not offered by any screen. A registered endpoint
  that refuses politely would be a feature that is still there. Worth knowing
  before it looks like a bug: with the flag off `POST /songs/import` answers
  **405 and not 404**, because `GET /songs/{song_id}` already claims that path -
  and every `POST /songs/<anything>` answers the same way whether the flag is on
  or off, so it leaks nothing. The OpenAPI document is where the absence is real.
- **Two resolvers, and the split is the whole reason the flag exists.**
  `direct` is a plain link to an audio file: no dependency, no account, nothing
  anybody's terms have an opinion about, so it is on by default for the reason
  LRCLIB is (T-2.2). `yt-dlp` reads a video page, is a large dependency that
  tracks other people's sites, and is **off unless named** - the rule the
  `modal` separator has, for a different reason. A name that is neither refuses
  to start; a variable that silently does nothing has cost this project two
  deployments already.
- **The dangerous part of accepting a URL is not the download, it is whose
  address it is.** An API that fetches what it is told can read what only it can
  reach: `169.254.169.254`, a database on a private address, itself.
  `check_url` refuses anything that is not `http(s)` and anything whose name
  resolves to a non-global address - **every** address it resolves to, not the
  first - and it is applied to **each hop of a redirect**, because a public URL
  that redirects to `127.0.0.1` is the ordinary way that check is got around.
  Honest limit, written in the code: urllib resolves the name again when it
  connects, so a name that answers differently a second later is not caught.
- Because of that, **a live check cannot import from `localhost`**. The address
  the test is served from is exactly the address the importer refuses.
- The download runs **in a thread**, for T-3.5's measured reason: a long
  blocking read on the event loop is 39.5 seconds in which the single instance
  answers nothing, `/system/health` included. The size limit is enforced on the
  bytes as they arrive and only pre-checked against `Content-Length`, which is a
  claim, and a stranger's.
- **`_ingest` is shared with both upload routes rather than copied.** An import
  that normalised or deduplicated even slightly differently would be a second
  definition of what a song is. What an importer may still say is the title and
  the source; everything else - ffmpeg, the hash, the quota, the job - is the
  same code. So a link to a song already uploaded from a file **deduplicates**,
  because the hash is of the normalised audio (T-1.5).
- The API carrying these bytes is a deliberate exception to chapter 3's "the API
  never handles audio", and there is nobody else to carry them: a browser cannot
  fetch a third-party address and PUT it to the bucket (that is what CORS
  prevents), and doing it on the GPU function would spend credit on a download
  that needs no GPU.
- `GET /system/features` is how the web app knows, rather than a second variable
  in Vercel that could disagree with the one in Render. It is deliberately not
  folded into `/system/health`, which a cron polls several hundred times a day
  and which must stay the cheapest thing in the service.
- Verified live, both ways. **Flag on**, in a browser against the real API:
  pasted `https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3`, saw
  "מביאים את הקובץ…", and landed on the progress screen at "מפריד ערוצים" -
  **exactly one API call for the import** (`POST /songs/import 201`) plus the
  SSE stream, and the row reading `source_type=url`, `source_ref` the address,
  title `SoundHelix-Song-1`, 372 seconds. **Flag off**, same browser, same page:
  the form and its "או" divider are gone, the upload form is untouched,
  `/system/features` says `import_enabled: false`, and the route is absent from
  `/openapi.json`.
- Worth knowing for the next live check: **`upload.wikimedia.org` drops the
  connection** for this client (`RemoteDisconnected`) where soundhelix.com
  answers in 12.6s for 8.9MB. Same shape as the Groq `User-Agent` finding - some
  hosts have an opinion about who is asking - so a failing import is worth
  trying against a second address before believing the code is wrong.
From `T-4.2`:

- **Until this task the project threw away what the file says it is.**
  `normalise` strips metadata on the way to the wav - deliberately, an upload's
  tags are the user's and nothing downstream wants them - and the title came
  from the file name. `packages/audio/tags.py` reads the *original* with one
  extra `ffprobe` (~50ms) on a path already running ffmpeg over the whole file.
- **Four sources, in this order: the importer, the file's tags, the open lyrics
  database, and last the file name.** Which is which lives in one place,
  `packages/core/metadata.py`, and above all four sits a person: any correction
  stamps `songs.details_edited_at` and every automatic write checks it.
- **A file name is never split into artist and title.** T-2.2 already decided
  that question cannot be answered without asking the database -
  `matching.readings` tries `X - Y` both ways round for exactly this reason - so
  writing a guess onto the row would make that decision twice, in two places,
  and the second one would be wrong about half the time. A file name fills the
  title and leaves the artist empty.
- **The one thing that can resolve it is a catalogue match**, because that match
  was made on the title, the artist *and* the measured duration. So a confident
  match may rename the song, under two narrow rules: the artist is filled only
  when there is none (a tag was written about *this file*; a database row is
  about a recording that merely matched), and the title is replaced only when
  ours **contains** theirs after normalising - `ריטה - שביר` contains `שביר`, so
  the extra words are the file name's. A merely *similar* title is left alone;
  `מעמקים` scores 0.923 against `ממעמקים` and is a different word (T-2.2), and
  "similar" is exactly where a wrong match would land.
- `details_edited_at` exists because the catalogue write-back lands **minutes
  after the song is already on the screen**. A person can perfectly well have
  fixed the name in between, and having it overwritten by a machine while they
  are looking at it is how somebody stops trusting a field.
- **The corrections are `PATCH /songs/{id}`, and loud rather than clamped.**
  T-1.16 clamps the player settings because an auto-save must never fail a
  session; this is a button somebody pressed with a name they typed. An empty
  title is `song_title_empty`, an absurd one is `song_name_too_long`, and an
  empty *artist* is a real answer that clears the field. A field that is not in
  the body is left alone - which is the whole reason it is a PATCH: a PUT makes
  "I only changed the artist" indistinguishable from "the title is now blank".
- **Two rules came from the five real Hebrew mp3s on this machine rather than
  from imagination.** Two of them carried `albumaty.com` and
  `newsmusic.blogspot.com` in the artist field - a download site's watermark,
  not a name - so a value that is a bare web address reads as no value. And two
  carried `אבי לרנר/חדשות המוזיקה להורדה`: ID3v2.3 says a slash separates
  performers, so taking the first is reading the format rather than guessing.
  `AC/DC` is the counterexample, and a segment of one or two characters is what
  tells them apart - a heuristic, and part of why the field is editable.
- Measured on those five: before, five titles from file names and **zero
  artists**; after, `בני פרידמן, ברוך לוין - ושבו בנים.mp3` becomes `ושבו בנים`
  by `בני פרידמן, ברוך לוין`, `JX_w2zDaAXY.mp3` becomes `יגאל בשן - תן לי`
  instead of a YouTube id, and **three of five have an artist**. The two with no
  artist tag stay empty rather than guessed.
- The editing is **in place on the song page**, not on a screen of its own: the
  name is already there and that is where somebody notices it is wrong. Sending
  them elsewhere to fix it is how a field stays wrong.
- Verified live end to end: the tagged excerpt uploaded through the real API
  arrived as `ושבו בנים` / `בני פרידמן, ברוך לוין` / 30s with nothing typed,
  and in a real browser "עריכת פרטים" → artist to `בני פרידמן` → save wrote
  `PATCH 200`, stamped `details_edited_at`, and the name survived a reload.
- **What could not be verified live: the catalogue write-back.** LRCLIB knows
  none of the five songs on this machine - asked, all five came back `None` -
  and the write-back needs a match. It rests on `tests/test_pipeline.py`, which
  runs the real pipeline against a stub catalogue and covers both the rename and
  the refusal to rename a song somebody has already named.

From `T-5.1`:

- **The evening lives on the device, not in the database.** T-5.2 made the same
  call about the A-B loop: a running order belongs to the half hour it was made
  in, and nothing here is worth a table, a migration or a round trip. But unlike
  the loop it cannot be React state - every song is its own server-rendered page,
  so moving to the next one is a navigation and anything held in a component is
  gone by the time the next player mounts. `localStorage` is what makes the
  queue outlive the page that built it, and it survives a tab closed by accident
  half way through an evening, which `sessionStorage` would not.
- **The engine now says `ended`, which is not the same as `playing: false`.**
  The worklet always sent an `ended` message and the engine turned it into a
  pause. That was enough while nothing followed a song, and the difference is
  the whole of this feature: a song running out is what starts the next one, and
  a person pressing stop is not.
- **A song that is not in the queue leads nowhere.** `nextAfter` answers null
  both for the last song and for a song the queue has never heard of, so opening
  one song from the library in the middle of an evening does not drop the singer
  into somebody else's running order.
- **Autoplay is in the address, not in storage.** `?autoplay=1` belongs to one
  navigation, and the player strips it the moment it acts on it - so a reload an
  hour later opens the song silently. It works at all because a browser keeps
  the *document's* activation across a client-side navigation: the play press
  that started song one is still good for song two. A full reload is not, and
  the honest response there is to sit paused rather than to pretend.
- **Full screen is our own layout first and the browser's second.**
  `requestFullscreen` on an element does not exist on iOS Safari, so the class
  has to be enough on its own; what the browser adds when it can is hiding its
  chrome. The live check confirmed it from the other end: the automation's
  synthetic keypress is not a user gesture, the browser refused with *"API can
  only be initiated by a user gesture"*, the catch swallowed it, and the cinema
  layout applied anyway - lyrics from 25.6px to 64px with the mixer, the key,
  the tempo and the loop hidden.
- **Letters are matched on `event.code`, never on `event.key`** (`keys.ts`). On
  a Hebrew layout the V key produces `ה`, N produces `מ` and F produces `כ`, so
  a shortcut table written against the letter would work on the developer's
  layout and silently do nothing for the person the app is for - which is
  indistinguishable from a broken feature. `physicalCode` falls back to the
  letter only when `code` is empty, and that is not hypothetical: **browser
  automation dispatches keys with an empty `code`**, so without the fallback
  none of this could have been checked in a browser at all. The fallback can
  only ever recover a Latin layout, which is the argument for `code` rather than
  a reason to trust `key`.
- **The shortcuts keep their hands off form controls**, and the scrubber is the
  case that proves it: its own arrow keys scrub, and a 5-second seek on top of
  that would be two controls fighting. Verified live - paused at 0:03 with the
  scrubber focused, ArrowLeft moved it to 0:04, one native step and nothing
  else. Space is also left alone on a focused button, where the browser already
  activates it; handling both would be two toggles for one press, which looks
  exactly like nothing happening.
- **Forward is the direction the words are read in.** In RTL the browser already
  reverses a range input's arrows, so ArrowLeft has to mean *later* or the two
  controls on one screen disagree about which way time goes.
- The keyboard handler is attached once and reads the current dispatch through a
  ref rewritten on every render. T-2.9 lost a nudge to exactly this shape of bug,
  and a listener closing over `mix` and `state` would go stale the same way -
  silently, and only for the person using the keys rather than the buttons.
- **Verified live end to end, three songs of 15s each on the local stack**: one
  click on "התחילו את הערב" and the browser then ran, untouched, `ותהי שמחה`
  0:00→0:14, `ושבו בנים` 0:00→0:14, `דרוש נא` 0:00→0:14 - 52 seconds, with about
  2.5s between songs for four stems to be fetched and decoded. Each hop is a
  client-side navigation, which is why the audio kept its permission to play.
  The keys were then checked one at a time on a real page: `F` full screen,
  ArrowLeft +5s, ArrowUp twice to `+2`, `-` to 95%, `V` taking the vocals fader
  to 0% and the button to "החזר שירה", Space starting playback, `/` opening the
  shortcut list, and `N` skipping to the next song in the queue.
- **Every song in an evening is counted as played**, which chapter 9's retention
  rule depends on. Worth knowing before it looks like a bug: the network log
  shows each `POST /played` as `204` *and* `ERR_ABORTED`, because the page
  navigates away as the response arrives. The server had already recorded it -
  all three songs came back with a `last_played_at` a few seconds apart.
- The queue button is only on a **playable** song. Queueing one that is still
  separating would put a wait in the middle of an evening.

From the attempt to make YouTube links work (2026-08-25, on top of `T-4.1`):

- **It does not work, and the reason is not in this code.** yt-dlp selects a
  perfectly good stream - format 251, opus, 4,949,491 bytes for a 288s track -
  and then YouTube's media server answers that URL with `200`,
  `Content-Length: 145107` and `content-type: video/mp4`. Confirmed with a
  plain `urllib` GET against the media URL, so it is not yt-dlp's download
  logic. The `android` client, which selected a *different* 7.8MB format,
  received **exactly the same 145,107 bytes** - an identical byte count across
  two different formats is what says this is a stub served to a client that has
  not passed the player challenge, rather than a download that broke. Every
  other player client (`web_safari`, `ios`, `tv`, `web_embedded`, `mweb`)
  refused outright with "Requested format is not available".
- **The dangerous part was that it looked like success.** yt-dlp raised nothing,
  the resolver returned an `Imported`, and ffmpeg normalises a 145KB stub
  happily - so the shape of the failure was a five-second "song" that had cost
  a GPU separation to produce. `_refuse_a_stub` now compares what landed on
  disk against the size yt-dlp itself declared for the stream it chose, and
  raises `import_incomplete` under `MIN_COMPLETE_FRACTION` (0.75 - loose enough
  never to argue with `filesize_approx`, which is bitrate times duration, and
  a stub is ~3% of its promise). A size the library does not know is not
  evidence of anything and lets the file through.
- **`YtDlp.fetch` never called `trust_system_certificates`.** The direct
  resolver has called it since T-4.1; this one had not, so on this machine the
  first real run died with "self-signed certificate in certificate chain",
  which reads like a broken network and is not one. yt-dlp builds its own TLS
  context, and `truststore.inject_into_ssl()` is global, so one call before it
  opens anything is enough.
- **So `yt-dlp` is deliberately still not a dependency and still not switched
  on in `render.yaml`** (`KARUKI_IMPORT=direct`). Shipping it would add a large
  dependency that tracks somebody else's site, to an image that has to stay
  deployable on a free tier, for a resolver measured to return nothing usable.
  What would be needed beyond that is a signed-in YouTube session or a PO-token
  provider on the server - credentials this project does not hold and a second
  step past terms that are not ours to decide.
- Honest limit of the measurement: it was made from this machine's IP. A
  datacenter address is treated *worse* by that service rather than better, so
  the deployment is not expected to differ - but that part is an inference and
  was not measured.
