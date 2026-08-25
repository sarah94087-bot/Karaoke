"""Getting a song from a link instead of from a file (D-01, phase 4).

D-01 is "both, starting from a file": the file was phase 1, and this is the
other half. It is deliberately the last thing built and the first thing that can
be switched off - `KARUKI_IMPORT=none` and the feature is not there, which is
what T-4.1 is for. The rest of the product does not import this module, does not
branch on it, and does not change when it is off.

Two resolvers behind one protocol, and the difference between them is the whole
reason the flag exists:

* **`direct`** takes a plain link to an audio file. No dependency, no account,
  nothing anybody's terms of service have an opinion about. On by default, for
  the reason LRCLIB is on by default (T-2.2): a free fetch of a public address
  is not a decision that has to be made twice.
* **`yt-dlp`** takes a video link. It is a large dependency, it breaks when a
  site changes, and datacentre addresses are routinely refused by the sites it
  reads - so it is **off by default and has to be asked for by name**, the same
  rule the `modal` separator has for a different reason.

The dangerous part of accepting a URL is not the download, it is *whose* address
it is. An API that fetches whatever it is told is a way to read the things only
it can reach: the cloud provider's metadata service on 169.254.169.254, a
database on a private address, or the API itself. `_public_address` is the
answer, and it is applied to every hop of a redirect rather than only to what
the user typed - a public URL that redirects to 127.0.0.1 is the ordinary way
that check is defeated.
"""

import ipaddress
import logging
import os
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Protocol

from packages.providers.net import USER_AGENT, trust_system_certificates

log = logging.getLogger("karuki.import")

# Long enough for a slow host to hand over eight minutes of audio, short enough
# that a link that will never answer does not hold a request open all afternoon.
TIMEOUT_SECONDS = float(os.getenv("KARUKI_IMPORT_TIMEOUT", "60"))

CHUNK_BYTES = 256 * 1024
MAX_REDIRECTS = 5

# What the bytes are, by what the server says they are. The suffix matters
# downstream: ffmpeg is told the format by the file name, and T-3.10 already
# paid for an extensionless copy once - Groq answered it with a list of the
# types it accepts instead of a transcript.
AUDIO_SUFFIXES = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "audio/opus": ".opus",
    "video/mp4": ".mp4",
}

# Only for naming the file when the server says nothing useful about the type.
URL_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".mp4"}


class SourceError(RuntimeError):
    """The link could not be turned into audio.

    Carries a code, because chapter 9's rule holds here as everywhere: the user
    sees a Hebrew sentence about what went wrong, not a stack trace and not a
    silence.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SourceUnavailable(SourceError):
    """The importer itself cannot run - no backend, or its dependency is absent.

    Deliberately distinct from a link that did not work, the same way
    `separation_unavailable` is distinct from `separation_failed` (T-1.7): one
    is the operator's problem and the other is the link's, and telling a user to
    try again with a different address is only useful when it is the latter.
    """


@dataclass(frozen=True)
class Imported:
    """A file on this machine, and what the source said about it.

    `title` and `artist` are what the source claims, not what anybody has
    checked. T-4.2 is where they reach a screen that lets a person disagree.
    """

    path: Path
    suffix: str
    title: str
    artist: str | None = None
    duration_sec: float | None = None
    source_url: str = ""
    provider: str = ""


class ImportSource(Protocol):
    """What the API may assume about a way in from a link."""

    name: str

    def handles(self, url: str) -> bool:
        """True when this resolver is willing to try. Cheap, no network."""
        ...

    def fetch(self, url: str, into: Path, max_bytes: int) -> Imported:
        """Download to a new file inside `into`, or raise `SourceError`."""
        ...


# -- what is allowed to be fetched -------------------------------------------


def _public_address(host: str) -> None:
    """Refuse anything that is not on the public internet.

    Every address the name resolves to has to be global, not just the first:
    a name with both a public and a loopback record would otherwise be a coin
    toss. Honest limit, worth writing down rather than implying: urllib resolves
    the name again when it connects, so a name that answers differently a
    moment later is not caught here. Closing that properly means connecting to a
    pinned address ourselves, which is a socket layer this project does not have
    - and the realistic attacker on a private site is a pasted link, not someone
    running their own DNS.
    """
    try:
        addresses = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SourceError("import_unreachable", f"cannot resolve {host}") from exc

    for *_, sockaddr in addresses:
        address = ipaddress.ip_address(sockaddr[0])
        if not address.is_global:
            raise SourceError(
                "import_forbidden_address",
                f"{host} resolves to {address}, which is not a public address",
            )


def check_url(url: str) -> urllib.parse.ParseResult:
    """The scheme, the host, and where that host actually is."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SourceError("import_unsupported_url", "only http and https links can be imported")
    if not parsed.hostname:
        raise SourceError("import_unsupported_url", "that link has no host in it")
    _public_address(parsed.hostname)
    return parsed


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    """Apply `check_url` to every hop, not only to what the user typed.

    A public URL that redirects to 127.0.0.1 is the ordinary way an address
    check is defeated, and it is defeated silently: urllib follows redirects by
    itself and the caller only ever sees the last response.
    """

    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _opener() -> urllib.request.OpenerDirector:
    trust_system_certificates()
    return urllib.request.build_opener(_CheckedRedirects())


# -- the backends ------------------------------------------------------------


class DirectAudio:
    """A plain link to an audio file: `https://example.com/song.mp3`."""

    name = "direct"

    def handles(self, url: str) -> bool:
        """A link that ends in an audio file name is certainly this one's.

        Anything else - a page, or an address with no suffix at all - is only
        this one's when nothing else is switched on, which `Importer.fetch`
        decides. Plenty of real audio links have no suffix, so declining here
        is a preference between resolvers and not a refusal.
        """
        return _suffix_from(url) is not None

    def fetch(self, url: str, into: Path, max_bytes: int) -> Imported:
        check_url(url)
        request = urllib.request.Request(
            url, headers={"User-Agent": USER_AGENT, "Accept": "audio/*,*/*;q=0.8"}
        )
        try:
            response = _opener().open(request, timeout=TIMEOUT_SECONDS)
        except urllib.error.HTTPError as exc:
            raise SourceError("import_unreachable", f"the address answered {exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise SourceError("import_unreachable", f"could not read that address: {exc}") from exc

        with response:
            declared = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            suffix = AUDIO_SUFFIXES.get(declared) or _suffix_from(url)
            if suffix is None:
                raise SourceError(
                    "import_not_audio",
                    f"that address returned {declared or 'no type'}, which is not audio",
                )

            # The declared length is a claim, checked here only because
            # refusing before the transfer is cheaper than refusing after it.
            # `_stream` is what actually enforces the limit.
            length = response.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise SourceError("import_too_large", "that file is larger than we accept")

            destination = into / f"original{suffix}"
            _stream(response, destination, max_bytes)

        return Imported(
            path=destination,
            suffix=suffix,
            title=_title_from(url),
            source_url=url,
            provider=self.name,
        )


class YtDlp:
    """A video link, read with yt-dlp. Off unless asked for by name.

    Not because there is anything wrong with the tool - it is the only sensible
    way to do this - but because of what carrying it means: a large dependency
    that tracks other people's sites, in an image that has to stay deployable on
    a free tier, reading addresses whose terms are not ours to decide. On a
    machine where that is the right call it is one environment variable, and it
    is one environment variable to take back.
    """

    name = "yt-dlp"

    def handles(self, url: str) -> bool:
        """Anything the `direct` resolver has declined - in practice, a page.

        A claim about what this is *for*, rather than a list of sites, which
        would go stale within a week of being written.
        """
        return urllib.parse.urlparse(url).scheme in ("http", "https")

    def fetch(self, url: str, into: Path, max_bytes: int) -> Imported:
        check_url(url)
        try:
            import yt_dlp
        except ImportError as exc:
            raise SourceUnavailable(
                "import_unavailable",
                "KARUKI_IMPORT names yt-dlp, but yt-dlp is not installed here",
            ) from exc

        options = {
            "format": "bestaudio/best",
            "outtmpl": str(into / "original.%(ext)s"),
            "noplaylist": True,  # a link into a playlist means the one song
            "max_filesize": max_bytes,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        try:
            with yt_dlp.YoutubeDL(options) as reader:
                info = reader.extract_info(url, download=True)
                downloaded = Path(reader.prepare_filename(info))
        except Exception as exc:  # yt-dlp raises its own hierarchy
            raise SourceError("import_failed", f"could not read that link: {exc}") from exc

        if not downloaded.is_file():
            # What `max_filesize` does when the file is over the limit: it
            # declines to download and says nothing else.
            raise SourceError("import_too_large", "that recording is larger than we accept")

        return Imported(
            path=downloaded,
            suffix=downloaded.suffix.lower(),
            title=(info.get("track") or info.get("title") or _title_from(url)),
            artist=info.get("artist") or info.get("uploader"),
            duration_sec=info.get("duration"),
            source_url=info.get("webpage_url") or url,
            provider=self.name,
        )


# -- the helpers the backends share ------------------------------------------


def _stream(response: IO[bytes], destination: Path, max_bytes: int) -> int:
    """Write the body out, refusing to hold an unbounded one in memory.

    The same rule the upload has (T-1.5): the limit is enforced as the bytes
    arrive, because a `Content-Length` is a claim and this one is a stranger's.
    """
    written = 0
    with destination.open("wb") as sink:
        while chunk := response.read(CHUNK_BYTES):
            written += len(chunk)
            if written > max_bytes:
                sink.close()
                destination.unlink(missing_ok=True)
                raise SourceError("import_too_large", "that file is larger than we accept")
            sink.write(chunk)
    if written == 0:
        destination.unlink(missing_ok=True)
        raise SourceError("import_not_audio", "that address returned nothing")
    return written


def _suffix_from(url: str) -> str | None:
    suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
    return suffix if suffix in URL_SUFFIXES else None


def _title_from(url: str) -> str:
    """The file name in the address, tidied - the only title a direct link has.

    Percent-decoded first, because a Hebrew file name in a URL is percent
    escaped and `%D7%A9%D7%99%D7%A8` is not a title anybody wants in a library.
    """
    name = urllib.parse.unquote(Path(urllib.parse.urlparse(url).path).stem)
    name = re.sub(r"[_+]+", " ", name).strip()
    return name or "ללא שם"


BACKENDS: dict[str, type] = {
    DirectAudio.name: DirectAudio,
    YtDlp.name: YtDlp,
}


@dataclass
class Importer:
    """The resolvers that are switched on, in the order they are tried.

    One object rather than a list so the API has a single thing to ask "is this
    feature here at all" - and so `enabled` is false in exactly one place.
    """

    sources: list[ImportSource]

    @property
    def enabled(self) -> bool:
        return bool(self.sources)

    @property
    def names(self) -> list[str]:
        return [source.name for source in self.sources]

    def fetch(self, url: str, into: Path, max_bytes: int) -> Imported:
        """The first resolver that wants the link, or the first one there is.

        The fallback is what keeps a `direct`-only deployment able to read an
        address with no suffix on it: declining is how the resolvers express a
        preference between themselves, not a refusal to try.
        """
        if not self.sources:
            raise SourceUnavailable("import_disabled", "importing from a link is turned off here")
        chosen = next((source for source in self.sources if source.handles(url)), self.sources[0])
        log.info("importing %s with %s", url, chosen.name)
        return chosen.fetch(url, into, max_bytes)


def get_importer(configured: str = "direct") -> Importer:
    """Read the flag. `none`, empty, or nonsense off the end all mean off.

    Comma-separated and ordered, so `direct,yt-dlp` means "a plain file link is
    a plain file link; anything else is a page" - which is the order that keeps
    the heavy resolver out of the common case.
    """
    names = [name.strip() for name in configured.split(",") if name.strip()]
    if not names or names == ["none"]:
        return Importer(sources=[])

    sources: list[ImportSource] = []
    for name in names:
        if name == "none":
            continue
        try:
            sources.append(BACKENDS[name]())
        except KeyError:
            raise SourceUnavailable(
                "import_unavailable",
                f"unknown import source {name!r}; expected some of {sorted(BACKENDS)}",
            ) from None
    return Importer(sources=sources)
