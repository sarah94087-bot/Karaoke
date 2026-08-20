"""T-2.1: timed lines go in, come back out, and old versions stay.

These build a song row directly rather than uploading one. Nothing here touches
audio - the acceptance criterion is about the words and their versions - and
going through the upload would make the whole file skip on a machine without
ffmpeg for no gain.
"""

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.config import API_PREFIX
from apps.api.main import create_app

SONGS = f"{API_PREFIX}/songs"


@pytest.fixture
def client(schema: None) -> Iterator[TestClient]:
    app = create_app()
    running = TestClient(app)
    running.__enter__()
    yield running
    running.__exit__(None, None, None)


@pytest.fixture(autouse=True)
def _clean(empty_songs: None) -> None:
    """Lyrics cascade with their song."""


@pytest.fixture
def a_song(connect: Callable[[], object]) -> Callable[..., str]:
    def _song(lyrics_status: str = "pending") -> str:
        with connect() as conn:
            row = conn.execute(
                "insert into songs (title, source_type, status, is_playable, lyrics_status) "
                "values ('שיר', 'file', 'processing', true, %s) returning id",
                [lyrics_status],
            ).fetchone()
        return str(row[0])

    return _song


def lyrics_of(client: TestClient, song_id: str, **params) -> dict:
    return client.get(f"{SONGS}/{song_id}/lyrics", params=params).json()


VERSE = [
    {"text": "שורה ראשונה", "start_ms": 1_000, "end_ms": 4_000},
    {"text": "שורה שנייה", "start_ms": 4_000, "end_ms": 7_500},
]


def test_a_song_still_being_transcribed_answers_202(client: TestClient, a_song):
    """D-28 opens the player before the lyrics exist, so "not yet" is a normal
    answer to a normal request - not a 404 the client would draw as a failure."""
    song_id = a_song("pending")

    response = client.get(f"{SONGS}/{song_id}/lyrics")

    assert response.status_code == 202
    assert response.json()["status"] == "pending"


def test_a_song_whose_transcription_failed_answers_an_empty_set(client: TestClient, a_song):
    """Chapter 7: a transcription failure is not a job failure. The editor has
    to open on something, and that something is an empty list."""
    song_id = a_song("missing")

    response = client.get(f"{SONGS}/{song_id}/lyrics")

    assert response.status_code == 200
    assert response.json()["lines"] == []
    assert response.json()["status"] == "missing"


def test_lines_come_back_as_they_went_in(client: TestClient, a_song):
    """The acceptance criterion, in one test."""
    song_id = a_song()

    created = client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE})

    assert created.status_code == 201
    lines = lyrics_of(client, song_id)["lines"]
    assert [line["text"] for line in lines] == ["שורה ראשונה", "שורה שנייה"]
    assert [line["start_ms"] for line in lines] == [1_000, 4_000]
    assert [line["index"] for line in lines] == [0, 1]


def test_word_timings_survive_the_round_trip(client: TestClient, a_song):
    song_id = a_song()

    client.put(
        f"{SONGS}/{song_id}/lyrics",
        json={
            "lines": [
                {
                    "text": "שתי מילים",
                    "start_ms": 1_000,
                    "end_ms": 2_000,
                    "words": [
                        {"text": "שתי", "start_ms": 1_000, "end_ms": 1_500},
                        {"text": "מילים", "start_ms": 1_500, "end_ms": 2_000},
                    ],
                }
            ]
        },
    )

    body = lyrics_of(client, song_id)
    assert [word["text"] for word in body["lines"][0]["words"]] == ["שתי", "מילים"]
    assert body["status"] == "word"


def test_an_edit_creates_a_version_and_leaves_the_old_one_alone(client: TestClient, a_song):
    """Chapter 6 is explicit that a PUT here never overwrites. This is the case
    it exists for: twenty minutes of hand-editing, then wanting the machine's
    version back."""
    song_id = a_song()
    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE, "source": "mix_asr"})

    edited = [dict(VERSE[0], text="שורה מתוקנת"), VERSE[1]]
    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": edited})

    newest = lyrics_of(client, song_id)
    assert newest["version"] == 2
    assert newest["lines"][0]["text"] == "שורה מתוקנת"

    first = lyrics_of(client, song_id, version=1)
    assert first["lines"][0]["text"] == "שורה ראשונה"
    assert first["source"] == "mix_asr"


def test_the_versions_are_listed_with_the_lyrics(client: TestClient, a_song):
    """The editor offers "go back to what the machine wrote", so the list has to
    arrive with the lyrics rather than behind a second request."""
    song_id = a_song()
    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE, "source": "vocals_asr"})
    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE})

    versions = lyrics_of(client, song_id)["versions"]

    assert [version["version"] for version in versions] == [2, 1]
    assert [version["source"] for version in versions] == ["manual", "vocals_asr"]


def test_the_songs_lyrics_status_follows_the_lines(client: TestClient, a_song, connect):
    """Derived from what was saved, never sent by the caller: a client free to
    set it eventually claims `word` about lines with no timings at all."""
    song_id = a_song()

    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE})

    with connect() as conn:
        stored = conn.execute("select lyrics_status from songs where id = %s", [song_id]).fetchone()
    assert stored[0] == "line"
    assert client.get(f"{SONGS}/{song_id}").json()["lyrics_status"] == "line"


def test_pasted_words_with_no_times_leave_the_song_marked_missing(client: TestClient, a_song):
    """T-2.10's starting point: the words are there, the timing is not, and the
    player must not pretend it can scroll them."""
    song_id = a_song()

    body = client.put(
        f"{SONGS}/{song_id}/lyrics",
        json={"lines": [{"text": "שורה ראשונה"}, {"text": "שורה שנייה"}]},
    ).json()

    assert body["status"] == "missing"
    assert len(body["lines"]) == 2


def test_blank_lines_between_verses_are_dropped(client: TestClient, a_song):
    song_id = a_song()

    body = client.put(
        f"{SONGS}/{song_id}/lyrics",
        json={"lines": [{"text": "בית ראשון"}, {"text": "  "}, {"text": "בית שני"}]},
    ).json()

    assert [line["index"] for line in body["lines"]] == [0, 1]


def test_a_line_that_ends_before_it_starts_is_refused_with_a_code(client: TestClient, a_song):
    """Unlike a settings save, which is automatic and is clamped, this is a
    deliberate press of a button - so it fails loudly rather than quietly
    rewriting what somebody typed."""
    song_id = a_song()

    response = client.put(
        f"{SONGS}/{song_id}/lyrics",
        json={"lines": [{"text": "שורה", "start_ms": 5_000, "end_ms": 4_000}]},
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_lyrics"


def test_saving_nothing_clears_the_lyrics_as_a_new_version(client: TestClient, a_song):
    """ "Start over" is a save, not a delete: the version that was there is still
    readable afterwards."""
    song_id = a_song()
    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE})

    emptied = client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": []}).json()

    assert emptied["version"] == 2
    assert emptied["lines"] == []
    assert emptied["status"] == "missing"
    assert len(lyrics_of(client, song_id, version=1)["lines"]) == 2


def test_lyrics_for_a_song_that_does_not_exist_are_a_404_with_a_code(client: TestClient):
    response = client.get(f"{SONGS}/{uuid.uuid4()}/lyrics")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "song_not_found"


def test_asking_for_a_version_that_does_not_exist_is_a_404(client: TestClient, a_song):
    song_id = a_song()
    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE})

    response = client.get(f"{SONGS}/{song_id}/lyrics", params={"version": 7})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "lyrics_version_not_found"


def test_deleting_a_song_takes_its_lyrics_and_lines_with_it(client: TestClient, a_song, connect):
    """Chapter 9's deletion policy frees everything belonging to a song, and the
    lines hang off the lyrics rather than off the song."""
    song_id = a_song()
    client.put(f"{SONGS}/{song_id}/lyrics", json={"lines": VERSE})

    with connect() as conn:
        conn.execute("delete from songs where id = %s", [song_id])
        left = conn.execute("select count(*) from lyric_lines").fetchone()[0]

    assert left == 0


def test_the_endpoints_are_documented(client: TestClient):
    paths = client.get("/openapi.json").json()["paths"]

    assert {"get", "put"} <= set(paths[f"{SONGS}/{{song_id}}/lyrics"])
