# Chapter 14's deployment checklist

T-3.13. Twelve items, each with what was actually done and where the evidence
is. Everything marked done was measured against the deployed services; the two
that are not done say so.

The smoke test that goes with this is `scripts/smoke.py` — six checks, about
two minutes, run after every deploy. Chapter 14's rule for it is worth
repeating because it is the point: **if one fails, roll back rather than fix
forward.**

| # | Item | Status |
|---|---|---|
| 1 | No credit card on any service, spending caps where possible | done |
| 2 | No secret in the repository — checked in the history, not only the working tree | done |
| 3 | `.env.example` up to date, with every name | done, and now a test |
| 4 | CORS configured on the API *and* on the storage bucket | done |
| 5 | Storage links signed and expiring — no public files | done |
| 6 | Migrations ran and succeeded | done |
| 7 | keep-alive active and verified | done |
| 8 | Error monitoring connected, verified with a deliberate error | done |
| 9 | User quotas active — verified by trying to exceed one | done |
| 10 | Retention policy scheduled, and a successful dry run | done |
| 11 | The smoke test passed in full | done (see below) |
| 12 | Checked on a real phone, not only a browser simulation | **not done** — `T-0.2.5`, waiting on hardware |

## 1. No credit card, and caps where they exist

Eight providers, each verified in the account at signup the way `T-0.6` insists
on — the published free-tier terms were wrong twice in phase 0, both times in
this project's disfavour.

| Provider | What for | Card asked for |
|---|---|---|
| Backblaze B2 | object storage (D-12) | no |
| Supabase | Postgres and accounts (D-15, D-16) | no |
| Modal | the GPU function (D-05) | no |
| Groq | transcription (D-27) | no |
| Render | the API (D-23) | no |
| Vercel | the web app | no |
| cron-job.org | the keep-alive (D-26) | no |
| Sentry | error tracking (D-24) | no |

Cloudflare R2 was rejected in `T-0.6` for putting its free tier behind a
payment method, which is the one decision this rule has actually cost.

**Caps.** Only Modal has a spending control that means anything here, and it is
the tightest of them: the workspace credit is $1/month, which is why
`KARUKI_SEPARATION_BACKEND` defaults to `local` and the remote backend has to be
asked for by name (`T-1.6`). Everywhere else the free tier *is* the cap — there
is no payment method to exceed it with, and the failure mode is a pause rather
than a bill, which is what chapter 1 chose.

## 2. No secret in the repository

`.env` and `apps/web/.env.local` are gitignored and have never been committed:
searched across **all 53 commits**, by path and by content, for private-key
headers, JWT-shaped strings, `gsk_`-prefixed keys, the B2 key id in use, and
the maintenance token. Nothing.

What *is* in the repository is deliberate and public by design: the deployed web
origin in `render.yaml` — an address, and a `value:` precisely because a blank
`sync: false` is silently skipped (`T-3.10`).

## 3. `.env.example` carries every name

`tests/test_env_example.py` reads the source for every environment variable the
code looks at and fails on any the file does not mention. It found twelve
undocumented names the day it was written, `SENTRY_DSN` among them.

A test rather than a habit, because the drift is invisible until the moment
somebody is setting up a deployment — and a missing name is a missing variable,
which is how `KARUKI_CORS_ORIGINS`, `MODAL_TOKEN_ID` and `GROQ_API_KEY` each
broke the first deployment.

## 4. CORS on both sides

The API's origins are `KARUKI_CORS_ORIGINS` in `render.yaml`. The bucket's are
written from the same list by `scripts/bucket_cors.py --apply`, so the two
cannot drift. Verified against B2 itself, and the smoke test's upload check
sends the browser's actual preflight to the bucket rather than trusting the
file.

## 5. Signed, expiring links — and nothing public

Every read is a presigned URL with a one-hour expiry (`KARUKI_SIGNED_URL_TTL`),
on both backends. The bucket is private, and `T-3.1` verified the three
refusals against B2 directly: unsigned is `401`, an edited expiry is `403`, an
expired link is `401`. The upload ticket is narrower still — one key, one
method, one hour — so a link that lets a browser write cannot be talked into
overwriting a stem (`T-3.2`).

## 6. Migrations ran

Five migrations, `1fe75bcef4e6` through `b58d0c9a3e77`, applied to the hosted
Postgres by the deploy's start command. Chapter 10 asks for a separate step
ahead of the code; Render's pre-deploy hook is paid, and with the single
instance chapter 9 budgets there is no second container to race. A failed
migration stops the server rather than serving against a schema it does not
understand.

The last of them is the one the live check forced: row level security on all
seven tables, because Supabase publishes the `public` schema over PostgREST and
Alembic creates tables with RLS off.

## 7. keep-alive

cron-job.org, every five minutes, against `/system/health`. Verified by *not
touching it*: 44 minutes in which nothing else asked the API anything, then one
request — `uptime_sec` 3441 and an answer in 0.78s (`T-3.11`).

GitHub Actions was tried first and measured out: one scheduled run in 81
minutes against a `*/5` cron, on a service that sleeps after 15. That workflow
stays as a backup, with the measurement written at the top of it.

## 8. Error monitoring, with a deliberate error

Sentry, EU region, two projects. `GET /api/v1/system/error` answers `404`
without a token and `500` with it; the run against the deployed API produced
`request_id 9c38843e…`, and that id is a tag on the issue in the dashboard —
which is what makes the number on somebody's screen and the report the same
incident. The browser side posted an envelope that answered `200`, and its
issue carries the page URL (`T-3.12`).

## 9. Quotas

Chapter 9's limits are enforced when the upload ticket is issued and shown on
the account screen (`T-3.8`). The smoke test makes the attempt the checklist
asks for: a ticket for a file larger than anyone's quota, which must be refused
with a code before a single byte moves.

## 10. Retention, scheduled

`POST /system/reap` is `scripts/reap.py` as an endpoint, so a deployment with
no machine of its own can run it — Render's cron jobs are a paid service type.
`.github/workflows/reap.yml` calls it daily at 03:15 UTC with its own token,
which lives in the repository's Actions secrets and nowhere else.

**This is the one place GitHub's unreliable scheduler is the right tool**: the
rule is six months and the pass is idempotent, so a run that lands hours late,
or tomorrow, changes nothing. The same scheduler was rejected for the keep-alive
in the same week, on the same measurement.

A dry run is the default on the endpoint and in the script, for `T-3.9`'s
reason: a command whose default is destructive is one that will one day be run
by accident. The endpoint's own token is deliberately *not* the error probe's —
one of those routes raises an exception, the other deletes audio.

## 11. The smoke test

`scripts/smoke.py` covers chapter 14's six checks, and adds two that the
project has paid for: the web app's own page (`T-3.10` shipped a build with
none of its variables while every `curl` against the API passed) and the
over-quota refusal.

Without credentials it runs the checks that need none and **skips** the rest
rather than reporting them as passes. With `KARUKI_SMOKE_EMAIL` /
`KARUKI_SMOKE_PASSWORD` in the environment it signs in, uploads a file straight
to the bucket, watches the progress stream open, and fetches a stem back
through a signed link.

## 12. A real phone

Not done, and not because of this task: `T-0.2.5` has been blocked on hardware
since phase 0. Everything for it is built and waiting — `T-1.17`'s light mode
exists precisely because four stems may be too much for a phone, and the
threshold it falls back at is a guess until somebody measures it on one.

The browser measurements that stand in for it are honest about being
simulations: 375×812 with no horizontal overflow, every touch target at least
44px, and light mode building half the vocoder work.
