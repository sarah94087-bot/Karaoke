"""The open synchronised-lyrics database, behind one call (T-2.2, D-08).

Chapter 3's third principle: every external service is wrapped in a thin layer,
one call from the rest of the code, so that the day a provider changes its terms
is a day one file changes. Phase 0 spent that day twice already.

The backend is **LRCLIB**: no account, no API key, and therefore no card, which
chapter 1 makes non-negotiable. It answers with LRC text - the same format every
other lyrics database speaks - so a second backend is a `search` method and not
a new parser.

Nothing here decides *whether* a result is the right song. That is
`packages/lyrics/matching.py`, kept apart because it is the part worth testing
against a hundred awkward filenames without a network.
"""

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("karuki.lyrics.catalogue")

LRCLIB_URL = "https://lrclib.net/api/search"
# LRCLIB asks callers to identify themselves rather than blend into the crowd,
# which is a fair price for a free service with no key.
USER_AGENT = "karuki/0.1.0 (https://github.com/sarah94087-bot/Karaoke)"
# One HTTP call inside a job that has already spent a minute separating. Short
# enough that a slow database never becomes the reason a song takes longer.
TIMEOUT_SECONDS = float(os.getenv("KARUKI_LYRICS_TIMEOUT", "8"))
# The database returns its best guesses first; a match this far down the list is
# not going to be right, and each one costs a scoring pass.
MAX_CANDIDATES = 20


class CatalogueError(RuntimeError):
    """The lookup failed. Never fatal.

    Chapter 7's rule for transcription applies here for the same reason: a song
    you can sing over is not a broken song, and a lyrics database being down at
    the wrong minute must not turn a working separation into a failed job.
    """


@dataclass(frozen=True)
class Candidate:
    """One row from the database, before anyone has decided it is the song."""

    title: str
    artist: str | None
    album: str | None
    duration_sec: float | None
    # LRC text. `None` when the database has the words but nobody has timed them,
    # which for this task is the same as not having them (T-2.10 is where
    # untimed words get a home).
    synced_lyrics: str | None
    instrumental: bool
    remote_id: str
    provider: str

    @property
    def is_usable(self) -> bool:
        return bool(self.synced_lyrics) and not self.instrumental


class LyricsCatalogue(Protocol):
    """What the rest of the code may assume about a lyrics database."""

    name: str

    def search(self, title: str, artist: str | None = None) -> list[Candidate]:
        """Candidates, best-first as the database ranks them. Never raises for
        "nothing found" - that is an empty list."""
        ...


class NoCatalogue:
    """No database at all, for a deployment that would rather not call out.

    Not an error case: the pipeline treats "no match" and "no catalogue" the
    same, because the song still plays and the editor still opens.
    """

    name = "none"

    def search(self, title: str, artist: str | None = None) -> list[Candidate]:
        return []


class LrclibCatalogue:
    """LRCLIB over its public search endpoint.

    `urllib` rather than a client library: this is one GET, and the API image
    has to stay small enough for a free tier. The one thing worth importing is
    `truststore`, and only when it happens to be installed - see below.
    """

    name = "lrclib"

    def __init__(self, url: str = LRCLIB_URL, timeout: float = TIMEOUT_SECONDS) -> None:
        self.url = url
        self.timeout = timeout

    def search(self, title: str, artist: str | None = None) -> list[Candidate]:
        query = {"track_name": title}
        if artist:
            query["artist_name"] = artist

        payload = self._get(f"{self.url}?{urllib.parse.urlencode(query)}")
        if not isinstance(payload, list):
            raise CatalogueError(f"{self.name} answered with {type(payload).__name__}, not a list")

        return [self._candidate(row) for row in payload[:MAX_CANDIDATES] if isinstance(row, dict)]

    def _get(self, url: str) -> object:
        _trust_the_machines_certificates()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310 - https, built above
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                # LRCLIB's "no such track", which is an answer and not a fault.
                return []
            raise CatalogueError(f"{self.name} answered {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            raise CatalogueError(f"{self.name} could not be reached: {exc}") from exc

    def _candidate(self, row: dict) -> Candidate:
        return Candidate(
            title=str(row.get("trackName") or ""),
            artist=row.get("artistName") or None,
            album=row.get("albumName") or None,
            duration_sec=row.get("duration"),
            synced_lyrics=row.get("syncedLyrics") or None,
            instrumental=bool(row.get("instrumental")),
            remote_id=str(row.get("id") or ""),
            provider=self.name,
        )


def _trust_the_machines_certificates() -> None:
    """Use the operating system's certificate store when we can.

    This machine runs TLS inspection: an antivirus re-signs HTTPS traffic, and
    the bundle Python ships with rejects the chain with "self-signed certificate
    in certificate chain". The Windows store already trusts that root. Optional
    on purpose - the container is Linux, has no inspection, and does not need
    the dependency in its image.
    """
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()


BACKENDS: dict[str, type] = {
    "lrclib": LrclibCatalogue,
    "none": NoCatalogue,
}


def get_catalogue(backend: str = "lrclib") -> LyricsCatalogue:
    """LRCLIB by default, unlike the separator.

    The reasoning that keeps `local` separation the default - a stray run spends
    real credit - does not apply here: this is a free read of a public database,
    and skipping it means transcribing a song somebody already timed by hand.
    """
    try:
        return BACKENDS[backend]()
    except KeyError:
        raise CatalogueError(
            f"unknown lyrics catalogue {backend!r}; expected one of {sorted(BACKENDS)}"
        ) from None
