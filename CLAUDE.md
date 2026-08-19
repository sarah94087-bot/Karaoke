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

Phase 1 in progress. `T-1.1` through `T-1.6` done. Next is `T-1.7`: the job
state machine and progress, surviving a restart.

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
- Only `songs`, `stems`, `jobs` exist. The other five tables in chapter 5 come
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

Open provider decisions, both deferred to phase 3 and neither blocking:
`D-12` storage (needs an alternative to R2) and `D-15`/`D-16` database and auth.
