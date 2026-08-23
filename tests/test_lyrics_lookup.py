"""T-2.2 end to end, against a fake database: a known song comes back timed.

**No test here reaches the network.** The catalogue is a seam precisely so that
this can be true; what LRCLIB actually answers was checked by hand once, and the
numbers are in CLAUDE.md rather than in a test that fails when a stranger's
service is down.
"""

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.deps import get_catalogue
from apps.api.main import create_app
from packages.providers.lyrics_catalogue import Candidate, CatalogueError

SONGS = f"{API_PREFIX}/songs"

LRC = """[ti:עוף גוזל]
[00:12.00]שורה ראשונה
[00:16.50]שורה שנייה
[00:21.00]שורה שלישית
"""


class FakeCatalogue:
    """A lyrics database that answers from a list, and remembers what it was
    asked - the queries matter as much as the answers."""

    name = "fake"

    def __init__(self, candidates: list[Candidate] | None = None, error: Exception | None = None):
        self.candidates = candidates if candidates is not None else [a_candidate()]
        self.error = error
        self.asked: list[tuple[str, str | None]] = []

    def search(self, title: str, artist: str | None = None) -> list[Candidate]:
        self.asked.append((title, artist))
        if self.error is not None:
            raise self.error
        return list(self.candidates)


def a_candidate(title="עוף גוזל", artist="אריק איינשטיין", duration=222.0, synced=LRC):
    return Candidate(
        title=title,
        artist=artist,
        album=None,
        duration_sec=duration,
        synced_lyrics=synced,
        instrumental=False,
        remote_id="42",
        provider="fake",
    )


@pytest.fixture
def catalogue() -> FakeCatalogue:
    return FakeCatalogue()


@pytest.fixture
def client(schema: None, catalogue: FakeCatalogue) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_catalogue] = lambda: catalogue
    running = TestClient(app)
    running.__enter__()
    yield running
    running.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Lyrics cascade with their song."""


@pytest.fixture
def a_song(connect: Callable[[], object]) -> Callable[..., str]:
    def _song(title: str = "אריק איינשטיין - עוף גוזל", duration: int | None = 222) -> str:
        with connect() as conn:
            row = conn.execute(
                "insert into songs (user_id, title, source_type, status, is_playable, "
                "lyrics_status, duration_sec) "
                "values ('00000000-0000-0000-0000-000000000001', %s, 'file', 'processing', true, 'pending', %s) returning id",
                [title, duration],
            ).fetchone()
        return str(row[0])

    return _song


def search(client: TestClient, song_id: str):
    return client.post(f"{SONGS}/{song_id}/lyrics/search")


def test_a_known_song_comes_back_with_timed_lyrics(client: TestClient, a_song):
    """T-2.2's acceptance criterion, through the API."""
    song_id = a_song()

    response = search(client, song_id)

    assert response.status_code == 201
    body = response.json()
    assert [line["text"] for line in body["lines"]] == [
        "שורה ראשונה",
        "שורה שנייה",
        "שורה שלישית",
    ]
    assert [line["start_ms"] for line in body["lines"]] == [12_000, 16_500, 21_000]


def test_the_match_is_recorded_as_coming_from_the_database(client: TestClient, a_song):
    """`source` is what tells T-2.4 and the editor where the words came from -
    hand-timed by a stranger is not the same as a machine transcript."""
    song_id = a_song()

    body = search(client, song_id).json()

    assert body["source"] == "db"
    assert body["status"] == "line"


def test_the_language_comes_from_the_words_themselves(client: TestClient, a_song, catalogue):
    """Not from the user's locale: a Hebrew speaker's library has English songs
    in it, and T-2.5's aligner has to be told which."""
    catalogue.candidates = [a_candidate(synced="[00:10.00]a line of english\n")]
    song_id = a_song()

    assert search(client, song_id).json()["language"] == "en"


def test_the_artist_is_guessed_from_the_filename_and_tried(client: TestClient, a_song, catalogue):
    """The song is called `אריק איינשטיין - עוף גוזל` because that was the
    filename. Both readings of it reach the database."""
    search(client, a_song())

    assert ("עוף גוזל", "אריק איינשטיין") in catalogue.asked


def test_a_song_of_a_different_length_is_not_matched(client: TestClient, a_song, catalogue):
    """The strongest signal there is: our duration is measured, not claimed."""
    catalogue.candidates = [a_candidate(duration=300.0)]

    response = search(client, a_song(duration=222))

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "lyrics_match_not_found"


def test_a_database_that_is_down_is_not_a_failure(client: TestClient, a_song, catalogue):
    """Chapter 7's rule: a lyrics failure is not a job failure. From the API it
    is the same 404 as "nothing found", because the user's next move is the
    same - open the editor."""
    catalogue.error = CatalogueError("connection refused")

    assert search(client, a_song()).status_code == 404


def test_lyrics_with_no_timings_are_not_stored(client: TestClient, a_song, catalogue):
    catalogue.candidates = [a_candidate(synced="שורה בלי זמן\n")]

    assert search(client, a_song()).status_code == 404


def test_searching_again_adds_a_version_and_keeps_the_edit(client: TestClient, a_song):
    """Asking explicitly is safe even after somebody has edited by hand: their
    version is still there, one behind."""
    song_id = a_song()
    client.put(
        f"{SONGS}/{song_id}/lyrics",
        json={"lines": [{"text": "מה שכתבתי בעצמי", "start_ms": 1_000}]},
    )

    body = search(client, song_id).json()

    assert body["version"] == 2
    assert body["source"] == "db"
    edited = client.get(f"{SONGS}/{song_id}/lyrics", params={"version": 1}).json()
    assert edited["lines"][0]["text"] == "מה שכתבתי בעצמי"


def test_searching_for_a_song_that_does_not_exist_is_a_404(client: TestClient):
    response = client.post(f"{SONGS}/{uuid.uuid4()}/lyrics/search")

    assert response.json()["error"]["code"] == "song_not_found"


def test_the_endpoint_is_documented(client: TestClient):
    assert f"{SONGS}/{{song_id}}/lyrics/search" in client.get("/openapi.json").json()["paths"]
