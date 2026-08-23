"""Chapter 9's limits, and D-30's rule about how they refuse (T-3.8).

Two halves. The first is arithmetic against a real database - what counts as
used, and which song gets offered up when there is no room. The second is the
part D-30 actually cares about: that going over says which limit it was and how
much of it is used, rather than stopping in a way that looks like a fault.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from packages.core import quota
from packages.core.db import create_engine, session_factory
from packages.core.enums import JobState, SongStatus, SourceType
from packages.core.models import Job, Song, Stem

pytestmark = pytest.mark.anyio

ALICE = uuid.UUID("11111111-1111-1111-1111-111111111111")
BOB = uuid.UUID("22222222-2222-2222-2222-222222222222")
MB = 1024 * 1024


@pytest.fixture
async def session(database_url: str, schema: None, empty_songs: None) -> AsyncIterator:
    engine = create_engine(database_url)
    factory = session_factory(engine)
    async with factory() as opened:
        yield opened
    await engine.dispose()


async def a_song(
    session,
    *,
    user: uuid.UUID = ALICE,
    megabytes: int = 15,
    played: datetime | None = None,
    created: datetime | None = None,
    title: str = "שיר",
) -> Song:
    song = Song(
        id=uuid.uuid4(),
        user_id=user,
        title=title,
        source_type=SourceType.FILE,
        content_hash=uuid.uuid4().hex,
        status=SongStatus.READY,
        last_played_at=played,
    )
    if created is not None:
        song.created_at = created
    session.add(song)
    await session.flush()
    if megabytes:
        session.add(
            Stem(
                song_id=song.id,
                kind="vocals",
                storage_key=f"songs/{song.id}/stems/vocals.mp3",
                format="mp3",
                bytes=megabytes * MB,
            )
        )
        await session.flush()
    return song


async def a_running_job(session, song: Song) -> Job:
    job = Job(id=uuid.uuid4(), song_id=song.id, user_id=song.user_id, state=JobState.RUNNING)
    session.add(job)
    await session.flush()
    return job


# --- what counts as used ----------------------------------------------------


async def test_the_month_counts_this_users_songs(session):
    await a_song(session)
    await a_song(session)
    await a_song(session, user=BOB)

    standing = await quota.usage(session, ALICE)

    assert standing.songs_this_month == 2
    assert standing.songs_left == quota.SONGS_PER_MONTH - 2


async def test_last_months_songs_do_not_count_against_this_month(session):
    """The limit is per month, so it has to actually reset."""
    await a_song(session, created=quota.start_of_month() - timedelta(days=1))

    assert (await quota.usage(session, ALICE)).songs_this_month == 0


async def test_storage_is_counted_from_the_stems(session):
    """Not by listing the bucket: that is a round trip to Amsterdam for a
    number the database already has."""
    await a_song(session, megabytes=15)
    await a_song(session, megabytes=12)
    await a_song(session, user=BOB, megabytes=100)

    assert (await quota.usage(session, ALICE)).storage_bytes == 27 * MB


async def test_a_song_still_processing_occupies_nothing_yet(session):
    """No stems, no bytes - and the account screen should not pretend
    otherwise while a song is separating."""
    await a_song(session, megabytes=0)

    assert (await quota.usage(session, ALICE)).storage_bytes == 0


async def test_running_jobs_are_counted_per_user(session):
    song = await a_song(session)
    await a_running_job(session, song)
    await a_running_job(session, await a_song(session, user=BOB))

    assert (await quota.usage(session, ALICE)).running_jobs == 1


# --- the refusals -----------------------------------------------------------


async def test_a_user_within_the_limits_may_add_a_song(session):
    await a_song(session)

    assert (await quota.check_can_add(session, ALICE)).songs_this_month == 1


async def test_the_monthly_limit_says_which_limit_and_how_much(session):
    """D-30: what ran out, and how much is left - not "no"."""
    for _ in range(quota.SONGS_PER_MONTH):
        await a_song(session, megabytes=1)

    with pytest.raises(quota.QuotaExceeded) as caught:
        await quota.check_can_add(session, ALICE)

    assert caught.value.code == "monthly_songs_exhausted"
    assert caught.value.used == quota.SONGS_PER_MONTH
    assert caught.value.limit == quota.SONGS_PER_MONTH


async def test_a_full_disk_is_its_own_refusal(session):
    await a_song(session, megabytes=quota.STORAGE_BYTES // MB)

    with pytest.raises(quota.QuotaExceeded) as caught:
        await quota.check_can_add(session, ALICE)

    assert caught.value.code == "storage_full"


async def test_one_song_at_a_time(session):
    """Two separations at once is two GPU calls, and locally two Demucs runs
    fighting over the same CPU."""
    await a_running_job(session, await a_song(session))

    with pytest.raises(quota.QuotaExceeded) as caught:
        await quota.check_can_add(session, ALICE)

    assert caught.value.code == "job_already_running"


async def test_an_upload_ticket_is_not_a_job(session):
    """A ticket is issued a minute before the job it leads to. The song running
    now will often have finished by the time the bytes arrive, so refusing the
    upload for it would be refusing on a fact that has already changed."""
    await a_running_job(session, await a_song(session))

    await quota.check_can_add(session, ALICE, concurrency=False)


async def test_somebody_elses_usage_is_not_yours(session):
    for _ in range(quota.SONGS_PER_MONTH):
        await a_song(session, user=BOB, megabytes=1)

    await quota.check_can_add(session, ALICE)


# --- what to offer when there is no room ------------------------------------


async def test_the_song_nobody_has_played_is_offered_first(session):
    """D-30 asks for the least played by name. Never played is the strongest
    form of it, and the cheapest song for its owner to lose."""
    await a_song(session, title="נוגן היום", played=datetime.now(UTC))
    await a_song(session, title="לא נוגן מעולם", played=None)
    await a_song(session, title="נוגן פעם", played=datetime.now(UTC) - timedelta(days=90))

    offered = await quota.crowding_out(session, ALICE)

    assert offered[0].title == "לא נוגן מעולם"
    assert offered[-1].title == "נוגן היום"


async def test_among_songs_nobody_played_the_oldest_goes_first(session):
    """A song uploaded this morning and not yet sung is not a candidate for
    anything."""
    await a_song(session, title="הבוקר", created=datetime.now(UTC))
    await a_song(session, title="לפני חודש", created=datetime.now(UTC) - timedelta(days=30))

    offered = await quota.crowding_out(session, ALICE)

    assert offered[0].title == "לפני חודש"


async def test_a_candidate_says_what_it_would_free(session):
    """An offer to delete without the size is an offer nobody can weigh."""
    await a_song(session, megabytes=14)

    assert (await quota.crowding_out(session, ALICE))[0].bytes == 14 * MB


async def test_only_your_own_songs_are_offered(session):
    await a_song(session, user=BOB, title="של מישהו אחר")

    assert await quota.crowding_out(session, ALICE) == []
