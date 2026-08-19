"""`GET /songs/{id}` and the stem audio behind it - what the player opens with.

Chapter 6 hands out signed URLs here, which needs an object store and therefore
D-12. Until that is decided the URLs point back at this API, which is the same
contract from the player's side: fetch what you were given.
"""

import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.deps import get_storage
from apps.api.main import create_app
from packages.core.enums import StemKind
from packages.providers.separation import STEM_NAMES, Separated
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise the upload"
)

SONGS = f"{API_PREFIX}/songs"


class StubSeparator:
    name = "stub"

    def separate(self, source: Path, destination: Path) -> Separated:
        destination.mkdir(parents=True, exist_ok=True)
        stems = {}
        for name in STEM_NAMES:
            path = destination / f"{name}.mp3"
            # Distinct contents, so a mixed-up stem is visible rather than
            # plausible.
            path.write_bytes(f"audio for {name}".encode())
            stems[name] = path
        return Separated(stems=stems, backend=self.name)


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def client(schema: None, storage: LocalStorage) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_storage] = lambda: storage
    running = TestClient(app)
    running.__enter__()
    app.state.runner.separator = StubSeparator()
    app.state.runner.storage = storage
    yield running
    running.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """The player opens one song; which one has to be known."""


def a_song(client: TestClient, tmp_path: Path) -> str:
    source = tmp_path / "a.mp3"
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
            "sine=frequency=440:duration=1:sample_rate=44100",
            "-ac",
            "2",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    with source.open("rb") as handle:
        body = client.post(
            f"{SONGS}/upload", files={"file": ("שיר.mp3", handle, "audio/mpeg")}
        ).json()

    import time

    for _ in range(100):
        if client.get(f"{API_PREFIX}/jobs/{body['job_id']}").json()["state"] in (
            "ready",
            "failed",
        ):
            break
        time.sleep(0.1)
    return body["id"]


def test_a_song_comes_back_with_four_stems(client: TestClient, tmp_path: Path):
    song_id = a_song(client, tmp_path)

    body = client.get(f"{SONGS}/{song_id}").json()

    assert {stem["kind"] for stem in body["stems"]} == set(STEM_NAMES)


def test_the_stems_are_in_mixer_order(client: TestClient, tmp_path: Path):
    """Vocals first: chapter 8's mixer puts the fader you reach for at the top."""
    song_id = a_song(client, tmp_path)

    kinds = [stem["kind"] for stem in client.get(f"{SONGS}/{song_id}").json()["stems"]]

    assert kinds == [str(kind) for kind in StemKind]


def test_each_stem_url_fetches_that_stem(client: TestClient, tmp_path: Path):
    """A player that loads four copies of the drums sounds like it is working."""
    song_id = a_song(client, tmp_path)

    for stem in client.get(f"{SONGS}/{song_id}").json()["stems"]:
        response = client.get(stem["url"])

        assert response.status_code == 200
        assert response.content == f"audio for {stem['kind']}".encode()


def test_stem_audio_is_cacheable_forever(client: TestClient, tmp_path: Path):
    """A stem never changes once written, and the player fetches four of them
    every time a song is opened."""
    song_id = a_song(client, tmp_path)
    stem = client.get(f"{SONGS}/{song_id}").json()["stems"][0]

    cache = client.get(stem["url"]).headers["cache-control"]

    assert "immutable" in cache
    assert "max-age=31536000" in cache


def test_a_song_that_is_not_there_is_a_404_with_a_code(client: TestClient):
    response = client.get(f"{SONGS}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "song_not_found"


def test_a_stem_that_is_not_there_is_a_404_with_a_code(client: TestClient, tmp_path: Path):
    song_id = a_song(client, tmp_path)

    response = client.get(f"{SONGS}/{song_id}/stems/vocals-but-wrong")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "stem_not_found"


def test_a_song_with_no_stems_yet_answers_with_an_empty_list(client: TestClient, tmp_path: Path):
    """The player has to say "not ready yet" rather than fail: a song can be
    fetched while it is still processing."""
    source = tmp_path / "b.mp3"
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
            "sine=frequency=660:duration=1",
            "-ac",
            "2",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    with source.open("rb") as handle:
        song_id = client.post(
            f"{SONGS}/upload", files={"file": ("ב.mp3", handle, "audio/mpeg")}
        ).json()["id"]

    body = client.get(f"{SONGS}/{song_id}").json()

    assert isinstance(body["stems"], list)


def test_the_endpoints_are_documented(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]

    assert f"{SONGS}/{{song_id}}" in paths
    assert f"{SONGS}/{{song_id}}/stems/{{kind}}" in paths
