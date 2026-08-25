"""Chapter 14's post-deploy smoke test: six checks, about two minutes (T-3.13).

    .venv\\Scripts\\python.exe scripts\\smoke.py
    .venv\\Scripts\\python.exe scripts\\smoke.py --api http://127.0.0.1:8000 --web http://localhost:3000

The chapter is blunt about what this is for: **if one of them fails, roll back
rather than fix forward.** So each check answers one question that a deployment
either passes or does not, and the script says which, in order, with the number
it measured.

What it needs, and what it does without:

- Nothing at all for the checks that do not need a session: the health
  endpoint, the web app, CORS, the refusals, and the error probe's 404.
- `KARUKI_SMOKE_EMAIL` / `KARUKI_SMOKE_PASSWORD` to sign in for real. This is
  the only way to check chapter 14's "signing in works and returns a valid
  token", and the password stays in the environment - never in this file, never
  in the repository, never on a command line that lands in a shell history.
- `KARUKI_ERROR_PROBE_TOKEN` for the deliberate error. Without it that check is
  skipped rather than failed, because the route is *meant* to be a 404 to
  everybody else.
- `--song` for the upload check. It defaults to any short audio file under
  `input/`, and skips if there is none.

Skips are printed as skips and never counted as passes. A run with no
credentials checks five things properly and says so, which is worth more than
one that says OK because it did not look.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from packages.providers.net import USER_AGENT, trust_system_certificates  # noqa: E402

DEFAULT_API = "https://karuki-api.onrender.com"
DEFAULT_WEB = "https://karaoke-theta-blue.vercel.app"

# A cold start on a free instance takes about 33 seconds (T-3.10), and the first
# check is allowed to be the one that pays for it.
TIMEOUT = 60


# The two request bodies this script sends to the API, as functions rather than
# as literals buried in the checks below. `tests/test_smoke_payloads.py` pins
# them to the Pydantic models on the other side, because the first real run of
# this script failed twice on its own field names - `size_bytes` for `bytes`
# and `key` for `upload_key` - and reported it as a broken deployment, which is
# the one thing a smoke test must never do.


def lowercase(headers: object) -> dict[str, str]:
    """Header names, lowercased, because HTTP does not care and this did.

    B2 answers a preflight with `access-control-allow-origin` in lower case.
    Looking it up by the spelling in the specification found nothing, so a
    bucket that is configured correctly - status 200, the rule in place, the
    browser uploading through it all day - was reported as refusing the
    request, with advice to re-run `bucket_cors.py`. That is a smoke test
    telling somebody to fix something that is not broken.
    """
    return {str(name).lower(): str(value) for name, value in dict(headers).items()}


def upload_ticket_payload(filename: str, size_bytes: int) -> dict[str, object]:
    return {"filename": filename, "bytes": size_bytes}


def song_from_upload_payload(upload_key: str, filename: str) -> dict[str, object]:
    return {"upload_key": upload_key, "filename": filename}


@dataclass
class Result:
    name: str
    ok: bool | None  # None = skipped
    detail: str = ""


@dataclass
class Smoke:
    api: str
    web: str
    song: Path | None = None
    results: list[Result] = field(default_factory=list)
    token: str | None = None
    # True for a local instance, where chapter 11 says there are no accounts and
    # every request is the development user. The checks below then run without
    # a token rather than skipping, which is what makes a local run useful.
    no_auth: bool = False
    environment: str = ""

    # -- plumbing ------------------------------------------------------------

    def request(
        self,
        url: str,
        *,
        method: str = "GET",
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = TIMEOUT,
    ) -> tuple[int, bytes, dict[str, str]]:
        """Any HTTP call, with the status kept rather than raised.

        A 401 and a 404 are answers this script is often *hoping* for, so an
        exception for them would be the wrong shape.
        """
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("User-Agent", USER_AGENT)
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                return response.status, response.read(), lowercase(response.headers)
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read(), lowercase(exc.headers)

    def record(self, name: str, ok: bool | None, detail: str = "") -> None:
        self.results.append(Result(name, ok, detail))
        mark = {True: "ok  ", False: "FAIL", None: "skip"}[ok]
        print(f"  {mark}  {name}" + (f"  - {detail}" if detail else ""))

    # -- the six checks ------------------------------------------------------

    def health(self) -> None:
        """1. The health endpoint answers - and answers quickly, which is what
        says the keep-alive (T-3.11) is doing its job."""
        started = time.monotonic()
        status, body, _ = self.request(f"{self.api}/system/health")
        elapsed = time.monotonic() - started
        payload = json.loads(body or b"{}")
        # Kept because the next check depends on it: a local run has no accounts
        # at all (chapter 11), so "the library refuses a request with no token"
        # is the wrong question to ask it.
        self.environment = payload.get("environment", "")
        ok = status == 200 and payload.get("status") == "ok"
        cold = " (cold start - the keep-alive is not knocking)" if elapsed > 5 else ""
        self.record(
            "health answers",
            ok,
            f"{status} in {elapsed:.2f}s, up {payload.get('uptime_sec', 0):.0f}s{cold}",
        )

    def sign_in(self) -> None:
        """2. Signing in works and hands back a token the API accepts.

        Both halves matter: a token from the identity provider that the API
        refuses is a deployment where nobody can see their own library, and the
        two sides are configured separately.
        """
        email = os.getenv("KARUKI_SMOKE_EMAIL", "")
        password = os.getenv("KARUKI_SMOKE_PASSWORD", "")
        supabase = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_ANON_KEY", "")

        if getattr(self, "environment", "") == "local":
            # Chapter 11: the whole product runs on a machine with no accounts
            # on it, and `create_app` only refuses that in production. Running
            # this script against a local instance is worth doing - it is how
            # the *other* checks get exercised without a password, and it is
            # what would have caught three wrong field names in this file
            # before they were shipped - so this check steps aside rather than
            # failing something that is working as designed.
            self.no_auth = True
            self.record("signing in", None, "this instance has no accounts (local)")
            return

        # Whatever else happens, an unauthenticated request must be refused.
        status, body, _ = self.request(f"{self.api}/api/v1/songs")
        if status != 401:
            self.record("signing in", False, f"the library answered {status} with no token")
            return

        if not (email and password and supabase and key):
            self.record(
                "signing in",
                None,
                "no KARUKI_SMOKE_EMAIL/PASSWORD; unauthenticated is refused (401), "
                "which is as far as this can go without them",
            )
            return

        status, body, _ = self.request(
            f"{supabase}/auth/v1/token?grant_type=password",
            method="POST",
            data=json.dumps({"email": email, "password": password}).encode(),
            headers={"apikey": key, "Content-Type": "application/json"},
        )
        if status != 200:
            # Say *which* refusal. "400" is true and useless: a wrong password,
            # an unconfirmed address and a project that has run out of email
            # all land here, and they are three different afternoons.
            answer = json.loads(body or b"{}") if body[:1] == b"{" else {}
            code = answer.get("error_code") or answer.get("error") or ""
            message = answer.get("msg") or answer.get("error_description") or ""
            self.record(
                "signing in",
                False,
                f"the identity provider answered {status} {code} {message}".strip(),
            )
            return
        self.token = json.loads(body)["access_token"]

        status, body, _ = self.request(
            f"{self.api}/api/v1/songs", headers={"Authorization": f"Bearer {self.token}"}
        )
        songs = json.loads(body or b"{}").get("total", "?") if status == 200 else "?"
        self.record(
            "signing in", status == 200, f"token accepted by the API, library has {songs} song(s)"
        )

    def direct_upload(self) -> None:
        """3. The browser's upload path, which is the check that catches CORS.

        Chapter 14 singles this one out, and rightly: the ticket, the PUT
        straight to the bucket and the ingest are three services agreeing, and
        the failure looks like a broken upload form rather than like
        configuration.
        """
        if self.token is None and not self.no_auth:
            self.record("upload goes straight to storage", None, "needs a session")
            return
        song = self.song
        if song is None or not song.is_file():
            self.record("upload goes straight to storage", None, "no --song to upload")
            return

        auth = {"Authorization": f"Bearer {self.token}"} if self.token else {}

        # Chapter 14's checklist wants the quota checked "by trying to exceed
        # it" (T-3.8), and this is the cheapest honest attempt: a ticket for a
        # file nobody could store. It must be refused before a byte moves.
        status, body, _ = self.request(
            f"{self.api}/api/v1/songs/upload-url",
            method="POST",
            data=json.dumps(upload_ticket_payload("enormous.mp3", 20_000_000_000)).encode(),
            headers={**auth, "Content-Type": "application/json"},
        )
        code = json.loads(body or b"{}").get("error", {}).get("code", "")
        self.record(
            "an over-quota upload is refused",
            status in (400, 413) and bool(code),
            f"{status} {code or 'with no code'}",
        )

        status, body, _ = self.request(
            f"{self.api}/api/v1/songs/upload-url",
            method="POST",
            data=json.dumps(upload_ticket_payload(song.name, song.stat().st_size)).encode(),
            headers={**auth, "Content-Type": "application/json"},
        )
        if status not in (200, 201):
            self.record("upload goes straight to storage", False, f"no ticket: {status}")
            return
        ticket = json.loads(body)

        # The preflight the browser sends before the PUT. This is the exact
        # request that fails when the bucket has no CORS rule, and it fails
        # *before* any byte is sent - which is why it is checked on its own.
        status, _, headers = self.request(
            ticket["url"],
            method="OPTIONS",
            headers={
                "Origin": self.web,
                "Access-Control-Request-Method": ticket["method"],
            },
        )
        allowed = headers.get("access-control-allow-origin", "")
        if not allowed:
            self.record(
                "upload goes straight to storage",
                False,
                f"the bucket refused the preflight ({status}); run scripts/bucket_cors.py --apply",
            )
            return

        started = time.monotonic()
        status, _, _ = self.request(
            ticket["url"], method=ticket["method"], data=song.read_bytes(), timeout=300
        )
        if status not in (200, 201):
            self.record("upload goes straight to storage", False, f"PUT answered {status}")
            return
        seconds = time.monotonic() - started

        status, body, _ = self.request(
            f"{self.api}/api/v1/songs",
            method="POST",
            data=json.dumps(song_from_upload_payload(ticket["key"], song.name)).encode(),
            headers={**auth, "Content-Type": "application/json"},
        )
        created = json.loads(body or b"{}")
        self.song_id = created.get("song_id") or created.get("id")
        self.job_id = created.get("job_id")
        self.record(
            "upload goes straight to storage",
            status in (200, 201) and bool(self.job_id),
            f"preflight allows {allowed}, {song.stat().st_size / 1e6:.1f}MB in {seconds:.1f}s",
        )

    def progress_stream(self) -> None:
        """4. A job comes back with an id, and the progress stream moves.

        "Moves" is the word chapter 14 uses and it is the right one: the first
        message is the snapshot, and receiving it means the stream authenticated
        (which it could not between T-3.7 and T-3.11), opened, and was not eaten
        by a buffering proxy.
        """
        job_id = getattr(self, "job_id", None)
        if not job_id or (self.token is None and not self.no_auth):
            self.record("the progress stream moves", None, "needs an upload")
            return

        request = urllib.request.Request(f"{self.api}/api/v1/jobs/{job_id}/events")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        request.add_header("Accept", "text/event-stream")
        request.add_header("User-Agent", USER_AGENT)
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as stream:  # noqa: S310
                first = ""
                while "\n\n" not in first and time.monotonic() - started < 30:
                    chunk = stream.read1(256).decode("utf-8", "replace")
                    if not chunk:
                        break
                    first += chunk
        except urllib.error.HTTPError as exc:
            self.record("the progress stream moves", False, f"the stream answered {exc.code}")
            return
        self.record(
            "the progress stream moves",
            "event:" in first or "retry:" in first,
            f"first frame in {time.monotonic() - started:.2f}s",
        )

    def player_opens(self) -> None:
        """5. A song that is already processed opens, with audio behind it.

        A script cannot hear anything, and this does not pretend to: it checks
        that the song hands out signed stem links and that one of them really
        returns audio bytes from the bucket. Playback itself was measured in a
        browser in T-3.10 and is not something to assert from here.
        """
        if self.token is None and not self.no_auth:
            self.record("a processed song opens with audio", None, "needs a session")
            return
        auth = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        status, body, _ = self.request(f"{self.api}/api/v1/songs", headers=auth)
        songs = json.loads(body or b"{}").get("songs", []) if status == 200 else []
        ready = next((s for s in songs if s.get("is_playable")), None)
        if ready is None:
            self.record(
                "a processed song opens with audio", None, "no playable song in the library"
            )
            return

        status, body, _ = self.request(f"{self.api}/api/v1/songs/{ready['id']}", headers=auth)
        detail = json.loads(body or b"{}")
        stems = detail.get("stems", [])
        if status != 200 or not stems:
            self.record("a processed song opens with audio", False, f"song detail {status}")
            return

        url = stems[0].get("url", "")
        signed = "sig=" in url or "X-Amz-Signature=" in url
        status, audio, headers = self.request(url)
        self.record(
            "a processed song opens with audio",
            status == 200 and len(audio) > 1000 and signed,
            f"{len(stems)} stems, first is {len(audio) / 1e6:.1f}MB of "
            f"{headers.get('content-type', '?')}, link is {'signed' if signed else 'UNSIGNED'}",
        )

    def deliberate_error(self) -> None:
        """6. The deliberate error, which is the only way to know reporting works.

        The 404s are checked first and are worth as much as the 500: a probe
        that anybody can fire is a way to spend a monthly error quota.
        """
        probe = f"{self.api}/api/v1/system/error"
        closed = self.request(probe)[0], self.request(f"{probe}?token=wrong")[0]
        if closed != (404, 404):
            self.record("a deliberate error reaches monitoring", False, f"probe is open: {closed}")
            return

        token = os.getenv("KARUKI_ERROR_PROBE_TOKEN", "")
        if not token:
            self.record(
                "a deliberate error reaches monitoring",
                None,
                "no KARUKI_ERROR_PROBE_TOKEN; the probe is closed (404), as it should be",
            )
            return

        status, body, _ = self.request(f"{probe}?token={urllib.parse.quote(token, safe='')}")
        payload = json.loads(body or b"{}")
        request_id = payload.get("request_id", "")
        self.record(
            "a deliberate error reaches monitoring",
            status == 500 and bool(request_id),
            f"{status}, request_id {request_id[:8]}… - look for that tag in Sentry",
        )

    def web_app(self) -> None:
        """Not one of the six, and thirty seconds well spent: the address a
        person actually types. T-3.10 shipped a build with none of its
        variables in it while every `curl` against the API passed."""
        status, body, _ = self.request(f"{self.web}/he")
        html = body.decode("utf-8", "replace")
        self.record(
            "the web app serves its Hebrew page",
            status == 200 and 'lang="he"' in html and 'dir="rtl"' in html,
            f"{status}, {len(body) / 1024:.0f}KB",
        )

    def run(self) -> int:
        print(f"api  {self.api}\nweb  {self.web}\n")
        for check in (
            self.health,
            self.web_app,
            self.sign_in,
            self.direct_upload,
            self.progress_stream,
            self.player_opens,
            self.deliberate_error,
        ):
            try:
                check()
            except Exception as exc:  # noqa: BLE001 - a check that crashes is a check that failed
                self.record(check.__name__, False, f"{type(exc).__name__}: {exc}")

        passed = sum(1 for r in self.results if r.ok is True)
        failed = [r for r in self.results if r.ok is False]
        skipped = [r for r in self.results if r.ok is None]
        print(f"\n{passed} passed, {len(failed)} failed, {len(skipped)} skipped")
        if failed:
            print("\nChapter 14: roll back, do not fix forward.")
        return 1 if failed else 0


def default_song() -> Path | None:
    """Any small audio file lying around, preferring the smallest."""
    candidates = [p for p in (ROOT / "input").glob("*.mp3") if p.stat().st_size < 8_000_000]
    return min(candidates, key=lambda p: p.stat().st_size, default=None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=os.getenv("KARUKI_SMOKE_API", DEFAULT_API))
    parser.add_argument("--web", default=os.getenv("KARUKI_SMOKE_WEB", DEFAULT_WEB))
    parser.add_argument("--song", type=Path, default=None, help="audio file for the upload check")
    args = parser.parse_args()

    trust_system_certificates()
    for line in (
        (ROOT / ".env").read_text(encoding="utf-8").splitlines()
        if (ROOT / ".env").is_file()
        else []
    ):
        if "=" in line and not line.strip().startswith("#"):
            name, _, value = line.partition("=")
            os.environ.setdefault(name.strip(), value.strip())

    return Smoke(
        api=args.api.rstrip("/"), web=args.web.rstrip("/"), song=args.song or default_song()
    ).run()


if __name__ == "__main__":
    raise SystemExit(main())
