"""Chapter 9's automatic deletion (T-3.9).

Two things have to be true at once, and they pull in opposite directions: the
audio of a song nobody sings must go, and everything a person put into that song
by hand must stay. Most of this file is the second half, because that is the one
a careless implementation gets wrong - and gets wrong silently, months later,
with nobody watching.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from packages.core import retention
from packages.core.db import create_engine, session_factory
from packages.core.enums import LyricsStatus, SongStatus, SourceType
from packages.core.lyrics import LineDraft, save_lyrics
from packages.core.models import Song, Stem
from packages.core.settings import save_settings
from packages.core.stems import stems_for
from packages.providers.storage import LocalStorage

pytestmark = pytest.mark.anyio

OWNER = uuid.UUID("11111111-1111-1111-1111-111111111111")
MB = 1024 * 1024


@pytest.fixture
async def session(database_url: str, schema: None, empty_songs: None) -> AsyncIterator:
    engine = create_engine(database_url)
    factory = session_factory(engine)
    async with factory() as opened:
        yield opened
    await engine.dispose()


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "storage")


def long_ago(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def a_song(
    session,
    storage: LocalStorage,
    tmp_path: Path,
    *,
    played: datetime | None = None,
    added: datetime | None = None,
    title: str = "שיר",
    with_audio: bool = True,
) -> Song:
    song = Song(
        id=uuid.uuid4(),
        user_id=OWNER,
        title=title,
        artist="אמן",
        source_type=SourceType.FILE,
        content_hash=uuid.uuid4().hex,
        status=SongStatus.READY,
        is_playable=True,
        lyrics_status=LyricsStatus.LINE,
        bpm=128.0,
        original_key="Dm",
        last_played_at=played,
    )
    if added is not None:
        song.created_at = added
    session.add(song)
    await session.flush()

    if with_audio:
        for kind in ("vocals", "drums"):
            source = tmp_path / f"{song.id}-{kind}.mp3"
            source.write_bytes(b"x" * MB)
            key = f"songs/{song.id}/stems/{kind}.mp3"
            storage.put(key, source)
            session.add(Stem(song_id=song.id, kind=kind, storage_key=key, format="mp3", bytes=MB))
        await session.flush()
    return song


# --- who is on the list -----------------------------------------------------


async def test_a_song_nobody_has_played_for_six_months_is_on_it(session, storage, tmp_path):
    await a_song(session, storage, tmp_path, played=long_ago(200))

    assert len(await retention.reapable(session)) == 1


async def test_a_song_played_last_week_is_not(session, storage, tmp_path):
    await a_song(session, storage, tmp_path, played=long_ago(7))

    assert await retention.reapable(session) == []


async def test_never_played_counts_from_when_it_was_added(session, storage, tmp_path):
    """A song uploaded eight months ago and never opened is exactly what this
    rule is for. Treating a null as "recently played" would exempt the clearest
    case there is."""
    await a_song(session, storage, tmp_path, played=None, added=long_ago(240))

    assert len(await retention.reapable(session)) == 1


async def test_a_song_added_yesterday_and_not_yet_sung_is_left_alone(session, storage, tmp_path):
    await a_song(session, storage, tmp_path, played=None, added=long_ago(1))

    assert await retention.reapable(session) == []


async def test_a_song_with_no_audio_left_is_not_on_the_list_again(session, storage, tmp_path):
    """Running the task twice in a day has to be harmless - which is what makes
    it safe to schedule daily for a six-month rule."""
    song = await a_song(session, storage, tmp_path, played=long_ago(200))
    await retention.archive(session, storage, song.id)

    assert await retention.reapable(session) == []


async def test_the_longest_idle_comes_first(session, storage, tmp_path):
    await a_song(session, storage, tmp_path, played=long_ago(200), title="לפני חצי שנה")
    await a_song(session, storage, tmp_path, played=long_ago(400), title="לפני שנה")

    assert (await retention.reapable(session))[0].title == "לפני שנה"


# --- what removing actually does --------------------------------------------


async def test_the_audio_is_gone_from_storage(session, storage, tmp_path):
    song = await a_song(session, storage, tmp_path, played=long_ago(200))

    freed = await retention.archive(session, storage, song.id)

    assert freed == 2 * MB
    assert not storage.exists(f"songs/{song.id}/stems/vocals.mp3")
    assert await stems_for(session, song.id) == []


async def test_the_song_itself_stays(session, storage, tmp_path):
    """Chapter 9 says the metadata stays, and this is the half that a careless
    implementation loses."""
    song = await a_song(session, storage, tmp_path, played=long_ago(200), title="עוף גוזל")

    await retention.archive(session, storage, song.id)

    kept = await session.get(Song, song.id)
    assert kept is not None
    assert kept.title == "עוף גוזל"
    assert kept.artist == "אמן"
    assert kept.bpm == 128.0
    assert kept.original_key == "Dm"


async def test_the_lyrics_somebody_corrected_by_hand_survive(session, storage, tmp_path):
    """T-2.9 is minutes of somebody's evening per song. Deleting that to free
    fifteen megabytes would be the worst trade in the project."""
    song = await a_song(session, storage, tmp_path, played=long_ago(200))
    # Held now: save_lyrics and save_settings commit, and a commit expires every
    # loaded object - reading `song.id` afterwards would be a lazy refresh from
    # synchronous code, which an async session refuses.
    song_id = song.id
    await save_lyrics(
        session,
        song_id,
        lines=[LineDraft(text="שורה שתוקנה ביד", start_ms=1000, end_ms=3000)],
        language="he",
        source="manual",
    )
    await session.commit()

    await retention.archive(session, storage, song_id)

    from packages.core.lyrics import get_lyrics

    kept = await get_lyrics(session, song_id)
    assert kept is not None
    assert kept.lines[0].text == "שורה שתוקנה ביד"


async def test_the_settings_survive(session, storage, tmp_path):
    """The key somebody transposed the song into is a fact about their voice,
    not about the audio."""
    song = await a_song(session, storage, tmp_path, played=long_ago(200))
    song_id = song.id  # save_settings commits, which expires `song` - see above
    await save_settings(session, OWNER, song_id, key_shift=-4, tempo_ratio=0.9)

    await retention.archive(session, storage, song_id)

    from packages.core.settings import get_settings

    kept = await get_settings(session, OWNER, song_id)
    assert kept is not None
    assert kept.key_shift == -4


async def test_an_archived_song_says_it_cannot_be_played(session, storage, tmp_path):
    """`is_playable` is the field every screen already reads for that (D-28), so
    the library and the player need no new rule."""
    song = await a_song(session, storage, tmp_path, played=long_ago(200))

    await retention.archive(session, storage, song.id)

    kept = await session.get(Song, song.id)
    assert kept.status == SongStatus.ARCHIVED
    assert kept.is_playable is False


async def test_removing_one_song_does_not_touch_another(session, storage, tmp_path):
    old = await a_song(session, storage, tmp_path, played=long_ago(200), title="ישן")
    recent = await a_song(session, storage, tmp_path, played=long_ago(3), title="חדש")

    await retention.archive(session, storage, old.id)

    assert storage.exists(f"songs/{recent.id}/stems/vocals.mp3")
    assert len(await stems_for(session, recent.id)) == 2
