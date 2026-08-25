"""T-4.1: the import module, and the flag that makes it not be there.

Three things are being pinned here, and only one of them is "does the download
work".

The first is the **flag**. Phase 4 is specified as a module that can be switched
off, so "off" has to be a state with a definition: no resolvers, an importer
that says so rather than one that half-works, and - in `test_import_api.py` -
no route.

The second is **whose address may be fetched**. An API that fetches what it is
told is a way to read what only it can reach: the cloud provider's metadata
service, a database on a private address, the API itself. These tests are the
reason `check_url` exists, and they cover the redirect as well as the link,
because a public URL that redirects to 127.0.0.1 is the ordinary way that check
is got around.

The third is that **no test here touches the network**, the same rule the GPU
and the transcription service have. Every address is an IP literal, so name
resolution is arithmetic rather than a DNS query, and the one test that reads a
body replaces the opener.
"""

import sys
import types
from pathlib import Path

import pytest

from packages.providers import import_source
from packages.providers.import_source import (
    DirectAudio,
    SourceError,
    SourceUnavailable,
    YtDlp,
    check_url,
    get_importer,
)

# example.com. A literal so that `getaddrinfo` answers from the string.
PUBLIC = "93.184.216.34"


# -- the flag ----------------------------------------------------------------


@pytest.mark.parametrize("configured", ["none", "", "   ", "none,none"])
def test_the_flag_turns_the_whole_module_off(configured: str):
    importer = get_importer(configured)

    assert importer.enabled is False
    assert importer.names == []


def test_an_importer_that_is_off_says_so_rather_than_failing_obscurely(tmp_path: Path):
    """`import_disabled` is its own code with its own sentence. A user on a
    deployment with the feature off should never reach this - the route is not
    registered - so anything that does get here is a bug, and it should read
    like a configuration and not like a broken link."""
    with pytest.raises(SourceUnavailable) as raised:
        get_importer("none").fetch("https://example.com/a.mp3", tmp_path, 1000)

    assert raised.value.code == "import_disabled"


def test_direct_is_the_default():
    assert get_importer().names == ["direct"]


def test_yt_dlp_has_to_be_asked_for_by_name():
    """The same rule the `modal` separator has, for a different reason: a large
    dependency that reads other people's sites is a decision, not a default."""
    assert "yt-dlp" not in get_importer().names
    assert get_importer("direct,yt-dlp").names == ["direct", "yt-dlp"]


def test_an_unknown_resolver_refuses_rather_than_meaning_off():
    """A variable that silently does nothing has already cost this project two
    deployments (T-3.10). A typo here stops the service instead."""
    with pytest.raises(SourceUnavailable) as raised:
        get_importer("direct,yt_dlp")

    assert raised.value.code == "import_unavailable"


# -- which resolver takes the link -------------------------------------------


def test_a_file_link_goes_to_direct_and_a_page_goes_to_yt_dlp():
    both = get_importer("direct,yt-dlp")

    assert both.sources[0].handles("https://example.com/song.mp3") is True
    assert both.sources[0].handles("https://example.com/watch?v=abc") is False
    assert both.sources[1].handles("https://example.com/watch?v=abc") is True


def test_with_only_direct_switched_on_it_still_gets_a_link_with_no_suffix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Declining is how the resolvers express a preference between themselves,
    not a refusal to try: plenty of real audio links have no suffix at all."""
    taken: list[str] = []
    monkeypatch.setattr(
        DirectAudio,
        "fetch",
        lambda self, url, into, max_bytes: taken.append(url),  # type: ignore[misc]
    )

    get_importer("direct").fetch(f"https://{PUBLIC}/stream", tmp_path, 1000)

    assert taken == [f"https://{PUBLIC}/stream"]


# -- whose address may be fetched --------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/song.mp3",
        "file:///etc/passwd",
        "gopher://example.com/",
        "https:///song.mp3",
    ],
)
def test_only_http_links_are_accepted(url: str):
    with pytest.raises(SourceError) as raised:
        check_url(url)

    assert raised.value.code == "import_unsupported_url"


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",  # the API itself
        "169.254.169.254",  # the cloud metadata service
        "10.0.0.5",  # a private network
        "192.168.1.1",
        "[::1]",
        "0.0.0.0",
    ],
)
def test_an_address_that_is_not_on_the_public_internet_is_refused(host: str):
    with pytest.raises(SourceError) as raised:
        check_url(f"http://{host}/song.mp3")

    assert raised.value.code == "import_forbidden_address"


def test_a_public_address_is_allowed():
    assert check_url(f"https://{PUBLIC}/song.mp3").hostname == PUBLIC


def test_every_hop_of_a_redirect_is_checked_too():
    """urllib follows redirects itself and the caller only sees the last
    response, so a check that runs once runs on the wrong URL."""
    handler = import_source._CheckedRedirects()

    with pytest.raises(SourceError) as raised:
        handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:8000/admin")

    assert raised.value.code == "import_forbidden_address"


# -- reading the bytes -------------------------------------------------------


class FakeResponse:
    """Enough of an HTTP response for `DirectAudio.fetch`, and nothing else."""

    def __init__(self, body: bytes, headers: dict[str, str]):
        self._body = body
        self._at = 0
        self.headers = headers

    def read(self, size: int = -1) -> bytes:
        chunk = self._body[self._at : self._at + size] if size > 0 else self._body[self._at :]
        self._at += len(chunk)
        return chunk

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc: object) -> None:
        return None


def serve(monkeypatch: pytest.MonkeyPatch, body: bytes, **headers: str) -> None:
    class FakeOpener:
        def open(self, request: object, timeout: float | None = None) -> FakeResponse:
            return FakeResponse(body, headers)

    monkeypatch.setattr(import_source, "_opener", FakeOpener)


def test_a_direct_link_is_downloaded_and_named_by_its_type(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    serve(monkeypatch, b"ID3 pretend audio", **{"Content-Type": "audio/mpeg"})

    imported = DirectAudio().fetch(f"https://{PUBLIC}/track", tmp_path, 1_000_000)

    assert imported.suffix == ".mp3"
    assert imported.path.read_bytes() == b"ID3 pretend audio"
    assert imported.provider == "direct"
    assert imported.source_url == f"https://{PUBLIC}/track"


def test_the_suffix_falls_back_to_the_address_when_the_type_is_useless(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Plenty of servers answer `application/octet-stream` for a file they are
    perfectly happy to name `.mp3`. The suffix is not cosmetic: ffmpeg is told
    the format by the file name, and T-3.10 paid for an extensionless copy
    once already."""
    serve(monkeypatch, b"audio", **{"Content-Type": "application/octet-stream"})

    imported = DirectAudio().fetch(f"https://{PUBLIC}/a/track.flac", tmp_path, 1_000_000)

    assert imported.suffix == ".flac"


def test_something_that_is_not_audio_at_all_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    serve(monkeypatch, b"<html>", **{"Content-Type": "text/html"})

    with pytest.raises(SourceError) as raised:
        DirectAudio().fetch(f"https://{PUBLIC}/page", tmp_path, 1_000_000)

    assert raised.value.code == "import_not_audio"


def test_the_size_limit_is_enforced_on_the_bytes_and_not_on_the_claim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """A `Content-Length` is a claim, and this one is a stranger's. The same
    rule the upload has (T-1.5)."""
    serve(
        monkeypatch,
        b"x" * 5000,
        **{"Content-Type": "audio/mpeg", "Content-Length": "10"},
    )

    with pytest.raises(SourceError) as raised:
        DirectAudio().fetch(f"https://{PUBLIC}/big.mp3", tmp_path, 1000)

    assert raised.value.code == "import_too_large"
    assert list(tmp_path.iterdir()) == [], "the part that arrived should not be left behind"


def test_a_declared_size_over_the_limit_is_refused_before_the_transfer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    serve(
        monkeypatch,
        b"x" * 10,
        **{"Content-Type": "audio/mpeg", "Content-Length": "99999999"},
    )

    with pytest.raises(SourceError) as raised:
        DirectAudio().fetch(f"https://{PUBLIC}/big.mp3", tmp_path, 1000)

    assert raised.value.code == "import_too_large"


def test_an_empty_body_is_not_a_song(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    serve(monkeypatch, b"", **{"Content-Type": "audio/mpeg"})

    with pytest.raises(SourceError) as raised:
        DirectAudio().fetch(f"https://{PUBLIC}/nothing.mp3", tmp_path, 1000)

    assert raised.value.code == "import_not_audio"


# -- the title ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://{PUBLIC}/songs/Hallelujah.mp3", "Hallelujah"),
        (f"https://{PUBLIC}/songs/night_train_to_cairo.mp3", "night train to cairo"),
        # A Hebrew file name in an address is percent escaped, and
        # `%D7%A9%D7%99%D7%A8` is not a title anybody wants in a library.
        (f"https://{PUBLIC}/%D7%A9%D7%99%D7%A8%20%D7%A9%D7%9C%D7%99.mp3", "שיר שלי"),
        (f"https://{PUBLIC}/", "ללא שם"),
    ],
)
def test_the_title_is_the_file_name_in_the_address(url: str, expected: str):
    assert import_source._title_from(url) == expected


# -- yt-dlp ------------------------------------------------------------------


def test_yt_dlp_without_yt_dlp_installed_is_unavailable_and_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """T-1.7's distinction, again: the operator's problem is not the link's
    problem, and a user told to try a different address cannot fix this one."""
    monkeypatch.setitem(__import__("sys").modules, "yt_dlp", None)

    with pytest.raises(SourceUnavailable) as raised:
        YtDlp().fetch(f"https://{PUBLIC}/watch?v=abc", tmp_path, 1000)

    assert raised.value.code == "import_unavailable"


def _fake_yt_dlp(written: int, declared: int | None, name: str = "original.webm"):
    """A stand-in for the real library. No test here touches the network.

    It writes `written` bytes where yt-dlp would have written the recording,
    and reports `declared` as the size of the stream it chose - which is the
    pair that the guard reads.
    """

    class Reader:
        def __init__(self, options: dict[str, object]):
            self.options = options

        def __enter__(self) -> "Reader":
            return self

        def __exit__(self, *_: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict[str, object]:
            template = str(self.options["outtmpl"])
            self.path = Path(template.replace("%(ext)s", name.rsplit(".", 1)[1]))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_bytes(b"x" * written)
            info: dict[str, object] = {
                "title": "בלוז כנעני",
                "uploader": "somebody",
                "duration": 288,
                "webpage_url": url,
                "format_id": "251",
            }
            if declared is not None:
                info["filesize"] = declared
            return info

        def prepare_filename(self, info: dict[str, object]) -> str:
            return str(self.path)

    return types.SimpleNamespace(YoutubeDL=Reader)


def test_a_link_that_hands_over_a_fragment_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Measured on a real YouTube link on 2026-08-25: yt-dlp chose a 4.95MB
    stream, reported success, and wrote 145,107 bytes - a stub the service
    hands to a client it will not serve. Nothing downstream could catch it;
    ffmpeg normalises a stub happily and the result is a five second song that
    has cost a GPU separation to make."""
    monkeypatch.setitem(sys.modules, "yt_dlp", _fake_yt_dlp(written=145_107, declared=4_949_491))

    with pytest.raises(SourceError) as raised:
        YtDlp().fetch(f"https://{PUBLIC}/watch?v=abc", tmp_path, 40 * 1024 * 1024)

    assert raised.value.code == "import_incomplete"
    # And the fragment does not sit in the staging directory afterwards.
    assert list(tmp_path.iterdir()) == []


def test_a_complete_download_is_kept(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setitem(sys.modules, "yt_dlp", _fake_yt_dlp(written=4_900_000, declared=4_949_491))

    imported = YtDlp().fetch(f"https://{PUBLIC}/watch?v=abc", tmp_path, 40 * 1024 * 1024)

    assert imported.path.is_file()
    assert imported.duration_sec == 288


def test_a_size_the_library_does_not_know_is_not_evidence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """An unknown size lets the file through. Blocking on a guess would refuse
    real recordings for a number nobody supplied."""
    monkeypatch.setitem(sys.modules, "yt_dlp", _fake_yt_dlp(written=1_000, declared=None))

    assert YtDlp().fetch(f"https://{PUBLIC}/watch?v=abc", tmp_path, 40 * 1024 * 1024).path.is_file()


def test_yt_dlp_asks_for_the_system_certificates_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """The direct resolver has always done this and this one had not, which on
    a machine that re-signs HTTPS fails with "self-signed certificate in
    certificate chain" - a message that reads like a broken network."""
    asked: list[bool] = []
    monkeypatch.setattr(import_source, "trust_system_certificates", lambda: asked.append(True))
    monkeypatch.setitem(sys.modules, "yt_dlp", _fake_yt_dlp(written=4_900_000, declared=4_949_491))

    YtDlp().fetch(f"https://{PUBLIC}/watch?v=abc", tmp_path, 40 * 1024 * 1024)

    assert asked == [True]
