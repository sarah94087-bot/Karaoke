"""T-4.2 from the outside: the fields fill themselves, and a person can fix them.

Two halves of the same acceptance criterion. The first is that an upload of a
tagged file arrives in the library already named - which is a change of
behaviour, since until this task the title was the file name and the artist was
always empty. The second is `PATCH /songs/{id}`, which is loud where the player
settings are clamped (T-1.16): this is a name somebody typed, and storing
something other than what they wrote is worse than saying it did not go in.
"""

import shutil
import subprocess
import tempfile
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
    shutil.which("ffmpeg") is None, reason="ffmpeg writes the tagged files"
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
    with TestClient(app) as running:
        app.state.runner.separator = StubSeparator()
        app.state.runner.storage = storage
        yield running


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Uploads deduplicate on the audio, so the table has to start empty."""


def an_mp3(path: Path, frequency: int = 440, **tags: str) -> Path:
    arguments = [
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
    ]
    for key, value in tags.items():
        arguments += ["-metadata", f"{key}={value}"]
    subprocess.run([*arguments, str(path)], check=True, capture_output=True, encoding="utf-8")
    return path


def upload(client: TestClient, source: Path, filename: str) -> dict:
    answer = client.post(
        f"{SONGS}/upload",
        files={"file": (filename, source.read_bytes(), "audio/mpeg")},
    )
    assert answer.status_code in (200, 201), answer.text
    return answer.json()


def detail(client: TestClient, song_id: str) -> dict:
    answer = client.get(f"{SONGS}/{song_id}")
    assert answer.status_code == 200, answer.text
    return answer.json()


# -- filled automatically ----------------------------------------------------


def test_an_uploaded_file_is_named_by_its_own_tags(client: TestClient, tmp_path: Path):
    """The change of behaviour this task is: until now the title was the file
    name and the artist was always empty."""
    source = an_mp3(tmp_path / "01 - track.mp3", title="שביר", artist="ריטה")

    created = upload(client, source, "01 - track.mp3")

    assert created["title"] == "שביר"
    assert detail(client, created["id"])["artist"] == "ריטה"


def test_a_file_with_no_tags_still_falls_back_to_its_name(client: TestClient, tmp_path: Path):
    source = an_mp3(tmp_path / "bare.mp3", frequency=330)

    created = upload(client, source, "עוף גוזל.mp3")

    assert created["title"] == "עוף גוזל"
    assert detail(client, created["id"])["artist"] is None


def test_the_measured_length_is_there_without_anybody_typing_it(client: TestClient, tmp_path: Path):
    created = upload(client, an_mp3(tmp_path / "a.mp3", frequency=550), "a.mp3")

    assert created["duration_sec"] == 1


# -- corrected by hand -------------------------------------------------------


def test_the_name_and_the_artist_can_be_corrected(client: TestClient, tmp_path: Path):
    song_id = upload(client, an_mp3(tmp_path / "b.mp3", frequency=220), "wrong.mp3")["id"]

    answer = client.patch(f"{SONGS}/{song_id}", json={"title": "שביר", "artist": "ריטה"})

    assert answer.status_code == 200, answer.text
    assert answer.json()["title"] == "שביר"
    assert answer.json()["details_edited"] is True
    # And it is what the library and the player see, not just what came back.
    assert detail(client, song_id)["artist"] == "ריטה"


def test_a_field_that_is_not_sent_is_left_alone(client: TestClient, tmp_path: Path):
    """The whole reason this is a PATCH: the screen sends what was edited, and a
    PUT would make "I only changed the artist" indistinguishable from "the title
    is now blank"."""
    source = an_mp3(tmp_path / "c.mp3", frequency=660, title="שביר", artist="מישהו")
    song_id = upload(client, source, "c.mp3")["id"]

    client.patch(f"{SONGS}/{song_id}", json={"artist": "ריטה"})

    after = detail(client, song_id)
    assert (after["title"], after["artist"]) == ("שביר", "ריטה")


def test_an_empty_artist_clears_it(client: TestClient, tmp_path: Path):
    """ "I do not know who this is" is a real answer, and the field starts empty
    for most songs anyway."""
    source = an_mp3(tmp_path / "d.mp3", frequency=770, title="שביר", artist="מישהו אחר")
    song_id = upload(client, source, "d.mp3")["id"]

    answer = client.patch(f"{SONGS}/{song_id}", json={"artist": "  "})

    assert answer.status_code == 200
    assert answer.json()["artist"] is None


def test_a_song_cannot_be_left_without_a_name(client: TestClient, tmp_path: Path):
    song_id = upload(client, an_mp3(tmp_path / "e.mp3", frequency=880), "e.mp3")["id"]

    answer = client.patch(f"{SONGS}/{song_id}", json={"title": "   "})

    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "song_title_empty"


def test_an_absurd_name_is_refused_rather_than_quietly_cut(client: TestClient, tmp_path: Path):
    """Loud where the player settings are clamped (T-1.16): an auto-save must
    never fail a session, but this is a button somebody pressed."""
    song_id = upload(client, an_mp3(tmp_path / "f.mp3", frequency=990), "f.mp3")["id"]

    answer = client.patch(f"{SONGS}/{song_id}", json={"title": "x" * 500})

    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "song_name_too_long"


def test_the_name_is_stored_tidied(client: TestClient, tmp_path: Path):
    song_id = upload(client, an_mp3(tmp_path / "g.mp3", frequency=210), "g.mp3")["id"]

    answer = client.patch(f"{SONGS}/{song_id}", json={"title": "  שיר    שלי  "})

    assert answer.json()["title"] == "שיר שלי"


def test_a_song_that_is_not_yours_is_not_found(client: TestClient):
    """Every route that names a song asks the same question in the same place
    (T-3.7), and a 404 rather than a 403 - a 403 answers a question the asker
    had no business asking."""
    answer = client.patch(f"{SONGS}/00000000-0000-0000-0000-0000000000ff", json={"title": "x"})

    assert answer.status_code == 404
    assert answer.json()["error"]["code"] == "song_not_found"
