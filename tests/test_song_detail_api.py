"""`GET /songs/{id}` and the stem audio behind it - what the player opens with.

Since T-3.1 the URLs are *signed and expiring*, which is chapter 6 as written.
With the local backend they point back at this API and the signature is checked
here; with the object store they point at B2 and the API is not in the path at
all. The tests below are about the property both share: the audio is reachable
through the link that was handed out, and through nothing else.
"""

import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX, settings
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


def test_a_stem_url_carries_an_expiry_and_a_signature(client: TestClient, tmp_path: Path):
    """T-3.1's acceptance criterion, read off the link itself."""
    song_id = a_song(client, tmp_path)

    url = client.get(f"{SONGS}/{song_id}").json()["stems"][0]["url"]

    query = parse_qs(urlparse(url).query)
    assert query["sig"], url
    assert int(query["expires"][0]) > time.time()


def test_stem_audio_is_not_cached_past_its_link(client: TestClient, tmp_path: Path):
    """The object never changes, but a response cached past the expiry would be
    served from the browser by a link that no longer works."""
    song_id = a_song(client, tmp_path)
    stem = client.get(f"{SONGS}/{song_id}").json()["stems"][0]

    cache = client.get(stem["url"]).headers["cache-control"]

    assert "private" in cache
    assert f"max-age={settings.signed_url_ttl}" in cache


def test_the_audio_cannot_be_fetched_without_the_signature(client: TestClient, tmp_path: Path):
    """The point of the whole task: the path alone is not authority."""
    song_id = a_song(client, tmp_path)
    url = client.get(f"{SONGS}/{song_id}").json()["stems"][0]["url"]

    assert client.get(urlparse(url).path).status_code == 422


def test_a_link_whose_expiry_was_edited_is_refused(client: TestClient, tmp_path: Path):
    """Extending the deadline in the address bar invalidates the signature it
    came with, because the deadline is inside what was signed."""
    song_id = a_song(client, tmp_path)
    url = client.get(f"{SONGS}/{song_id}").json()["stems"][0]["url"]
    parts = urlparse(url)
    query = parse_qs(parts.query)
    later = int(query["expires"][0]) + 86_400

    response = client.get(f"{parts.path}?expires={later}&sig={query['sig'][0]}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "link_invalid"


def test_an_expired_link_is_refused(client: TestClient, tmp_path: Path, storage: LocalStorage):
    song_id = a_song(client, tmp_path)
    stem_key = urlparse(client.get(f"{SONGS}/{song_id}").json()["stems"][0]["url"]).path
    key = stem_key.split("/files/", 1)[1]
    expired = storage.signed_url(key, -1)

    response = client.get(expired)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "link_invalid"


def test_a_signed_link_to_an_object_that_is_gone_is_a_410(
    client: TestClient, storage: LocalStorage
):
    """Distinct from a bad link on purpose: one is the operator's problem."""
    response = client.get(storage.signed_url("songs/nobody/vocals.mp3", 60))

    assert response.status_code == 410
    assert response.json()["error"]["code"] == "file_missing"


def test_a_song_that_is_not_there_is_a_404_with_a_code(client: TestClient):
    response = client.get(f"{SONGS}/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "song_not_found"


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
    assert f"{API_PREFIX}/files/{{key}}" in paths
    assert f"{SONGS}/{{song_id}}/stems/{{kind}}" not in paths, (
        "the unsigned route is what T-3.1 removes"
    )
