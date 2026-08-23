"""The direct upload (T-3.2): `POST /songs/upload-url` then `POST /songs`.

Chapter 6's shape, and the point of it is what is *not* here - the file does not
pass through the API on its way in. What the API hands out is one key, one
method and one hour; what it keeps is the right to decide those. There is no
credential in the browser.

The signed PUT is served locally by `apps/api/routers/files.py`, which is the
stand-in for a browser writing straight into the bucket. Same signature, same
expiry, same refusals - so this file exercises the flow the object store will
run, on a machine with no bucket.
"""

import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.deps import get_storage
from apps.api.main import create_app
from packages.providers.separation import STEM_NAMES, Separated
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise the upload"
)

SONGS = f"{API_PREFIX}/songs"


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


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage", secret="test-secret")


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
    """Uploads deduplicate on the audio, so the table has to start empty."""


def an_mp3(tmp_path: Path, frequency: int = 440, name: str = "a.mp3") -> Path:
    source = tmp_path / name
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
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    return source


def ticket(client: TestClient, filename: str = "שיר.mp3", size: int = 4096) -> dict:
    response = client.post(f"{SONGS}/upload-url", json={"filename": filename, "bytes": size})
    assert response.status_code == 200, response.text
    return response.json()


def upload(client: TestClient, tmp_path: Path, filename: str = "שיר.mp3") -> dict:
    """The whole browser side: ask, PUT, create."""
    source = an_mp3(tmp_path, name="local.mp3")
    handed = ticket(client, filename, source.stat().st_size)

    put = client.put(handed["url"], content=source.read_bytes())
    assert put.status_code == 201, put.text

    return client.post(f"{SONGS}", json={"upload_key": handed["key"], "filename": filename}).json()


# -- the ticket --------------------------------------------------------------


def test_the_ticket_is_a_put_link_that_expires(client: TestClient):
    handed = ticket(client)

    assert handed["method"] == "PUT"
    assert handed["expires_in"] > 0
    assert "sig=" in handed["url"]


def test_the_client_does_not_choose_the_key(client: TestClient):
    """A key the caller picked could name somebody's stem. This one is ours,
    under `uploads/`, and `POST /songs` accepts no other shape."""
    handed = ticket(client, "שיר.mp3")

    assert handed["key"].startswith("uploads/")
    assert handed["key"].endswith("/original.mp3")
    assert "שיר" not in handed["key"]


def test_a_format_we_cannot_read_is_refused_before_the_upload(client: TestClient):
    """Better to say so now than after 30MB has crossed the wire."""
    response = client.post(f"{SONGS}/upload-url", json={"filename": "notes.txt", "bytes": 10})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_format"


def test_a_file_that_says_it_is_too_big_is_refused_before_the_upload(client: TestClient):
    response = client.post(
        f"{SONGS}/upload-url", json={"filename": "a.mp3", "bytes": 500 * 1024 * 1024}
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


# -- the upload --------------------------------------------------------------


def test_the_bytes_do_not_go_through_the_songs_endpoint(client: TestClient, tmp_path: Path):
    """T-3.2 in one assertion: the PUT goes to storage, and the call that makes
    the song carries a key rather than a file."""
    handed = ticket(client)

    assert urlparse(handed["url"]).path.startswith(f"{API_PREFIX}/files/")


def test_a_read_link_cannot_be_used_to_write(
    client: TestClient, tmp_path: Path, storage: LocalStorage
):
    """The method is inside the signature. Without that, every stem URL the
    player is handed would also be permission to overwrite that stem."""
    body = upload(client, tmp_path)
    readable = storage.signed_url(f"songs/{body['id']}/normalised.wav", 60)

    response = client.put(readable, content=b"not audio")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "link_invalid"


def test_a_body_over_the_limit_is_refused_as_it_arrives(client: TestClient, storage: LocalStorage):
    """Content-Length is a claim; this is the bytes."""
    handed = ticket(client)

    response = client.put(handed["url"], content=b"x" * (80 * 1024 * 1024 + 1))

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert not storage.exists(handed["key"]), "a refused upload must not be stored"


# -- creating the song -------------------------------------------------------


def test_the_song_is_created_from_the_uploaded_key(client: TestClient, tmp_path: Path):
    body = upload(client, tmp_path)

    assert body["job_id"] is not None
    assert body["title"] == "שיר"
    assert body["duration_sec"] == 1


def test_the_staging_copy_does_not_survive_the_song(
    client: TestClient, tmp_path: Path, storage: LocalStorage
):
    """It is a transfer, not a second copy of the library. Left behind, every
    upload would cost twice the storage of the song it produced."""
    source = an_mp3(tmp_path, name="local.mp3")
    handed = ticket(client, "שיר.mp3", source.stat().st_size)
    client.put(handed["url"], content=source.read_bytes())

    client.post(f"{SONGS}", json={"upload_key": handed["key"], "filename": "שיר.mp3"})

    assert not storage.exists(handed["key"])


def test_the_same_audio_twice_is_one_song(client: TestClient, tmp_path: Path):
    """Dedup is on the normalised audio and is unchanged by how it arrived."""
    first = upload(client, tmp_path)
    second = upload(client, tmp_path)

    assert second["id"] == first["id"]
    assert second["already_existed"] is True
    assert second["job_id"] is None


def test_a_key_that_is_not_an_upload_key_is_refused(client: TestClient, tmp_path: Path):
    """Otherwise `POST /songs` would read any object in the bucket by name."""
    body = upload(client, tmp_path)

    response = client.post(
        f"{SONGS}",
        json={"upload_key": f"songs/{body['id']}/normalised.wav", "filename": "x.mp3"},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_creating_a_song_from_an_upload_that_never_happened_is_a_404(client: TestClient):
    response = client.post(
        f"{SONGS}",
        json={"upload_key": f"uploads/{uuid.uuid4()}/original.mp3", "filename": "a.mp3"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "upload_not_found"


def test_a_file_that_is_not_audio_fails_on_the_song_call(client: TestClient, tmp_path: Path):
    """The upload itself cannot tell - storage takes whatever bytes it is given.
    The screen has to say something true at this point instead."""
    handed = ticket(client, "a.mp3", 9)

    client.put(handed["url"], content=b"not audio")
    response = client.post(f"{SONGS}", json={"upload_key": handed["key"], "filename": "a.mp3"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unreadable_audio"


def test_the_original_keeps_its_format_even_when_storage_hands_back_a_bare_file(
    client: TestClient, tmp_path: Path, storage: LocalStorage
):
    """The bug a live run found and the local backend hid.

    `local_path` on the object store returns a *cache* file, named by a hash and
    with no extension, so reading the suffix off it stored the original as
    `songs/<id>/original` - no format on the key, and octet-stream when it is
    read back. On disk the same code was right by accident, which is why this
    test makes the local backend behave like the remote one.
    """

    class HandsBackABareFile(LocalStorage):
        def local_path(self, key: str) -> Path:
            source = super().local_path(key)
            bare = source.parent / "cached-under-a-hash"
            bare.write_bytes(source.read_bytes())
            return bare

    bare = HandsBackABareFile(storage.root, secret=storage.secret)
    client.app.dependency_overrides[get_storage] = lambda: bare

    body = upload(client, tmp_path)

    assert bare.exists(f"songs/{body['id']}/original.mp3")


def test_both_endpoints_are_documented(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]

    assert "post" in paths[f"{SONGS}/upload-url"]
    assert "post" in paths[SONGS]
    assert "put" in paths[f"{API_PREFIX}/files/{{key}}"]
