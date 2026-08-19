"""T-1.5 through the API: upload a local file, get a normalised song.

Needs the compose stack (a real Postgres) and ffmpeg, and skips without either.
The point of testing this end to end rather than unit-testing the pieces is that
the interesting failures are at the joins: a row committed before storage
succeeded, a temporary file left behind, an error that loses its code on the way
out.
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
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise"
)

UPLOAD = f"{API_PREFIX}/songs/upload"


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


@pytest.fixture
def client(schema: None, storage: LocalStorage) -> Iterator[TestClient]:
    app = create_app()
    # The database comes from DATABASE_URL through the lifespan, as in
    # production; only storage is redirected, so tests do not write into the
    # developer's var/storage.
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as running:
        yield running


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Every test starts from an empty library; dedup tests depend on it."""


def synth(path: Path, *, seconds: float = 1.0, rate: int = 44100, channels: int = 2) -> Path:
    layout = "mono" if channels == 1 else "stereo"
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
            f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
            "-ac",
            str(channels),
            "-af",
            f"aformat=channel_layouts={layout}",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def upload(client: TestClient, path: Path, filename: str | None = None):
    with path.open("rb") as handle:
        return client.post(UPLOAD, files={"file": (filename or path.name, handle, "audio/mpeg")})


def test_a_song_uploads_and_comes_back_normalised(client: TestClient, tmp_path: Path):
    response = upload(client, synth(tmp_path / "tune.mp3", seconds=2, rate=22050, channels=1))

    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["sample_rate"], body["channels"]) == (44100, 2)
    assert body["duration_sec"] == 2
    assert body["status"] == "pending"
    assert body["is_playable"] is False, "nothing is playable before separation"


def test_a_hebrew_filename_becomes_the_title(client: TestClient, tmp_path: Path):
    response = upload(client, synth(tmp_path / "t.mp3"), filename="ותהי שמחה.mp3")

    assert response.json()["title"] == "ותהי שמחה"


def test_both_the_original_and_the_normalised_file_are_kept(
    client: TestClient, storage: LocalStorage, tmp_path: Path
):
    """Chapter 7 wants every stage re-runnable from saved intermediates, and
    normalisation is a stage."""
    song_id = upload(client, synth(tmp_path / "t.mp3")).json()["id"]

    assert storage.exists(f"songs/{song_id}/original.mp3")
    assert storage.exists(f"songs/{song_id}/normalised.wav")


def test_the_stored_file_really_is_44100_stereo(
    client: TestClient, storage: LocalStorage, tmp_path: Path
):
    """The response says so; this checks the bytes on disk agree."""
    song_id = upload(client, synth(tmp_path / "t.wav", rate=8000, channels=1)).json()["id"]

    from packages.audio.normalize import probe

    info = probe(storage.local_path(f"songs/{song_id}/normalised.wav"))

    assert (info.sample_rate, info.channels) == (44100, 2)


def test_the_same_audio_twice_is_not_processed_twice(client: TestClient, tmp_path: Path):
    """Chapter 9 caps new songs per month, and identical audio would produce
    identical stems for double the GPU credit."""
    source = synth(tmp_path / "same.mp3")

    first = upload(client, source)
    second = upload(client, source)

    assert first.json()["already_existed"] is False
    assert second.json()["already_existed"] is True
    assert second.json()["id"] == first.json()["id"]


def test_the_same_song_in_two_formats_is_recognised_as_one(client: TestClient, tmp_path: Path):
    """The hash is of the normalised audio, so the container does not matter."""
    wav = synth(tmp_path / "a.wav")
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(wav), str(tmp_path / "a.flac")],
        check=True,
        capture_output=True,
    )

    first = upload(client, wav)
    second = upload(client, tmp_path / "a.flac")

    assert second.json()["id"] == first.json()["id"]


def test_a_file_that_is_not_audio_is_refused_with_a_code(client: TestClient, tmp_path: Path):
    bogus = tmp_path / "song.mp3"
    bogus.write_bytes(b"not audio at all")

    response = upload(client, bogus)

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["code"] in {"unreadable_audio", "no_audio_stream", "unknown_duration"}
    assert body["request_id"], "an error the user reports must be traceable"


def test_an_unsupported_extension_is_refused_before_decoding(client: TestClient, tmp_path: Path):
    document = tmp_path / "lyrics.pdf"
    document.write_bytes(b"%PDF-1.4")

    response = upload(client, document)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "unsupported_format"


def test_a_song_over_eight_minutes_is_refused(client: TestClient, tmp_path: Path):
    long_song = synth(tmp_path / "epic.wav", seconds=8 * 60 + 5, rate=8000, channels=1)

    response = upload(client, long_song)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "song_too_long"


def test_an_empty_file_is_refused(client: TestClient, tmp_path: Path):
    empty = tmp_path / "empty.mp3"
    empty.write_bytes(b"")

    response = upload(client, empty)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "empty_file"


def test_a_file_over_the_size_limit_is_refused_while_it_uploads(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """Enforced as the bytes arrive, not from Content-Length, which is a claim."""
    import dataclasses

    from apps.api.routers import songs

    # Settings is frozen, so swap in a copy rather than mutating the singleton.
    monkeypatch.setattr(
        songs, "settings", dataclasses.replace(songs.settings, max_upload_bytes=1024)
    )
    big = synth(tmp_path / "big.wav", seconds=5)

    response = upload(client, big)

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_a_rejected_upload_leaves_no_row_and_no_files(
    client: TestClient, storage: LocalStorage, tmp_path: Path, connect
):
    bogus = tmp_path / "song.mp3"
    bogus.write_bytes(b"not audio at all")

    upload(client, bogus)

    with connect() as conn:
        count = conn.execute("select count(*) from songs").fetchone()[0]
    assert count == 0
    assert not list((storage.root).rglob("*.wav"))


def test_the_endpoint_is_documented(client: TestClient):
    schema = client.get("/openapi.json").json()

    assert UPLOAD in schema["paths"]


def test_a_deduplicated_upload_answers_200_not_201(client: TestClient, tmp_path: Path):
    """201 means something was created. On the second upload nothing was."""
    source = synth(tmp_path / "same.mp3")

    assert upload(client, source).status_code == 201
    assert upload(client, source).status_code == 200
