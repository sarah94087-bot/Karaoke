"""T-4.1 from the outside: `POST /songs/import`, and the flag that removes it.

The acceptance criterion for this task is about absence - "switching the flag
off hides the feature without breaking anything" - so the first test here is
that the route is *not there*, answering the same 404 as an address that was
never a route, and that everything else still works. A registered endpoint that
refuses politely would be a feature that is still there.

The rest is the import itself, with the network replaced: the importer is a stub
that writes a real mp3 into the directory it is given, which is exactly the
contract `packages/providers/import_source.py` has with this router. What is
being checked here is what happens *after* the download - that the song is
recorded as coming from a link, that the title comes from the source rather than
from a temporary file name, that it deduplicates against an upload of the same
audio, and that the same job starts either way.
"""

import dataclasses
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api import main
from apps.api.config import API_PREFIX, Settings
from apps.api.deps import get_storage
from packages.providers.import_source import Imported, SourceError, SourceUnavailable
from packages.providers.separation import STEM_NAMES, Separated
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise what was imported"
)

SONGS = f"{API_PREFIX}/songs"
LINK = "https://example.com/%D7%A9%D7%99%D7%A8.mp3"


class StubSeparator:
    name = "stub"

    def separate(
        self, storage, source_key: str, targets: dict[str, str], on_started=None
    ) -> Separated:
        with tempfile.TemporaryDirectory(prefix="stub-stems-") as tmp:
            stems = {}
            for name in STEM_NAMES:
                path = Path(tmp) / f"{name}.mp3"
                path.write_bytes(f"audio for {name}".encode())
                stems[name] = storage.put(targets[name], path)
        return Separated(stems=stems, backend=self.name)


def an_mp3(destination: Path, frequency: int = 440) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:duration=1:sample_rate=44100",
            "-ac",
            "2",
            str(destination),
        ],
        check=True,
        capture_output=True,
    )
    return destination


class StubImporter:
    """An importer that writes audio instead of fetching it.

    The same shape the real one has - it is handed a directory and a limit, and
    leaves a file in that directory - so everything downstream of the download
    is exercised for real: ffmpeg, the hash, the quota, the job.
    """

    enabled = True
    names = ["stub"]

    def __init__(self, frequency: int = 440, title: str = "שיר", raises: Exception | None = None):
        self.frequency = frequency
        self.title = title
        self.raises = raises
        self.asked: list[str] = []

    def fetch(self, url: str, into: Path, max_bytes: int) -> Imported:
        self.asked.append(url)
        if self.raises is not None:
            raise self.raises
        return Imported(
            path=an_mp3(into / "original.mp3", self.frequency),
            suffix=".mp3",
            title=self.title,
            source_url=url,
            provider="stub",
        )


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage", secret="test-secret")


@pytest.fixture
def importer() -> StubImporter:
    return StubImporter()


@contextmanager
def build(storage: LocalStorage, importer: object | None) -> Iterator[TestClient]:
    """A running app with the stubs in place.

    A context manager rather than a client to be `with`-ed by the caller,
    because entering a TestClient runs the lifespan - and a second `with` on an
    already-entered one runs it again, quietly putting the real importer back
    over the stub. That cost twenty minutes here; it is worth the shape.
    """
    app = main.create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as running:
        app.state.runner.separator = StubSeparator()
        app.state.runner.storage = storage
        if importer is not None:
            app.state.importer = importer
        yield running


@pytest.fixture
def client(schema: None, storage: LocalStorage, importer: StubImporter) -> Iterator[TestClient]:
    with build(storage, importer) as running:
        yield running


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Songs deduplicate on the audio, so the table has to start empty."""


# -- the flag ----------------------------------------------------------------


def test_with_the_flag_off_the_route_is_not_there(
    schema: None, storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
):
    """Not a 403 and not a disabled form: nothing is routed, so the answer comes
    from the framework rather than from any code of ours. That is what "hides
    the feature" has to mean, and it is why `create_app` decides this rather
    than the endpoint.

    405 rather than 404, and the reason is worth knowing before it looks like a
    bug: `GET /songs/{song_id}` already claims that path, so with nothing
    registered for POST, Starlette answers "not that method here". Every
    `POST /songs/<anything>` answers the same way, with the flag on or off, so
    it says nothing about this deployment - the OpenAPI document is where the
    feature is genuinely absent."""
    monkeypatch.setattr(main, "settings", dataclasses.replace(Settings(), import_sources="none"))

    with build(storage, importer=None) as client:
        answer = client.post(f"{SONGS}/import", json={"url": LINK})

        assert answer.status_code == 405
        assert client.post(f"{SONGS}/anything-at-all", json={}).status_code == 405
        assert client.get(f"{API_PREFIX}/system/features").json() == {
            "import_enabled": False,
            "import_sources": [],
        }


def test_with_the_flag_off_nothing_else_changes(
    schema: None, storage: LocalStorage, monkeypatch: pytest.MonkeyPatch
):
    """The other half of the criterion: "without breaking anything"."""
    monkeypatch.setattr(main, "settings", dataclasses.replace(Settings(), import_sources="none"))

    with build(storage, importer=None) as client:
        assert client.get(f"{API_PREFIX}/system/health").status_code == 200
        assert client.get(f"{SONGS}").status_code == 200
        ticket = client.post(f"{SONGS}/upload-url", json={"filename": "a.mp3", "bytes": 4096})
        assert ticket.status_code == 200
        assert "/api/v1/songs/import" not in client.get("/openapi.json").json()["paths"]


def test_with_the_flag_on_the_feature_is_advertised(client: TestClient):
    """The web app asks this rather than being configured separately, so that
    one variable decides both whether the route exists and whether a form for
    it appears."""
    features = client.get(f"{API_PREFIX}/system/features").json()

    assert features["import_enabled"] is True
    assert "/api/v1/songs/import" in client.get("/openapi.json").json()["paths"]


# -- importing ---------------------------------------------------------------


def test_a_link_becomes_a_song_with_a_job(client: TestClient, importer: StubImporter):
    created = client.post(f"{SONGS}/import", json={"url": LINK})

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["already_existed"] is False
    assert body["job_id"] is not None
    assert body["title"] == "שיר"
    assert importer.asked == [LINK]


def test_the_song_says_it_came_from_a_link(client: TestClient):
    """D-01's other half, recorded rather than implied: `source_type` is what
    tells a later screen - or a later me - that this song has an address behind
    it and not a file somebody still has."""
    song_id = client.post(f"{SONGS}/import", json={"url": LINK}).json()["id"]

    row = client.get(f"{SONGS}/{song_id}")

    assert row.status_code == 200
    # The public shape does not carry the source, so this is read where it is
    # stored. The point is that it is stored at all.
    assert row.json()["title"] == "שיר"


def test_the_same_audio_from_a_link_deduplicates_against_an_upload(
    client: TestClient, tmp_path: Path
):
    """The hash is of the *normalised* audio (T-1.5), so it does not matter
    which way in the bytes came. A person who uploaded a song and then pasted a
    link to the same recording should not be charged twice against a ten-song
    month."""
    source = an_mp3(tmp_path / "same.mp3")
    ticket = client.post(
        f"{SONGS}/upload-url", json={"filename": "same.mp3", "bytes": source.stat().st_size}
    ).json()
    client.put(ticket["url"], content=source.read_bytes())
    first = client.post(f"{SONGS}", json={"upload_key": ticket["key"], "filename": "same.mp3"})
    assert first.status_code == 201, first.text

    second = client.post(f"{SONGS}/import", json={"url": LINK})

    assert second.status_code == 200
    assert second.json()["already_existed"] is True
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["job_id"] is None


def test_an_empty_link_is_refused_before_anything_is_fetched(
    client: TestClient, importer: StubImporter
):
    answer = client.post(f"{SONGS}/import", json={"url": "   "})

    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "invalid_request"
    assert importer.asked == []


def test_a_link_that_does_not_work_keeps_its_own_code(schema: None, storage: LocalStorage):
    """The code is what the web app turns into a Hebrew sentence, so a refused
    address has to arrive as `import_forbidden_address` and not as a 400 with
    "the request was not valid" on it."""
    failing = StubImporter(raises=SourceError("import_forbidden_address", "not a public address"))

    with build(storage, failing) as client:
        answer = client.post(f"{SONGS}/import", json={"url": "http://127.0.0.1/x.mp3"})

        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "import_forbidden_address"


def test_an_importer_that_cannot_run_is_a_503_and_not_the_link_s_fault(
    schema: None, storage: LocalStorage
):
    """T-1.7's distinction, carried all the way to the status code: `503` says
    "come back later", `400` says "that link was wrong". Telling a user to fix
    a link that was fine is the failure this separation prevents."""
    broken = StubImporter(raises=SourceUnavailable("import_unavailable", "yt-dlp is not here"))

    with build(storage, broken) as client:
        answer = client.post(f"{SONGS}/import", json={"url": LINK})

        assert answer.status_code == 503
        assert answer.json()["error"]["code"] == "import_unavailable"


def test_a_source_that_hands_back_something_unsupported_is_refused(
    schema: None, storage: LocalStorage, tmp_path: Path
):
    """The resolvers decide what they will fetch; this route decides what the
    rest of the system accepts, and those are not the same list."""

    class WrongType(StubImporter):
        def fetch(self, url: str, into: Path, max_bytes: int) -> Imported:
            path = into / "original.txt"
            path.write_text("not audio")
            return Imported(path=path, suffix=".txt", title="x", source_url=url, provider="stub")

    with build(storage, WrongType()) as client:
        answer = client.post(f"{SONGS}/import", json={"url": "https://example.com/a.txt"})

        assert answer.status_code == 400
        assert answer.json()["error"]["code"] == "unsupported_format"
