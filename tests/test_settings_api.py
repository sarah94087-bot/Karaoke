"""T-1.16: come back to a song and find the key and the mix as you left them.

The acceptance criterion is a round trip, so that is what most of these do:
save, throw the session away, read it back.
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
            path.write_bytes(name.encode())
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
    """Settings cascade with their song."""


def settle(client: TestClient, job_id: str) -> None:
    import time

    for _ in range(100):
        if client.get(f"{API_PREFIX}/jobs/{job_id}").json()["state"] in ("ready", "failed"):
            return
        time.sleep(0.1)
    raise AssertionError("the job never settled")


def a_song(client: TestClient, tmp_path: Path, *, processed: bool = False) -> str:
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
    if processed:
        settle(client, body["job_id"])
    return body["id"]


def settings_of(client: TestClient, song_id: str) -> dict:
    return client.get(f"{SONGS}/{song_id}").json()["settings"]


def test_a_new_song_opens_at_the_defaults(client: TestClient, tmp_path: Path):
    """Not null, and not an error: a song nobody has adjusted is at 0 and 100%."""
    song_id = a_song(client, tmp_path)

    assert settings_of(client, song_id) == {
        "key_shift": 0,
        "tempo_ratio": 1.0,
        "stem_volumes": None,
        "lyric_offset_ms": 0,
    }


def test_settings_survive_coming_back_to_the_song(client: TestClient, tmp_path: Path):
    """T-1.16's acceptance criterion."""
    song_id = a_song(client, tmp_path)

    client.put(
        f"{SONGS}/{song_id}/settings",
        json={"key_shift": -3, "tempo_ratio": 0.85, "stem_volumes": {"vocals": 0.0}},
    )

    reopened = settings_of(client, song_id)
    assert reopened["key_shift"] == -3
    assert reopened["tempo_ratio"] == 0.85
    assert reopened["stem_volumes"] == {"vocals": 0.0}


def test_saving_twice_updates_rather_than_duplicating(client: TestClient, tmp_path: Path, connect):
    """The primary key is (user_id, song_id), so there cannot be two rows - this
    checks the upsert actually relies on that."""
    song_id = a_song(client, tmp_path)

    client.put(f"{SONGS}/{song_id}/settings", json={"key_shift": 2})
    client.put(f"{SONGS}/{song_id}/settings", json={"key_shift": 5})

    with connect() as conn:
        rows = conn.execute(
            "select count(*) from user_song_settings where song_id = %s", [song_id]
        ).fetchone()[0]

    assert rows == 1
    assert settings_of(client, song_id)["key_shift"] == 5


def test_the_song_and_its_settings_arrive_together(client: TestClient, tmp_path: Path):
    """Opening a song is one request, not two: the player needs both before it
    can build the graph at the right key."""
    song_id = a_song(client, tmp_path, processed=True)
    client.put(f"{SONGS}/{song_id}/settings", json={"key_shift": 4})

    body = client.get(f"{SONGS}/{song_id}").json()

    assert body["settings"]["key_shift"] == 4
    assert len(body["stems"]) == 4


@pytest.mark.parametrize(
    ("sent", "expected"),
    [
        ({"key_shift": 40}, 6),
        ({"key_shift": -40}, -6),
    ],
)
def test_a_key_outside_the_range_is_corrected_not_rejected(
    client: TestClient, tmp_path: Path, sent: dict, expected: int
):
    """A settings save must never fail a user's session, so an impossible value
    is clamped rather than turned into a 422."""
    song_id = a_song(client, tmp_path)

    response = client.put(f"{SONGS}/{song_id}/settings", json=sent)

    assert response.status_code == 200
    assert response.json()["key_shift"] == expected


def test_a_tempo_outside_the_range_is_corrected(client: TestClient, tmp_path: Path):
    song_id = a_song(client, tmp_path)

    assert (
        client.put(f"{SONGS}/{song_id}/settings", json={"tempo_ratio": 9}).json()["tempo_ratio"]
        == 1.5
    )
    assert (
        client.put(f"{SONGS}/{song_id}/settings", json={"tempo_ratio": 0}).json()["tempo_ratio"]
        == 0.5
    )


def test_a_volume_for_a_stem_that_does_not_exist_is_dropped(client: TestClient, tmp_path: Path):
    """Junk in the row would be applied by some future client that trusts it."""
    song_id = a_song(client, tmp_path)

    client.put(
        f"{SONGS}/{song_id}/settings",
        json={"stem_volumes": {"vocals": 0.5, "kazoo": 1.0}},
    )

    assert settings_of(client, song_id)["stem_volumes"] == {"vocals": 0.5}


def test_volumes_are_clamped(client: TestClient, tmp_path: Path):
    song_id = a_song(client, tmp_path)

    client.put(f"{SONGS}/{song_id}/settings", json={"stem_volumes": {"drums": 5, "bass": -2}})

    assert settings_of(client, song_id)["stem_volumes"] == {"drums": 1.0, "bass": 0.0}


def test_the_lyrics_offset_survives_coming_back_to_the_song(client: TestClient, tmp_path: Path):
    """T-2.7: nudging the words is a setting like any other, and the whole point
    is that the song opens next time already nudged."""
    song_id = a_song(client, tmp_path)

    client.put(f"{SONGS}/{song_id}/settings", json={"lyric_offset_ms": -400})

    assert settings_of(client, song_id)["lyric_offset_ms"] == -400


def test_an_offset_in_seconds_by_mistake_is_clamped(client: TestClient, tmp_path: Path):
    """A song is eight minutes at most, so half a minute of offset is already a
    unit mix-up. Clamped, not refused: an auto-save must never fail a session."""
    song_id = a_song(client, tmp_path)

    response = client.put(f"{SONGS}/{song_id}/settings", json={"lyric_offset_ms": 400_000})

    assert response.status_code == 200
    assert response.json()["lyric_offset_ms"] == 30_000


def test_saving_for_a_song_that_does_not_exist_is_a_404_with_a_code(client: TestClient):
    response = client.put(f"{SONGS}/{uuid.uuid4()}/settings", json={"key_shift": 1})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "song_not_found"


def test_deleting_a_song_takes_its_settings_with_it(client: TestClient, tmp_path: Path, connect):
    """Chapter 9's deletion policy frees everything belonging to a song."""
    song_id = a_song(client, tmp_path)
    client.put(f"{SONGS}/{song_id}/settings", json={"key_shift": 1})

    with connect() as conn:
        conn.execute("delete from songs where id = %s", [song_id])
        left = conn.execute(
            "select count(*) from user_song_settings where song_id = %s", [song_id]
        ).fetchone()[0]

    assert left == 0


def test_the_endpoint_is_documented(client: TestClient):
    assert f"{SONGS}/{{song_id}}/settings" in client.get("/openapi.json").json()["paths"]
