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

Run the API alone, without Docker (docs at `/docs`, health at `/system/health`):

```
.venv\Scripts\python.exe -m uvicorn apps.api.main:app --reload --port 8000
```

Serve the prototypes locally:

```
.venv\Scripts\python.exe -m http.server 8000 --bind 127.0.0.1
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
- This machine runs TLS inspection. `requests` fails with "self-signed
  certificate in certificate chain" — `research/verify_groq.py` fixes it with
  `truststore.inject_into_ssl()`, not by disabling verification. `curl` needs
  `--ssl-no-revoke`.
- Console is cp1255. Set `PYTHONIOENCODING=utf-8` when a tool prints Unicode,
  and pass `encoding="utf-8"` to `subprocess` when filenames are Hebrew.

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

Phase 1 in progress. `T-1.1`, `T-1.2` and `T-1.3` done. Next is `T-1.4`: models
and migrations for songs, stems, jobs.

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

Open provider decisions, both deferred to phase 3 and neither blocking:
`D-12` storage (needs an alternative to R2) and `D-15`/`D-16` database and auth.
