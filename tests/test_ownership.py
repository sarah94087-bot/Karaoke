"""T-3.7's acceptance criterion: one user cannot reach another's song.

Every endpoint that names a song is exercised, because the failure this guards
against is not "the check is wrong" - it is "one route forgot", and the only way
to notice that is to ask all of them.

Somebody else's song answers **404, not 403**. A 403 says "this exists and it is
not yours", which answers a question the asker had no business asking. Missing
and forbidden are deliberately indistinguishable here, and these tests assert
that rather than allowing either.
"""

import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.auth import AuthError
from apps.api.config import API_PREFIX
from apps.api.deps import get_storage
from apps.api.main import create_app
from packages.providers.separation import STEM_NAMES, Separated
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is required to normalise the upload"
)

SONGS = f"{API_PREFIX}/songs"
ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")


class PretendVerifier:
    """Two people, told apart by their token.

    Standing in for Supabase so the isolation itself can be tested without a
    project, a network or a signing key. What it verifies is nothing; what it
    provides is two different users, which is all these tests need. The real
    verifier has its own file.
    """

    name = "pretend"

    def user_id(self, token: str | None) -> uuid.UUID:
        people = {"alice-token": ALICE, "bob-token": BOB}
        if token not in people:
            raise AuthError("no such token")
        return people[token]


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
    app.state.verifier = PretendVerifier()
    app.state.runner.separator = StubSeparator()
    app.state.runner.storage = storage
    yield running
    running.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Both libraries start empty, or "Bob sees nothing" proves nothing."""


def as_alice(**headers) -> dict[str, str]:
    return {"Authorization": "Bearer alice-token", **headers}


def as_bob(**headers) -> dict[str, str]:
    return {"Authorization": "Bearer bob-token", **headers}


def an_mp3(tmp_path: Path, frequency: int = 440) -> Path:
    source = tmp_path / f"{frequency}.mp3"
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


def upload(client: TestClient, tmp_path: Path, headers: dict[str, str], frequency=440) -> dict:
    with an_mp3(tmp_path, frequency).open("rb") as handle:
        response = client.post(
            f"{SONGS}/upload",
            files={"file": ("שיר.mp3", handle, "audio/mpeg")},
            headers=headers,
        )
    assert response.status_code in (200, 201), response.text
    return response.json()


def finished(client: TestClient, job_id: str, headers: dict[str, str]) -> None:
    for _ in range(100):
        state = client.get(f"{API_PREFIX}/jobs/{job_id}", headers=headers).json()["state"]
        if state in ("ready", "failed"):
            return
        time.sleep(0.1)


# --- the library ------------------------------------------------------------


def test_the_library_is_only_your_own_songs(client: TestClient, tmp_path: Path):
    upload(client, tmp_path, as_alice(), frequency=440)

    mine = client.get(SONGS, headers=as_alice()).json()
    theirs = client.get(SONGS, headers=as_bob()).json()

    assert mine["total"] == 1
    assert theirs["total"] == 0
    assert theirs["songs"] == []


# --- one song ---------------------------------------------------------------


def test_somebody_elses_song_is_a_404(client: TestClient, tmp_path: Path):
    song = upload(client, tmp_path, as_alice())

    response = client.get(f"{SONGS}/{song['id']}", headers=as_bob())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "song_not_found"


def test_a_song_that_does_not_exist_answers_identically(client: TestClient, tmp_path: Path):
    """The two cases have to be indistinguishable, or the 404 leaks the fact
    that the song is real."""
    song = upload(client, tmp_path, as_alice())

    theirs = client.get(f"{SONGS}/{song['id']}", headers=as_bob())
    nothing = client.get(f"{SONGS}/{uuid.uuid4()}", headers=as_bob())

    assert theirs.status_code == nothing.status_code
    assert theirs.json()["error"] == nothing.json()["error"]


def test_settings_cannot_be_written_on_somebody_elses_song(client: TestClient, tmp_path: Path):
    song = upload(client, tmp_path, as_alice())

    response = client.put(
        f"{SONGS}/{song['id']}/settings",
        json={"key_shift": 3, "tempo_ratio": 1.0, "stem_volumes": None, "lyric_offset_ms": 0},
        headers=as_bob(),
    )

    assert response.status_code == 404


def test_settings_are_per_person(client: TestClient, tmp_path: Path):
    """Two people singing the same song want different keys, and T-1.16 keyed
    the row on (user, song) for exactly that. Now the user is real."""
    song = upload(client, tmp_path, as_alice())
    body = {"key_shift": -4, "tempo_ratio": 0.9, "stem_volumes": None, "lyric_offset_ms": 0}

    client.put(f"{SONGS}/{song['id']}/settings", json=body, headers=as_alice())

    assert (
        client.get(f"{SONGS}/{song['id']}", headers=as_alice()).json()["settings"]["key_shift"]
        == -4
    )


# --- lyrics -----------------------------------------------------------------


def test_lyrics_cannot_be_read_on_somebody_elses_song(client: TestClient, tmp_path: Path):
    song = upload(client, tmp_path, as_alice())

    response = client.get(f"{SONGS}/{song['id']}/lyrics", headers=as_bob())

    assert response.status_code == 404


def test_lyrics_cannot_be_written_on_somebody_elses_song(client: TestClient, tmp_path: Path):
    """The one that would be worst: not reading somebody's song, but editing it."""
    song = upload(client, tmp_path, as_alice())

    response = client.put(
        f"{SONGS}/{song['id']}/lyrics",
        json={"lines": [{"text": "שורה", "start_ms": 0}], "language": "he", "source": "manual"},
        headers=as_bob(),
    )

    assert response.status_code == 404


def test_a_lyrics_search_cannot_be_run_on_somebody_elses_song(client: TestClient, tmp_path: Path):
    song = upload(client, tmp_path, as_alice())

    response = client.post(f"{SONGS}/{song['id']}/lyrics/search", headers=as_bob())

    assert response.status_code == 404


# --- jobs -------------------------------------------------------------------


def test_somebody_elses_job_is_a_404(client: TestClient, tmp_path: Path):
    song = upload(client, tmp_path, as_alice())

    response = client.get(f"{API_PREFIX}/jobs/{song['job_id']}", headers=as_bob())

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


def test_somebody_elses_job_cannot_be_retried(client: TestClient, tmp_path: Path):
    """A retry spends GPU credit. Spending somebody else's would be the
    expensive version of this bug."""
    song = upload(client, tmp_path, as_alice())

    response = client.post(f"{API_PREFIX}/jobs/{song['job_id']}/retry", headers=as_bob())

    assert response.status_code == 404


def test_somebody_elses_progress_stream_is_refused_before_it_opens(
    client: TestClient, tmp_path: Path
):
    """Refused with a status, not with a 200 that carries a refusal - an SSE
    response that opened and then complained would be one the client has to
    interpret."""
    song = upload(client, tmp_path, as_alice())

    response = client.get(f"{API_PREFIX}/jobs/{song['job_id']}/events", headers=as_bob())

    assert response.status_code == 404


# --- no token at all --------------------------------------------------------


def test_without_a_token_nothing_is_readable(client: TestClient, tmp_path: Path):
    song = upload(client, tmp_path, as_alice())

    for path in (SONGS, f"{SONGS}/{song['id']}", f"{API_PREFIX}/jobs/{song['job_id']}"):
        response = client.get(path)
        assert response.status_code == 401, path
        assert response.json()["error"]["code"] == "not_signed_in"


def test_a_token_that_is_not_ours_is_refused(client: TestClient):
    response = client.get(SONGS, headers={"Authorization": "Bearer somebody-elses-token"})

    assert response.status_code == 401


# --- the same audio, two people ---------------------------------------------


def test_the_same_song_uploaded_by_two_people_is_two_songs(client: TestClient, tmp_path: Path):
    """Dedup is per owner (T-3.7's migration). Global dedup would hand whoever
    uploads second the first person's row - their title, their stems, and the
    knowledge that somebody else has that song."""
    mine = upload(client, tmp_path, as_alice(), frequency=660)
    theirs = upload(client, tmp_path, as_bob(), frequency=660)

    assert theirs["id"] != mine["id"]
    assert theirs["already_existed"] is False
    assert client.get(SONGS, headers=as_bob()).json()["total"] == 1


def test_uploading_the_same_song_twice_yourself_is_still_one_song(
    client: TestClient, tmp_path: Path
):
    """The dedup T-1.5 built has not been lost, only narrowed."""
    first = upload(client, tmp_path, as_alice(), frequency=880)
    again = upload(client, tmp_path, as_alice(), frequency=880)

    assert again["id"] == first["id"]
    assert again["already_existed"] is True
