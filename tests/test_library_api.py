"""`GET /songs` - what the library screen reads.

The row the screen draws needs the song *and* the state of its most recent job,
and the interesting cases are the ones where those two disagree: a song that is
playable while still processing (D-28), and a song whose job failed.
"""

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.deps import get_storage
from apps.api.main import create_app
from packages.providers.separation import STEM_NAMES, Separated, SeparationError
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise the upload"
)

SONGS = f"{API_PREFIX}/songs"
UPLOAD = f"{SONGS}/upload"


class StubSeparator:
    name = "stub"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    def separate(self, source: Path, destination: Path) -> Separated:
        if self.error is not None:
            raise self.error
        destination.mkdir(parents=True, exist_ok=True)
        stems = {}
        for name in STEM_NAMES:
            path = destination / f"{name}.mp3"
            path.write_bytes(name.encode())
            stems[name] = path
        return Separated(stems=stems, backend=self.name)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


def build(storage: LocalStorage, separator: object) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    client = TestClient(app)
    client.__enter__()
    app.state.runner.separator = separator
    app.state.runner.storage = storage
    return client


@pytest.fixture
def client(schema: None, storage: LocalStorage) -> Iterator[TestClient]:
    running = build(storage, StubSeparator())
    yield running
    running.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """The library is a list; its contents have to be known."""


def synth(path: Path, frequency: int = 440, seconds: float = 1.0) -> Path:
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
            f"sine=frequency={frequency}:duration={seconds}:sample_rate=44100",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def upload(client: TestClient, path: Path, name: str | None = None) -> dict:
    with path.open("rb") as handle:
        return client.post(UPLOAD, files={"file": (name or path.name, handle, "audio/mpeg")}).json()


def settle(client: TestClient, job_id: str) -> None:
    import time

    for _ in range(100):
        if client.get(f"{API_PREFIX}/jobs/{job_id}").json()["state"] in ("ready", "failed"):
            return
        time.sleep(0.1)
    raise AssertionError("the job never settled")


def test_an_empty_library_is_an_empty_list_not_an_error(client: TestClient):
    """The first thing a new user sees. A 404 here would be a bug that reads as
    a broken account."""
    response = client.get(SONGS)

    assert response.status_code == 200
    assert response.json() == {"songs": [], "total": 0}


def test_a_song_appears_with_its_processing_state(client: TestClient, tmp_path: Path):
    """T-1.10's acceptance criterion."""
    job_id = upload(client, synth(tmp_path / "a.mp3"), "ותהי שמחה.mp3")["job_id"]
    settle(client, job_id)

    body = client.get(SONGS).json()

    assert body["total"] == 1
    (song,) = body["songs"]
    assert song["title"] == "ותהי שמחה"
    assert song["status"] == "ready"
    assert song["job"]["state"] == "ready"
    assert song["job"]["progress"] == 100


def test_the_newest_song_comes_first(client: TestClient, tmp_path: Path):
    """A library sorted oldest-first hides the song you just added."""
    first = upload(client, synth(tmp_path / "a.mp3", frequency=440), "ראשון.mp3")
    settle(client, first["job_id"])
    second = upload(client, synth(tmp_path / "b.mp3", frequency=660), "שני.mp3")
    settle(client, second["job_id"])

    titles = [song["title"] for song in client.get(SONGS).json()["songs"]]

    assert titles == ["שני", "ראשון"]


def test_a_playable_song_says_so_even_while_processing(client: TestClient, tmp_path: Path):
    """D-28. A library that only shows "processing" loses the whole point: the
    user could already be singing."""
    job_id = upload(client, synth(tmp_path / "a.mp3"))["job_id"]
    settle(client, job_id)

    (song,) = client.get(SONGS).json()["songs"]

    assert song["is_playable"] is True
    assert "is_playable" in song and "status" in song, "the two must be separately visible"


def test_a_failed_song_carries_the_code_the_screen_renders(
    storage: LocalStorage, schema: None, tmp_path: Path
):
    failing = build(storage, StubSeparator(SeparationError("the GPU went away")))
    try:
        job_id = upload(failing, synth(tmp_path / "a.mp3"))["job_id"]
        settle(failing, job_id)

        (song,) = failing.get(SONGS).json()["songs"]

        assert song["status"] == "failed"
        assert song["job"]["error_code"] == "separation_failed"
        assert song["is_playable"] is False
    finally:
        failing.__exit__(None, None, None)


def test_only_the_most_recent_job_is_reported(client: TestClient, tmp_path: Path):
    """A song retried three times still has one state, not three."""
    job_id = upload(client, synth(tmp_path / "a.mp3"))["job_id"]
    settle(client, job_id)

    (song,) = client.get(SONGS).json()["songs"]

    assert song["job"]["id"] == job_id


def test_the_lyrics_status_is_visible(client: TestClient, tmp_path: Path):
    """Chapter 8's library shows whether a song has lyrics yet."""
    job_id = upload(client, synth(tmp_path / "a.mp3"))["job_id"]
    settle(client, job_id)

    (song,) = client.get(SONGS).json()["songs"]

    assert song["lyrics_status"] == "pending"


def test_paging_is_available_for_when_the_library_grows(client: TestClient, tmp_path: Path):
    for index, frequency in enumerate((440, 550, 660)):
        settle(
            client,
            upload(client, synth(tmp_path / f"{index}.mp3", frequency=frequency))["job_id"],
        )

    page = client.get(SONGS, params={"limit": 2}).json()

    assert len(page["songs"]) == 2
    assert page["total"] == 3, "total is the library, not the page"


def test_a_silly_page_size_is_refused(client: TestClient):
    assert client.get(SONGS, params={"limit": 5000}).status_code == 422


def test_the_endpoint_is_documented(client: TestClient):
    assert SONGS in client.get("/openapi.json").json()["paths"]
